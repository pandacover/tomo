from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Annotated, Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from .agent import SYSTEM_PROMPT
from .browser_tools import browser
from .file_tools import edit_file, glob, read_file, write_file
from .gateway import extract_tool_errors, get_message_tool_content, get_message_tool_name, get_tool_calls, rectification_prompt, unresolved_failure_reply
from .model import make_model
from .cross_gateway_bridge import make_cross_gateway_tool
from .social_browser import social_browser
from .tools import append_memory, files_search, generate_image, read_memory, terminal, web_fetch, web_search


class TomoGraphState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    task_complexity: NotRequired[Literal["simple", "multi_step"]]
    required_context: NotRequired[set[str]]
    tool_errors: NotRequired[list[str]]
    validation_errors: NotRequired[list[str]]
    repair_attempts: NotRequired[int]
    completion_ready: NotRequired[bool]
    pending_approval_calls: NotRequired[list[dict[str, object]]]
    approved_tool_call_ids: NotRequired[list[str]]
    denied_tool_call_ids: NotRequired[list[str]]
    forced_final: NotRequired[str]


APPROVAL_REQUIRED_TOOLS = frozenset({"terminal", "write_file", "edit_file", "schedule_task"})
SOCIAL_APPROVAL_REQUIRED_ACTIONS = frozenset({"login_start", "connect_chrome", "publish_post", "publish_reply", "logout"})
DISCOURAGED_TOOLS = frozenset({"grep", "ls", "execute"})
MAX_REPAIR_ATTEMPTS = 2
LANGGRAPH_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n\nLangGraph skill use:\n"
    "- Before using the browser tool for screenshots, snapshots, rendered UI validation, local dev server checks, or page text extraction, "
    "read `skills/browser-tool/SKILL.md` with `read_file` when available and follow the snapshot-and-ref workflow.\n"
    "- Before using `social_browser` for logged-in X access, read `skills/social-browser/SKILL.md` with `read_file` when available. "
    "Use `social_browser` rather than the generic browser for logged-in social accounts.\n"
    "- Before writing or rewriting Gen Z, slang-heavy, meme-aware, or youth-audience copy, read "
    "`skills/gen-z-phrasing/COMMON_PHRASES.md` with `read_file` when available and follow its usage guidance.\n"
)


def make_langgraph_agent(*, reasoning_effort: str | None = None, model: object | None = None, tools: Sequence[BaseTool] | None = None):
    graph_tools = list(tools) if tools is not None else default_langgraph_tools()
    tool_node = ToolNode(graph_tools)
    tool_by_name = {tool.name: tool for tool in graph_tools}
    chat_model = model if model is not None else make_model(reasoning_effort=reasoning_effort)
    bound_model = chat_model.bind_tools(graph_tools) if hasattr(chat_model, "bind_tools") else chat_model

    def classify_context(state: TomoGraphState) -> dict[str, object]:
        text = latest_user_text(state)
        required: set[str] = set()
        lowered = text.lower()
        if has_any_word(lowered, ("file", "code", "repo", "git", "test", "package", "path", "symbol", "implementation", "tomo")):
            required.add("local_files")
        if has_any_word(lowered, ("current", "latest", "today", "news", "docs", "documentation", "version", "api", "web", "search")):
            required.add("web")
        if has_any_word(lowered, ("image", "photo", "picture", "illustration", "draw", "render", "generate")):
            required.add("image_generation")
        if has_any_word(lowered, ("remember", "preference", "memory", "previous", "past", "decision", "recurring")):
            required.add("memory")
        if not required:
            required.add("stable_general_knowledge")
        complexity: Literal["simple", "multi_step"] = "multi_step" if is_multi_step_request(lowered) else "simple"
        return {"required_context": required, "task_complexity": complexity, "repair_attempts": state.get("repair_attempts", 0)}

    def prepare_context(state: TomoGraphState) -> dict[str, object] | None:
        required = state.get("required_context", set())
        text = latest_user_text(state)
        messages: list[AnyMessage] = []
        if "local_files" in required:
            messages.extend(invoke_context_tool(tool_by_name, "files_search", {"query": text, "path": ".", "k": 8}))
        if "web" in required:
            messages.extend(invoke_context_tool(tool_by_name, "web_search", {"query": text, "k": 3, "fetch_results": True}))
        if "memory" in required:
            messages.extend(invoke_context_tool(tool_by_name, "read_memory", {"query": text, "k": 8}))
        return {"messages": messages} if messages else None

    def plan_if_needed(state: TomoGraphState) -> dict[str, object] | None:
        if state.get("task_complexity") != "multi_step":
            return None
        return {
            "messages": [
                HumanMessage(
                    content=(
                        "Private orchestration note: this is multi-step work. Keep an internal checklist, "
                        "validate before claiming completion, and do not mark anything complete after failures."
                    ),
                    name="tomo_orchestrator",
                )
            ]
        }

    def model_node(state: TomoGraphState) -> dict[str, object]:
        messages = [SystemMessage(content=LANGGRAPH_SYSTEM_PROMPT), *state.get("messages", [])]
        response = bound_model.invoke(messages)
        if hasattr(response, "model_copy"):
            response = response.model_copy(update={"id": None})
        return {"messages": [response]}

    def route_tool_calls(state: TomoGraphState) -> dict[str, object] | None:
        last = last_message(state)
        calls = get_tool_calls(last) if last is not None else []
        invalid = [call for call in calls if str(call.get("name")) in DISCOURAGED_TOOLS]
        edit_without_read = [call for call in calls if str(call.get("name")) == "edit_file" and not has_prior_tool(state, "read_file")]
        if invalid or edit_without_read:
            lines = []
            for call in invalid:
                name = str(call.get("name"))
                if name in {"grep", "ls"}:
                    lines.append(f"`{name}` is not available. Use `files_search` for text search or `glob` for path discovery.")
                elif name == "execute":
                    lines.append("`execute` is discouraged. Use `terminal` unless terminal is unavailable.")
            if edit_without_read:
                lines.append("Read the target file with `read_file` before calling `edit_file`.")
            return {"messages": [HumanMessage(content="\n".join(lines), name="tomo_orchestrator")]}
        pending = [
            call
            for call in calls
            if tool_call_requires_approval(call) and str(call.get("id")) not in state.get("approved_tool_call_ids", [])
        ]
        if pending:
            return {"pending_approval_calls": pending}
        return None

    def approve_or_reject(state: TomoGraphState) -> dict[str, object]:
        pending = state.get("pending_approval_calls", [])
        if not pending:
            return {}
        action_requests = [
            {
                "name": str(call.get("name") or "tool"),
                "args": call.get("input") or {},
                "description": approval_description(call),
            }
            for call in pending
        ]
        decisions = interrupt({"action_requests": action_requests})
        normalized = decisions.get("decisions", decisions) if isinstance(decisions, Mapping) else decisions
        if isinstance(normalized, Mapping):
            normalized = [normalized]
        approved_ids = list(state.get("approved_tool_call_ids", []))
        denied_ids = list(state.get("denied_tool_call_ids", []))
        denial_messages: list[ToolMessage] = []
        for call, decision in zip(pending, normalized if isinstance(normalized, list) else []):
            call_id = str(call.get("id") or call.get("name") or "tool")
            decision_type = decision.get("type") if isinstance(decision, Mapping) else None
            if decision_type == "approve":
                approved_ids.append(call_id)
                continue
            denied_ids.append(call_id)
            denial_messages.append(
                ToolMessage(
                    content="Error: User denied the tool call.",
                    name=str(call.get("name") or "tool"),
                    tool_call_id=call_id,
                    status="error",
                )
            )
        return {
            "messages": denial_messages,
            "approved_tool_call_ids": approved_ids,
            "denied_tool_call_ids": denied_ids,
            "pending_approval_calls": [],
        }

    def inspect_tool_results(state: TomoGraphState) -> dict[str, object]:
        errors = extract_tool_errors(latest_tool_batch_messages(list(state.get("messages", []))))
        return {"tool_errors": errors}

    def repair_or_continue(state: TomoGraphState) -> dict[str, object] | None:
        errors = state.get("tool_errors", [])
        if not errors:
            return None
        attempts = state.get("repair_attempts", 0)
        if attempts < MAX_REPAIR_ATTEMPTS:
            return {
                "repair_attempts": attempts + 1,
                "messages": [HumanMessage(content=rectification_prompt(errors), name="tomo_orchestrator")],
            }
        return {"forced_final": unresolved_failure_reply(errors), "completion_ready": False}

    def validate_completion(state: TomoGraphState) -> dict[str, object]:
        errors = state.get("tool_errors", [])
        if errors:
            return {"completion_ready": False, "validation_errors": errors}
        missing = missing_validation_errors(state)
        if missing:
            already_requested = any(
                isinstance(message, HumanMessage) and getattr(message, "name", None) == "tomo_validator"
                for message in state.get("messages", [])
            )
            if not already_requested:
                return {
                    "completion_ready": False,
                    "validation_errors": missing,
                    "messages": [HumanMessage(content="Validation required before final success:\n- " + "\n- ".join(missing), name="tomo_validator")],
                }
        return {"completion_ready": True, "validation_errors": []}

    def final_response(state: TomoGraphState) -> dict[str, object] | None:
        forced = state.get("forced_final")
        if forced:
            return {"messages": [AIMessage(content=forced)]}
        return None

    graph = StateGraph(TomoGraphState)
    graph.add_node("classify_context", classify_context)
    graph.add_node("prepare_context", prepare_context)
    graph.add_node("plan_if_needed", plan_if_needed)
    graph.add_node("model", model_node)
    graph.add_node("route_tool_calls", route_tool_calls)
    graph.add_node("approve_or_reject", approve_or_reject)
    graph.add_node("execute_tools", tool_node)
    graph.add_node("inspect_tool_results", inspect_tool_results)
    graph.add_node("repair_or_continue", repair_or_continue)
    graph.add_node("validate_completion", validate_completion)
    graph.add_node("final_response", final_response)

    graph.add_edge(START, "classify_context")
    graph.add_edge("classify_context", "prepare_context")
    graph.add_edge("prepare_context", "plan_if_needed")
    graph.add_edge("plan_if_needed", "model")
    graph.add_conditional_edges("model", after_model, {"route_tool_calls": "route_tool_calls", "validate_completion": "validate_completion"})
    graph.add_conditional_edges("route_tool_calls", after_route_tools, {"approve_or_reject": "approve_or_reject", "execute_tools": "execute_tools", "model": "model"})
    graph.add_conditional_edges("approve_or_reject", after_approval, {"execute_tools": "execute_tools", "model": "model"})
    graph.add_edge("execute_tools", "inspect_tool_results")
    graph.add_edge("inspect_tool_results", "repair_or_continue")
    graph.add_conditional_edges(
        "repair_or_continue",
        after_repair,
        {"model": "model", "validate_completion": "validate_completion", "final_response": "final_response"},
    )
    graph.add_conditional_edges("validate_completion", after_validation, {"model": "model", "final_response": "final_response"})
    graph.add_edge("final_response", END)
    return graph.compile(checkpointer=MemorySaver()).with_config({"recursion_limit": 200})


def default_langgraph_tools() -> list[BaseTool]:
    return [
        files_search,
        read_file,
        glob,
        terminal,
        browser,
        social_browser,
        web_search,
        web_fetch,
        generate_image,
        append_memory,
        read_memory,
        make_cross_gateway_tool(),
        write_file,
        edit_file,
    ]


def tool_call_requires_approval(call: Mapping[str, object]) -> bool:
    name = str(call.get("name") or "")
    if name in APPROVAL_REQUIRED_TOOLS:
        return True
    if name != "social_browser":
        return False
    args = parse_call_input(call.get("input"))
    action = str(args.get("action") or "")
    return action in SOCIAL_APPROVAL_REQUIRED_ACTIONS


def approval_description(call: Mapping[str, object]) -> str:
    name = str(call.get("name") or "tool")
    if name != "social_browser":
        return f"Tool execution requires approval: {name}"
    args = parse_call_input(call.get("input"))
    platform = str(args.get("platform") or "x")
    action = str(args.get("action") or "")
    url = str(args.get("url") or args.get("reply_to_url") or "").strip()
    text = str(args.get("text") or "").strip()
    lines = [
        "Logged-in social account action requires approval.",
        f"Platform: {platform}",
        f"Action: {action}",
        "This uses the logged-in X account in Tomo's managed browser profile.",
    ]
    if url:
        lines.append(f"Target URL: {url}")
    if text:
        lines.append(f"Text: {text}")
    return "\n".join(lines)


def parse_call_input(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def after_model(state: TomoGraphState) -> str:
    last = last_message(state)
    return "route_tool_calls" if last is not None and get_tool_calls(last) else "validate_completion"


def after_route_tools(state: TomoGraphState) -> str:
    last = last_message(state)
    if isinstance(last, HumanMessage) and getattr(last, "name", None) == "tomo_orchestrator":
        return "model"
    if state.get("pending_approval_calls"):
        return "approve_or_reject"
    return "execute_tools"


def after_approval(state: TomoGraphState) -> str:
    return "model" if state.get("denied_tool_call_ids") else "execute_tools"


def after_repair(state: TomoGraphState) -> str:
    if state.get("forced_final"):
        return "final_response"
    last = last_message(state)
    if isinstance(last, HumanMessage) and getattr(last, "name", None) == "tomo_orchestrator":
        return "model"
    if get_message_tool_name(last) is not None:
        return "model"
    return "validate_completion"


def after_validation(state: TomoGraphState) -> str:
    last = last_message(state)
    if isinstance(last, HumanMessage) and getattr(last, "name", None) == "tomo_validator":
        return "model"
    return "final_response"


def latest_user_text(state: TomoGraphState) -> str:
    for message in reversed(state.get("messages", [])):
        role = getattr(message, "type", None)
        if isinstance(message, Mapping):
            role = message.get("role") or message.get("type")
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if role in {"human", "user"} or message.__class__.__name__ == "HumanMessage":
            return str(content or "")
    return ""


def is_multi_step_request(text: str) -> bool:
    return any(word in text for word in ("implement", "fix", "refactor", "add ", "build", "test", "plan", "diagnose")) or text.count("\n") > 2


def has_any_word(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def invoke_context_tool(tool_by_name: Mapping[str, BaseTool], name: str, args: dict[str, object]) -> list[AnyMessage]:
    tool = tool_by_name.get(name)
    if tool is None:
        return []
    call_id = f"context-{name}"
    result = tool.invoke(args)
    return [
        AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}]),
        ToolMessage(content=str(result), name=name, tool_call_id=call_id),
    ]


def last_message(state: TomoGraphState) -> AnyMessage | None:
    messages = state.get("messages", [])
    return messages[-1] if messages else None


def has_prior_tool(state: TomoGraphState, name: str) -> bool:
    return any(get_message_tool_name(message) == name for message in state.get("messages", []))


def latest_tool_batch_messages(messages: list[AnyMessage]) -> list[AnyMessage]:
    latest_model_index: int | None = None
    for index in range(len(messages) - 1, -1, -1):
        if get_tool_calls(messages[index]):
            latest_model_index = index
            break
    if latest_model_index is None:
        return []
    batch: list[AnyMessage] = []
    for message in messages[latest_model_index + 1 :]:
        if get_message_tool_name(message) is not None:
            batch.append(message)
            continue
        if batch:
            break
    return batch


def missing_validation_errors(state: TomoGraphState) -> list[str]:
    messages = list(state.get("messages", []))
    mutating_indexes: list[int] = []
    for index, message in enumerate(messages):
        for call in get_tool_calls(message):
            if str(call.get("name")) in {"write_file", "edit_file", "terminal"}:
                mutating_indexes.append(index)
    if not mutating_indexes:
        return []
    last_mutating = max(mutating_indexes)
    later_tool_names = {get_message_tool_name(message) for message in messages[last_mutating + 1 :] if get_message_tool_name(message)}
    if later_tool_names.intersection({"read_file", "files_search", "glob"}):
        return []
    return ["write/edit/shell work needs read-back, tests, build output, or direct inspection before final success"]
