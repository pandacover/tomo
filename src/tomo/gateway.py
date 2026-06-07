from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable, Protocol

from langgraph.types import Command

from .agent import SKILL_SOURCES, extract_text, make_agent
from .reasoning import effective_reasoning_effort
from .session_store import ChatSession, create_session, save_session
from .tools import ApprovalRequest


class Gateway(Protocol):
    def run(self) -> None: ...


class ApprovalResponder(Protocol):
    def request_approval(self, channel_id: str, request: ApprovalRequest) -> bool: ...


@dataclass(frozen=True)
class ToolCallLifecycleEvent:
    name: str
    input: object = None

    def render(self, *, full_input: bool = False) -> str:
        return f'{self.name}: "{format_tool_input(self.input, limit=None if full_input else 50)}"'


@dataclass(frozen=True)
class AgentTrace:
    reasoning_summary: str | None = None
    tool_events: tuple[ToolCallLifecycleEvent, ...] = ()
    tool_errors: tuple[str, ...] = ()

    def render(self, *, include_reasoning: bool = False, full_tool_input: bool = False) -> str:
        lines: list[str] = []
        if include_reasoning and self.reasoning_summary:
            lines.append(f"Reasoning summary: {self.reasoning_summary}")
        lines.extend(event.render(full_input=full_tool_input) for event in self.tool_events)
        lines.extend(f"Tool error: {error}" for error in self.tool_errors)
        return "\n".join(lines)


@dataclass(frozen=True)
class GatewayReply:
    text: str
    trace: AgentTrace


TraceEventHandler = Callable[[ToolCallLifecycleEvent], None]
TextDeltaHandler = Callable[[str], None]
MAX_RECTIFICATION_ATTEMPTS = 2


def format_tool_input(value: object, *, limit: int | None = 50) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        except TypeError:
            text = str(value)
    text = " ".join(text.split())
    if limit is None:
        return text
    return f"{text[:limit]}..." if len(text) > limit else text


@dataclass
class TomoGateway:
    responder: ApprovalResponder
    agent: object = field(default_factory=make_agent)
    sessions: dict[str, ChatSession] = field(default_factory=dict)
    agents_by_effort: dict[str, object] = field(default_factory=dict)
    channel_reasoning_effort: dict[str, str] = field(default_factory=dict)
    channel_trace_override: dict[str, bool | None] = field(default_factory=dict)

    def set_channel_reasoning_effort(self, channel_id: str, effort: str) -> None:
        self.channel_reasoning_effort[channel_id] = effort

    def set_channel_trace_override(self, channel_id: str, enabled: bool | None) -> None:
        self.channel_trace_override[channel_id] = enabled

    def get_agent(self, channel_id: str) -> object:
        if channel_id not in self.channel_reasoning_effort:
            return self.agent
        effort = self.channel_reasoning_effort.get(channel_id, effective_reasoning_effort())
        cached = self.agents_by_effort.get(effort)
        if cached is not None:
            return cached
        built = make_agent(reasoning_effort=effort)
        self.agents_by_effort[effort] = built
        return built

    def get_session(self, channel_id: str) -> ChatSession:
        session = self.sessions.get(channel_id)
        if session is None:
            session = create_session(f"Gateway {channel_id}")
            save_session(session)
            self.sessions[channel_id] = session
        return session

    def send_text(self, channel_id: str, text: str) -> str:
        return self.send_text_with_trace(channel_id, text).text

    def send_text_with_trace(self, channel_id: str, text: str) -> GatewayReply:
        return self.send_text_with_events(channel_id, text)

    def send_text_with_events(
        self,
        channel_id: str,
        text: str,
        on_event: TraceEventHandler | None = None,
        on_text_delta: TextDeltaHandler | None = None,
    ) -> GatewayReply:
        session = self.get_session(channel_id)
        session.messages.append({"role": "user", "content": text})
        save_session(session)

        messages = [dict(message) for message in session.messages]
        messages[-1]["content"] = add_skill_context(text)
        result = self.invoke_agent_with_approvals(
            channel_id,
            session,
            {"messages": messages},
            on_event=on_event,
            on_text_delta=on_text_delta,
        )

        for _ in range(MAX_RECTIFICATION_ATTEMPTS):
            tool_errors = extract_tool_errors(extract_messages(result))
            if not tool_errors:
                break
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": rectification_prompt(tool_errors),
                },
            ]
            result = self.invoke_agent_with_approvals(
                channel_id,
                session,
                {"messages": messages},
                on_event=on_event,
                on_text_delta=on_text_delta,
            )

        if tool_errors := extract_tool_errors(extract_messages(result)):
            result = {
                "messages": [
                    *extract_messages(result),
                    {"role": "assistant", "content": unresolved_failure_reply(tool_errors)},
                ]
            }

        reply = extract_text(result)
        trace = extract_agent_trace(result)

        session.messages.append({"role": "assistant", "content": reply})
        save_session(session)
        return GatewayReply(text=reply, trace=trace)

    def invoke_agent_with_approvals(
        self,
        channel_id: str,
        session: ChatSession,
        payload: object,
        on_event: TraceEventHandler | None = None,
        on_text_delta: TextDeltaHandler | None = None,
    ) -> object:
        config = {"configurable": {"thread_id": session.metadata.id}}
        emitted = ToolEventEmitter(on_event)
        agent = self.get_agent(channel_id)
        result = invoke_agent_streaming(agent, payload, config, emitted, on_text_delta=on_text_delta)

        while interrupts := extract_interrupts(result):
            interrupt_value = interrupts[0].value
            action_requests = interrupt_value.get("action_requests", [])
            if not action_requests:
                break

            request = approval_request_from_actions(action_requests)
            approved = self.responder.request_approval(channel_id, request)
            decisions = [
                {"type": "approve"} if approved else {"type": "reject", "message": "User denied the tool call."}
                for _ in action_requests
            ]
            result = invoke_agent_streaming(
                agent,
                Command(resume={"decisions": decisions}),
                config,
                emitted,
                on_text_delta=on_text_delta,
            )

        return result


def rectification_prompt(tool_errors: list[str]) -> str:
    errors = "\n".join(f"- {error}" for error in tool_errors)
    return (
        "The previous attempt had failed tool output. Do not mark any related todo/task complete yet.\n"
        "Rectify the failure before advancing: inspect the error, retry or choose a valid alternative, "
        "and validate the result before claiming completion.\n\n"
        f"Failed tool output:\n{errors}"
    )


def unresolved_failure_reply(tool_errors: list[str]) -> str:
    errors = "\n".join(f"- {error}" for error in tool_errors)
    return f"I could not complete the task because validation/tool execution is still failing:\n{errors}"


def add_skill_context(text: str) -> str:
    skills = resolve_skill_references(text)
    if not skills:
        return text
    blocks = [f'<skill_context name="{name}" path="{path}">\n{content}\n</skill_context>' for name, path, content in skills]
    return "\n\n".join([*blocks, f"User prompt:\n{text}"])


def resolve_skill_references(text: str) -> list[tuple[str, str, str]]:
    import re

    names = list(dict.fromkeys(re.findall(r"(?<!\S)\$([A-Za-z0-9_-]+)", text)))
    resolved: list[tuple[str, str, str]] = []
    for name in names:
        skill_file = find_skill_file(name)
        if skill_file is None:
            continue
        resolved.append((name, skill_file.as_posix(), skill_file.read_text(encoding="utf-8")))
    return resolved


def find_skill_file(name: str) -> Path | None:
    for source in reversed(SKILL_SOURCES):
        skill_file = Path(source).expanduser() / name / "SKILL.md"
        if skill_file.is_file():
            return skill_file
    return None


def extract_interrupts(result: object) -> list[object]:
    interrupts = getattr(result, "interrupts", None)
    if interrupts:
        return list(interrupts)
    if isinstance(result, dict):
        interrupts = result.get("__interrupt__") or result.get("interrupts")
        if interrupts:
            return list(interrupts if isinstance(interrupts, list | tuple) else [interrupts])
    return []


def approval_request_from_actions(action_requests: list[object]) -> ApprovalRequest:
    lines: list[str] = []
    names: list[str] = []
    for action in action_requests:
        if isinstance(action, dict):
            name = str(action.get("name", "tool"))
            args = action.get("args", {})
            description = action.get("description")
        else:
            name = str(getattr(action, "name", "tool"))
            args = getattr(action, "args", {})
            description = getattr(action, "description", None)
        names.append(name)
        lines.append(str(description or f"Tool: {name}\nArgs: {args}"))
    return ApprovalRequest(operation=", ".join(names), target="tool call", reason="\n\n".join(lines))


class ToolEventEmitter:
    def __init__(self, on_event: TraceEventHandler | None) -> None:
        self.on_event = on_event
        self.started: set[str] = set()
        self.finished: set[str] = set()
        self.derived_events: list[ToolCallLifecycleEvent] = []

    def emit_from_result(self, result: object) -> None:
        for event in extract_tool_lifecycle_events(extract_messages(result), started=self.started, finished=self.finished):
            self.emit(event)

    def emit(self, event: ToolCallLifecycleEvent) -> None:
        self.derived_events.append(event)
        if self.on_event is not None:
            self.on_event(event)


def invoke_agent_streaming(
    agent: object,
    payload: object,
    config: object,
    emitter: ToolEventEmitter,
    on_text_delta: TextDeltaHandler | None = None,
) -> object:
    stream = getattr(agent, "stream", None)
    if callable(stream):
        result = consume_langchain_stream(
            stream(payload, config=config, stream_mode=["messages", "updates"], version="v2"),
            emitter,
            on_text_delta,
        )
        if result is not None:
            return result

    stream_events = getattr(agent, "stream_events", None)
    if callable(stream_events):
        stream = stream_events(payload, config=config, version="v3")
        raw_result = consume_raw_event_stream(stream, emitter, on_text_delta)
        if raw_result is not None:
            return raw_result
        if hasattr(stream, "tool_calls") and hasattr(stream, "output"):
            for call in stream.tool_calls:
                name = str(getattr(call, "tool_name", None) or getattr(call, "name", None) or "tool")
                emitter.emit(ToolCallLifecycleEvent(name=name, input=getattr(call, "input", None) or getattr(call, "args", None)))
                error = getattr(call, "error", None)
                output_deltas = getattr(call, "output_deltas", None)
                try:
                    if error is None and output_deltas is not None:
                        for _ in output_deltas:
                            pass
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"{name} failed while streaming output: {exc}") from exc
                if error is not None:
                    raise RuntimeError(f"{name} failed: {error}")
            return stream.output

    if not callable(stream):
        result = agent.invoke(payload, config=config, version="v2")
        emitter.emit_from_result(result)
        return result

    last: object | None = None
    for chunk in stream(payload, config=config, stream_mode="values", version="v2"):
        last = chunk
        emitter.emit_from_result(chunk)
    if last is not None:
        return last

    result = agent.invoke(payload, config=config, version="v2")
    emitter.emit_from_result(result)
    return result


def consume_langchain_stream(
    stream: object,
    emitter: ToolEventEmitter,
    on_text_delta: TextDeltaHandler | None = None,
) -> object | None:
    try:
        iterator = iter(stream)
    except TypeError:
        return None

    text_parts: list[str] = []
    accumulated_updates: dict[str, object] = {}
    saw_chunk = False
    saw_recognized_chunk = False
    for chunk in iterator:
        if not isinstance(chunk, Mapping):
            continue
        saw_chunk = True
        chunk_type = chunk.get("type")
        if chunk_type == "messages":
            saw_recognized_chunk = True
            delta = extract_langchain_message_delta(chunk)
            if delta:
                text_parts.append(delta)
                if on_text_delta is not None:
                    on_text_delta(delta)
            continue
        if chunk_type == "updates":
            saw_recognized_chunk = True
            data = chunk.get("data")
            if isinstance(data, Mapping):
                accumulated_updates.update(data)
                consume_update_tool_events(data, emitter)

    state = flatten_update_state(accumulated_updates)
    if text_parts:
        state.setdefault("messages", [])
        streamed_text = "".join(text_parts)
        if isinstance(state["messages"], list) and not has_assistant_message_content(state["messages"], streamed_text):
            state["messages"].append({"role": "assistant", "content": streamed_text})
        return state
    if accumulated_updates:
        return state
    return {} if saw_chunk and saw_recognized_chunk else None


def extract_langchain_message_delta(chunk: Mapping[str, object]) -> str | None:
    data = chunk.get("data")
    if not isinstance(data, tuple) or len(data) != 2:
        return None
    token, metadata = data
    if isinstance(metadata, Mapping) and metadata.get("langgraph_node") == "tools":
        return None
    if is_full_langchain_message(token):
        return None
    blocks = getattr(token, "content_blocks", None)
    if isinstance(blocks, list):
        text = "".join(str(block.get("text", "")) for block in blocks if isinstance(block, Mapping) and block.get("type") == "text")
        return text or None
    text = getattr(token, "text", None)
    if isinstance(text, str) and text:
        return text
    content = getattr(token, "content", None)
    return content if isinstance(content, str) and content else None


def is_full_langchain_message(token: object) -> bool:
    token_type = str(getattr(token, "type", ""))
    class_name = token.__class__.__name__
    if class_name.endswith("Chunk"):
        return False
    return token_type in {"ai", "assistant", "human", "user", "system", "tool"} or class_name in {
        "AIMessage",
        "HumanMessage",
        "SystemMessage",
        "ToolMessage",
    }


def has_assistant_message_content(messages: list[object], content: str) -> bool:
    for message in messages:
        role = message.get("role") if isinstance(message, Mapping) else getattr(message, "role", None)
        message_type = message.get("type") if isinstance(message, Mapping) else getattr(message, "type", None)
        if role not in {"assistant", "ai", None} and message_type not in {"ai", "assistant", None}:
            continue
        if get_message_tool_name(message) is not None:
            continue
        message_content = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", None)
        if message_content == content:
            return True
    return False


def consume_update_tool_events(data: Mapping[str, object], emitter: ToolEventEmitter) -> None:
    for node, update in data.items():
        if not isinstance(update, Mapping):
            continue
        messages = update.get("messages")
        if not isinstance(messages, list):
            continue
        if node == "model":
            for message in messages:
                for call in get_tool_calls(message):
                    call_id = str(call.get("id") or call.get("tool_call_id") or call.get("name") or "tool")
                    name = str(call.get("name") or "tool")
                    if call_id not in emitter.started:
                        emitter.started.add(call_id)
                        emitter.emit(ToolCallLifecycleEvent(name=name, input=call.get("input")))
        if node == "tools":
            for message in messages:
                name = get_message_tool_name(message)
                if name is None:
                    continue
                call_id = get_message_tool_call_id(message) or name
                emitter.finished.add(call_id)


def flatten_update_state(data: Mapping[str, object]) -> dict[str, object]:
    messages: list[object] = []
    interrupts: list[object] = []
    for key in ("__interrupt__", "interrupts"):
        value = data.get(key)
        if value:
            interrupts.extend(value if isinstance(value, list | tuple) else [value])
    for update in data.values():
        if isinstance(update, Mapping) and isinstance(update.get("messages"), list):
            messages.extend(update["messages"])
            continue
        if isinstance(update, Mapping):
            for key in ("__interrupt__", "interrupts"):
                value = update.get(key)
                if value:
                    interrupts.extend(value if isinstance(value, list | tuple) else [value])
    state: dict[str, object] = {}
    if messages:
        state["messages"] = messages
    if interrupts:
        state["__interrupt__"] = interrupts
    return state


def consume_raw_event_stream(
    stream: object,
    emitter: ToolEventEmitter,
    on_text_delta: TextDeltaHandler | None = None,
) -> object | None:
    try:
        iterator = iter(stream)
    except TypeError:
        return None

    text_parts: list[str] = []
    saw_event = False
    for event in iterator:
        if not isinstance(event, Mapping):
            continue
        saw_event = True
        consume_raw_tool_event(event, emitter)
        text_delta = extract_raw_text_delta(event)
        if text_delta:
            text_parts.append(text_delta)
            if on_text_delta is not None:
                on_text_delta(text_delta)

    if text_parts:
        return {"messages": [{"role": "assistant", "content": "".join(text_parts)}]}
    output = getattr(stream, "output", None)
    if output is not None:
        return output
    return {} if saw_event else None


def consume_raw_tool_event(event: Mapping[str, object], emitter: ToolEventEmitter) -> None:
    method = event.get("method")
    if method != "tool_calls":
        return
    for item in event_payload_items(event):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("tool_name") or item.get("name") or "tool")
        status = str(item.get("status") or item.get("event") or "")
        completed = bool(item.get("completed"))
        error = item.get("error")
        call_id = str(item.get("id") or item.get("tool_call_id") or name)
        if call_id not in emitter.started:
            emitter.started.add(call_id)
            emitter.emit(ToolCallLifecycleEvent(name=name, input=item.get("input") or item.get("args")))
        if completed or error or status in {"completed", "failed", "error"}:
            emitter.finished.add(call_id)


def extract_raw_text_delta(event: Mapping[str, object]) -> str | None:
    if event.get("method") != "messages":
        return None
    for item in event_payload_items(event):
        if not isinstance(item, Mapping):
            continue
        if item.get("event") != "content-block-delta":
            continue
        delta = item.get("delta")
        if not isinstance(delta, Mapping) or delta.get("type") != "text-delta":
            continue
        text = delta.get("text")
        if isinstance(text, str):
            return text
    return None


def event_payload_items(event: Mapping[str, object]) -> list[object]:
    params = event.get("params")
    if not isinstance(params, Mapping):
        return []
    data = params.get("data")
    if isinstance(data, list):
        return data
    if data is not None:
        return [data]
    return []


def extract_agent_trace(result: object) -> AgentTrace:
    messages = extract_messages(result)
    return AgentTrace(
        reasoning_summary=extract_reasoning_summary(messages),
        tool_events=tuple(extract_tool_lifecycle_events(messages)),
        tool_errors=tuple(extract_tool_errors(messages)),
    )


def extract_messages(result: object) -> list[object]:
    if hasattr(result, "value"):
        result = result.value
    if isinstance(result, Mapping):
        messages = result.get("messages")
        if isinstance(messages, list):
            return messages
    messages = getattr(result, "messages", None)
    if isinstance(messages, list):
        return messages
    return []


def extract_reasoning_summary(messages: list[object]) -> str | None:
    for message in reversed(messages):
        for candidate in reasoning_candidates(message):
            text = normalize_trace_text(candidate)
            if text:
                return text
    return None


def reasoning_candidates(message: object) -> list[object]:
    candidates: list[object] = []
    content = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", None)
    candidates.extend(reasoning_from_content(content))
    for attr in ("additional_kwargs", "response_metadata"):
        metadata = message.get(attr) if isinstance(message, Mapping) else getattr(message, attr, None)
        if isinstance(metadata, Mapping):
            for key in ("reasoning_summary", "reasoning", "reasoning_content", "summary"):
                if key in metadata:
                    candidates.append(metadata[key])
            candidates.extend(reasoning_from_content(metadata.get("content")))
    return candidates


def reasoning_from_content(content: object) -> list[object]:
    if not isinstance(content, list):
        return []
    values: list[object] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get("type", ""))
        if "reasoning" not in item_type and item_type not in {"summary", "reasoning_summary"}:
            continue
        for key in ("summary", "text", "content"):
            if key in item:
                values.append(item[key])
    return values


def normalize_trace_text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [normalize_trace_text(item) for item in value]
        text = "\n".join(part for part in parts if part)
        return text or None
    if isinstance(value, Mapping):
        for key in ("summary", "text", "content"):
            text = normalize_trace_text(value.get(key))
            if text:
                return text
    return None


def extract_tool_errors(messages: list[object]) -> list[str]:
    errors: list[str] = []
    for message in messages:
        if get_message_tool_name(message) is None:
            continue
        status = get_message_tool_status(message)
        content = get_message_tool_content(message)
        if is_failed_tool_result(status, content):
            name = get_message_tool_name(message) or "tool"
            text = format_tool_input(content, limit=200)
            errors.append(f"{name}: {text}" if text else name)
    return errors


def is_failed_tool_result(status: str, content: object) -> bool:
    if status in {"error", "failed", "failure"}:
        return True
    if not isinstance(content, str):
        return False
    if content.lower().startswith("error:"):
        return True
    exit_match = re.search(r"(?im)^Exit code:\s*(-?\d+)\s*$", content)
    return bool(exit_match and int(exit_match.group(1)) != 0)


def extract_tool_lifecycle_events(
    messages: list[object],
    *,
    started: set[str] | None = None,
    finished: set[str] | None = None,
) -> list[ToolCallLifecycleEvent]:
    started = started if started is not None else set()
    finished = finished if finished is not None else set()
    calls: set[str] = set()
    events: list[ToolCallLifecycleEvent] = []
    anonymous_index = 0
    for message in messages:
        for call in get_tool_calls(message):
            call_id = str(call.get("id") or call.get("tool_call_id") or f"tool-{anonymous_index}")
            anonymous_index += 1
            name = str(call.get("name") or "tool")
            calls.add(call_id)
            if call_id not in started:
                started.add(call_id)
                events.append(ToolCallLifecycleEvent(name=name, input=call.get("input")))

        tool_name = get_message_tool_name(message)
        if tool_name is None:
            continue
        tool_call_id = get_message_tool_call_id(message)
        if tool_call_id and tool_call_id in calls:
            finished.add(tool_call_id)
        else:
            generated_id = f"tool-message-{anonymous_index}"
            if generated_id not in started:
                started.add(generated_id)
                events.append(ToolCallLifecycleEvent(name=tool_name, input=get_message_tool_content(message)))
            finished.add(generated_id)
            anonymous_index += 1
    return events


def get_tool_calls(message: object) -> list[dict[str, object]]:
    raw_calls = message.get("tool_calls") if isinstance(message, Mapping) else getattr(message, "tool_calls", None)
    if not raw_calls:
        additional = message.get("additional_kwargs") if isinstance(message, Mapping) else getattr(message, "additional_kwargs", None)
        if isinstance(additional, Mapping):
            raw_calls = additional.get("tool_calls")
    calls: list[dict[str, object]] = []
    for call in raw_calls or []:
        if isinstance(call, Mapping):
            function = call.get("function")
            name = call.get("name")
            if isinstance(function, Mapping):
                name = name or function.get("name")
                input_value = function.get("arguments")
            else:
                input_value = call.get("args") or call.get("input")
            calls.append({"id": call.get("id"), "name": name or "tool", "input": input_value})
        else:
            calls.append(
                {
                    "id": getattr(call, "id", None),
                    "name": getattr(call, "name", None) or "tool",
                    "input": getattr(call, "args", None) or getattr(call, "input", None),
                }
            )
    return calls


def get_message_tool_name(message: object) -> str | None:
    message_type = message.get("type") if isinstance(message, Mapping) else getattr(message, "type", None)
    if message_type != "tool" and message.__class__.__name__ != "ToolMessage":
        return None
    name = message.get("name") if isinstance(message, Mapping) else getattr(message, "name", None)
    return str(name or "tool")


def get_message_tool_call_id(message: object) -> str | None:
    value = message.get("tool_call_id") if isinstance(message, Mapping) else getattr(message, "tool_call_id", None)
    return str(value) if value else None


def get_message_tool_status(message: object) -> str:
    status = message.get("status") if isinstance(message, Mapping) else getattr(message, "status", None)
    if status:
        return str(status)
    content = get_message_tool_content(message)
    return "error" if is_failed_tool_result("", content) else "success"


def get_message_tool_content(message: object) -> object:
    return message.get("content") if isinstance(message, Mapping) else getattr(message, "content", None)
