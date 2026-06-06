from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Literal

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document


Surface = Literal["chat", "gateway"]


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    surfaces: frozenset[Surface]
    telegram_name: str | None = None

    @property
    def token(self) -> str:
        return f"/{self.name}"

    @property
    def telegram_token(self) -> str:
        return self.telegram_name or self.name


CHAT_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("session", "List or switch saved sessions", frozenset({"chat"})),
    SlashCommand("clear", "Clear the current session", frozenset({"chat"})),
    SlashCommand("debug-tool", "Show full tool prompts", frozenset({"chat"})),
    SlashCommand("exit", "Quit Tomo", frozenset({"chat"})),
)

GATEWAY_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("start", "Show Tomo is ready", frozenset({"gateway"})),
    SlashCommand("cancel", "Cancel a pending action", frozenset({"gateway"})),
    SlashCommand("approve", "Approve a pending tool call", frozenset({"gateway"})),
    SlashCommand("deny", "Deny a pending tool call", frozenset({"gateway"})),
    SlashCommand("yolo", "Toggle approval-free tool execution", frozenset({"gateway"})),
    SlashCommand("debug-tool", "Show full tool prompts", frozenset({"gateway"}), telegram_name="debug_tool"),
)

ALL_COMMANDS: tuple[SlashCommand, ...] = CHAT_COMMANDS + GATEWAY_COMMANDS


def commands_for(surface: Surface) -> tuple[SlashCommand, ...]:
    return tuple(command for command in ALL_COMMANDS if surface in command.surfaces)


def command_tokens(surface: Surface) -> tuple[str, ...]:
    return tuple(command.token for command in commands_for(surface))


def status_hint(surface: Surface) -> str:
    tokens = ", ".join(command_tokens(surface))
    if surface == "chat":
        return f"Commands: {tokens} · Scroll chat: PgUp/PgDn or mouse wheel"
    return f"Commands: {tokens}"


def telegram_bot_commands() -> list[dict[str, str]]:
    return [{"command": command.telegram_token, "description": command.description} for command in GATEWAY_COMMANDS]


def slash_prefix(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    token = stripped.split()[0]
    return token if token != "/" else None


def suggest_command(text: str, surface: Surface) -> str | None:
    prefix = slash_prefix(text)
    if prefix is None:
        return None
    candidates = command_tokens(surface)
    if prefix in candidates:
        return None
    matches = get_close_matches(prefix, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


def unrecognized_message(text: str, surface: Surface) -> str:
    suggestion = suggest_command(text, surface)
    if suggestion:
        return f"Unrecognized command. Did you mean {suggestion}?"
    tokens = ", ".join(command_tokens(surface))
    return f"Unrecognized command. Commands: {tokens}"


class SlashCommandCompleter(Completer):
    def __init__(self, surface: Surface) -> None:
        self.surface = surface
        self.commands = commands_for(surface)

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        fragment = text.split()[0]
        if fragment == "/":
            start = -len(fragment)
            for command in self.commands:
                yield Completion(command.token, start_position=start, display_meta=command.description)
            return
        start = -len(fragment)
        for command in self.commands:
            if command.token.startswith(fragment):
                yield Completion(command.token, start_position=start, display_meta=command.description)
