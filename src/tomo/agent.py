from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from deepagents import FilesystemPermission, HarnessProfile, create_deep_agent, register_harness_profile
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import TodoListMiddleware
from langgraph.checkpoint.memory import MemorySaver

from .model import make_model
from .tools import append_memory, files_search, read_memory, terminal, web_fetch, web_search


SKILLS_LOGGER = logging.getLogger("deepagents.middleware.skills")
SKILLS_LOGGER.setLevel(logging.ERROR)


SYSTEM_PROMPT = """You are Tomo, a concise and helpful chat assistant.

Tool surface:
- read_file: read a known project file.
- glob: discover project files by pattern.
- terminal: run shell commands from the workspace.
- files_search: BM25-ranked local project text search.
- web_search: public web search with optional fetched excerpts.
- web_fetch: fetch and clean text from a known public URL.
- append_memory: store durable observations in MEMORY.md.
- read_memory: retrieve relevant memories from MEMORY.md.

Routing policy:
NEVER answer these from memory or mental computation; always use a tool first:
- File contents, exact code, project structure, file existence, file paths, line counts, sizes, git state, package metadata, test results, command output, or local configuration.
- Current or external facts, including news, weather, prices, schedules, versions, API docs, laws, regulations, public company/person facts, and anything likely to have changed.
- Prior user preferences, project decisions, durable context, or remembered facts that may affect the answer.

Tool routing:
- Use read_memory first when past context, preferences, project decisions, or user-specific facts may matter.
- Use files_search when you need to find where something is defined, mentioned, tested, or configured in the workspace.
- Use read_file after files_search/glob when you know the file path and need exact file contents.
- Use glob when you know a filename pattern and need matching paths.
- Use terminal for commands, tests, git/status checks, package manager queries, file metadata, counts, or any answer that depends on exact command output.
- Use web_search for current or external information when you do not already have a specific URL.
- Use web_fetch when you already have a URL or need to read a specific public source.
- Use append_memory when you learn a reusable user preference, project fact, decision, workaround, or detail that should persist.
- If the answer can be given from stable general knowledge and none of the mandatory-tool cases apply, answer directly.

Use memory proactively. Call read_memory when past context, preferences, decisions, or project facts might help. Call append_memory whenever you learn a reusable fact, user preference, decision, workaround, or project detail that may be useful later; do not wait for the user to ask.

Filesystem access outside the current project directory requires human approval through the UI unless the current surface has enabled approval-free mode.
Dotfiles and dot-directories are denied by policy.
Writes, edits, and shell commands require human approval through the UI unless the current surface has enabled approval-free mode.
If approval is denied, explain what was denied and continue without retrying the same call.
Prefer minimal, precise edits. Ask before destructive, broad, or ambiguous changes.

Task completion policy:
- Do not mark a todo/task complete after a failed tool call, failed command, rejected approval, or validation error.
- If any step fails, keep the task in progress, explain the failure, and either fix it or ask for the missing approval/input.
- Validate changes with an appropriate read, command, test, or inspection before claiming the task is complete.
"""


DEFAULT_INTERRUPT_ON = {
    "read_file": {"allowed_decisions": ["approve", "reject"]},
    "glob": {"allowed_decisions": ["approve", "reject"]},
    "terminal": {"allowed_decisions": ["approve", "reject"]},
    "write_file": {"allowed_decisions": ["approve", "reject"]},
    "edit_file": {"allowed_decisions": ["approve", "reject"]},
}


EXCLUDED_BUILTIN_TOOLS = frozenset({"grep", "ls"})
TOOL_DESCRIPTION_OVERRIDES = {
    "read_file": "Read exact contents from a known workspace file path. Use after files_search or glob when the file path is known and the answer depends on file text.",
    "glob": "Return workspace file paths matching a glob pattern. Use to discover files by name, extension, or directory pattern before reading them.",
}


TOMO_WRITE_TODOS_SYSTEM_PROMPT = """## `write_todos`

You have access to the `write_todos` tool to manage a private planning checklist for complex objectives.
Use it to break work into clear steps and track progress. It does not edit project files and it does not deliver the final answer.

Task state rules:
- pending: not started.
- in_progress: actively being worked.
- completed: fully finished and validated.

Only mark a todo completed after the work for that item has succeeded and been validated with appropriate evidence, such as a passing command, test, read-back, or direct inspection.
If a command/tool fails, approval is denied, validation fails, or the result is uncertain, keep the related todo in_progress and add/follow a repair or validation todo.
Do not mark implementation work completed merely because you attempted an edit.
Do not mark verification completed unless verification actually passed.
When all work is done, write the final answer in a normal assistant message after the last `write_todos` call.
"""


TOMO_WRITE_TODOS_TOOL_DESCRIPTION = """Create or update the private planning checklist for this work session.

This tool only manages task state; it does not read, write, or edit project files.

Use it for complex multi-step work. Avoid it for simple tasks.

Status meanings:
- pending: not started.
- in_progress: actively being worked.
- completed: successfully finished and validated.

Completion gate:
- Mark a todo completed only after the step has actually succeeded and, for code/file changes, after suitable validation passed.
- If any tool call, command, approval, edit, or validation fails, keep the related todo in_progress and continue with a repair/validation task.
- Never mark a todo completed for partial work, failed work, unverified changes, or work that merely produced an answer.
"""


class TomoTodoListMiddleware(TodoListMiddleware):
    def __init__(self) -> None:
        super().__init__(
            system_prompt=TOMO_WRITE_TODOS_SYSTEM_PROMPT,
            tool_description=TOMO_WRITE_TODOS_TOOL_DESCRIPTION,
        )


register_harness_profile(
    "xai",
    HarnessProfile(
        excluded_tools=EXCLUDED_BUILTIN_TOOLS,
        excluded_middleware=frozenset({TodoListMiddleware}),
        extra_middleware=(TomoTodoListMiddleware(),),
        tool_description_overrides=TOOL_DESCRIPTION_OVERRIDES,
    ),
)


SKILL_SOURCES = [
    str(Path.home() / ".deepagents" / "tomo" / "skills"),
    str(Path.home() / ".agents" / "skills"),
    str(Path.cwd() / ".deepagents" / "skills"),
    str(Path.cwd() / ".agents" / "skills"),
]


def workspace_permissions() -> list[FilesystemPermission]:
    workspace = Path.cwd().resolve().as_posix()
    skill_paths = [Path(path).resolve().as_posix() for path in SKILL_SOURCES]
    return [
        FilesystemPermission(
            operations=["read", "write"],
            paths=[*(f"{path}/**" for path in skill_paths), *skill_paths],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/**/.*/**", "/**/.*"],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=[f"{workspace}/**", workspace],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/**"],
            mode="allow",
        ),
    ]


def make_agent():
    return create_deep_agent(
        model=make_model(),
        tools=[files_search, terminal, web_search, web_fetch, append_memory, read_memory],
        system_prompt=SYSTEM_PROMPT,
        skills=SKILL_SOURCES,
        permissions=workspace_permissions(),
        backend=FilesystemBackend(root_dir=".", virtual_mode=False),
        interrupt_on=DEFAULT_INTERRUPT_ON,
        checkpointer=MemorySaver(),
    )


def extract_text(result: object) -> str:
    # Handle case where result has .value (common after Command resume in DeepAgents)
    if hasattr(result, "value"):
        result = result.value

    if isinstance(result, Mapping):
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            for message in reversed(messages):
                role = message.get("role") if isinstance(message, Mapping) else getattr(message, "role", None)
                message_type = message.get("type") if isinstance(message, Mapping) else getattr(message, "type", None)
                if role not in {None, "assistant", "ai"} and message_type not in {None, "ai", "assistant"}:
                    continue
                content = getattr(message, "content", None)
                if isinstance(message, Mapping):
                    content = message.get("content", content)
                if isinstance(content, str) and content:
                    return content
        if not messages:
            return ""

    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content

    return str(result)
