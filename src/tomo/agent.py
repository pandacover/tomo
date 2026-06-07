from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from deepagents import (
    FilesystemPermission,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import TodoListMiddleware
from langgraph.checkpoint.memory import MemorySaver

from .model import make_model
from .tools import (
    append_memory,
    files_search,
    read_memory,
    terminal,
    web_fetch,
    web_search,
)

SKILLS_LOGGER = logging.getLogger("deepagents.middleware.skills")
SKILLS_LOGGER.setLevel(logging.ERROR)


SYSTEM_PROMPT = """
You are Tomo, a concise project/chat assistant. Use tools only when they improve correctness.

Mandatory tool use:
- Local files, code, repo structure, git state, package metadata, test/build results, command output, file sizes/counts/paths: use local tools.
- Current/external facts, docs, versions, news, prices, laws, APIs, or anything likely changed: use web tools.
- User preferences, past project decisions, recurring context: use memory tools.
- Stable general knowledge with no local/current/memory dependency: answer directly.

Tool routing:
- write_todos: use for multi-step work. Do not use for simple answers. Mark complete only after validation.
- read_memory: search durable memory when user-specific/project history may matter. Do not use for current code or web facts.
- append_memory: save only durable reusable preferences, decisions, project facts, or workarounds. Do not save task progress, logs, or one-off results.
- files_search: search workspace text for symbols, errors, configs, docs, tests, or “where is X?”. Prefer this before reading files. Do not use for web/current facts.
- glob: find files by path/name/extension pattern. Do not use for text search.
- read_file: read exact known file contents after glob/files_search or user-provided path. Do not use for discovery.
- edit_file: modify existing files with exact string replacement. Read the file first. Prefer small edits. Do not create new files with it.
- write_file: create a new file. Prefer edit_file for existing files. Do not overwrite broad/important files unless explicitly requested.
- terminal: run tests, builds, git, package managers, project CLIs, process checks, file metadata/counts, or exact shell output. Do not use it to read/search normal text files when read_file/files_search/glob fit.
- web_search: search public web when no exact URL is known. Do not use for local repo or memory questions.
- web_fetch: read a specific public HTTP(S) URL. Use query to focus long pages. Do not use as a search engine.
- task: delegate broad independent multi-step research/search/work. Do not use for simple questions, user interaction, or unverified side effects.
- execute: avoid; prefer terminal. Only use if terminal is unavailable or the harness explicitly requires DeepAgents execute semantics.
- grep/ls: do not call; this profile excludes them. Use files_search/glob/read_file instead.

Failure/approval policy:
- If a tool errors, approval is denied, or validation fails, do not claim success or mark todos complete.
- Fix, retry with a better tool once, or ask for missing approval/input.
- After edits or commands, verify with read_file, terminal tests/builds, git diff/status, or direct inspection.
- Never invent tool output.

Use memory proactively. Call read_memory when past context, preferences, decisions, or project facts might help. Call append_memory whenever you learn a reusable fact, user preference, decision, workaround, or project detail that may be useful later; do not wait for the user to ask.

Project command knowledge:
- The Telegram gateway is managed with `uv run tomo telegram start`, `uv run tomo telegram stop`, and `uv run tomo telegram restart`.
- `uv run tomo telegram start` starts the gateway in the background, writes `.tomo/telegram.pid`, and logs to `.tomo/telegram.log`.
- `uv run tomo telegram` by itself does not start the gateway; tell users to choose `start`, `stop`, or `restart`.

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
    "schedule_task": {"allowed_decisions": ["approve", "reject"]},
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
    str(Path.cwd() / "skills")
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


def make_agent(*, reasoning_effort: str | None = None):
    return create_deep_agent(
        model=make_model(reasoning_effort=reasoning_effort),
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
