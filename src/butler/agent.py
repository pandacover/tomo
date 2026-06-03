from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

from .model import make_model
from .tools import append_memory, bash, edit, read, read_memory, search, web_fetch, web_search, write


SKILLS_LOGGER = logging.getLogger("deepagents.middleware.skills")
SKILLS_LOGGER.setLevel(logging.ERROR)


SYSTEM_PROMPT = """You are Butler, a concise and helpful chat assistant.

You have DeepAgents filesystem tools, a shell tool, and memory tools:
- ls, read_file, glob, grep for reading/searching
- write_file, edit_file for changes
- bash for shell commands
- web_search and web_fetch for public web research
- append_memory and read_memory for long-term observations

Use memory proactively. Call read_memory when past context, preferences, decisions, or project facts might help. Call append_memory whenever you learn a reusable fact, user preference, decision, workaround, or project detail that may be useful later; do not wait for the user to ask.

Filesystem access is scoped to the current project directory. Dotfiles and dot-directories are denied by policy.
Writes, edits, and shell commands require human approval through the UI.
If approval is denied, explain what was denied and continue without retrying the same call.
Prefer minimal, precise edits. Ask before destructive, broad, or ambiguous changes.
"""


DEFAULT_INTERRUPT_ON = {
    "write_file": {"allowed_decisions": ["approve", "reject"]},
    "edit_file": {"allowed_decisions": ["approve", "reject"]},
    "write": {"allowed_decisions": ["approve", "reject"]},
    "edit": {"allowed_decisions": ["approve", "reject"]},
    "bash": {"allowed_decisions": ["approve", "reject"]},
}


SKILL_SOURCES = [
    str(Path.home() / ".deepagents" / "butler" / "skills"),
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
            paths=[f"{workspace}/**/.*/**", f"{workspace}/**/.*"],
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
            mode="deny",
        ),
    ]


def make_agent():
    return create_deep_agent(
        model=make_model(),
        tools=[read, write, edit, search, bash, web_search, web_fetch, append_memory, read_memory],
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

    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content

    return str(result)
