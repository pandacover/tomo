from __future__ import annotations

from tomo.slash_commands import (
    SlashCommandCompleter,
    suggest_command,
    status_hint,
    telegram_bot_commands,
    unrecognized_message,
)
from unittest.mock import Mock

from tomo.session_store import create_session
from tomo.telegram import TelegramGateway
from tomo.tui import PromptChat
from prompt_toolkit.document import Document


def make_chat() -> PromptChat:
    return PromptChat(session=create_session(), agent=Mock(), app=None)


def test_suggest_command_finds_close_match_for_chat():
    assert suggest_command("/exi", "chat") == "/exit"
    assert suggest_command("/sesion", "chat") == "/session"


def test_suggest_command_finds_close_match_for_gateway():
    assert suggest_command("/aprove", "gateway") == "/approve"


def test_suggest_command_ignores_exact_matches_and_non_slash():
    assert suggest_command("/exit", "chat") is None
    assert suggest_command("hello", "chat") is None


def test_unrecognized_message_includes_suggestion():
    assert unrecognized_message("/exi", "chat") == "Unrecognized command. Did you mean /exit?"


def test_status_hint_lists_surface_commands():
    assert "/session" in status_hint("chat")
    assert "/debug-tool" in status_hint("chat")
    assert "/approve" in status_hint("gateway")
    assert "/yolo" in status_hint("gateway")
    assert "/debug-tool" in status_hint("gateway")


def test_telegram_bot_commands_match_gateway_surface():
    names = {item["command"] for item in telegram_bot_commands()}
    assert names == {"start", "cancel", "approve", "deny", "yolo", "debug_tool"}


def test_slash_command_completer_offers_chat_commands():
    completer = SlashCommandCompleter("chat")
    document = Document("/se", 3)
    completions = list(completer.get_completions(document, None))

    assert [completion.text for completion in completions] == ["/session"]


def test_prompt_chat_unknown_slash_does_not_hit_agent():
    chat = make_chat()

    handled = chat.handle_command("/exi")

    assert handled is True
    assert "Did you mean /exit?" in chat.output.text


class RecordingTelegram(TelegramGateway):
    def __init__(self) -> None:
        super().__init__("token", tomo=Mock())
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.sent: list[tuple[str, str]] = []

    def api(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, payload))
        return {"ok": True, "result": True}

    def send_message(self, chat_id: str, text: str) -> list[int]:
        self.sent.append((chat_id, text))
        return [1]


def test_telegram_gateway_registers_bot_commands():
    gateway = RecordingTelegram()

    gateway.register_bot_commands()

    assert gateway.calls[0] == ("setMyCommands", {"commands": telegram_bot_commands()})


def test_telegram_gateway_suggests_unknown_slash_command():
    gateway = RecordingTelegram()

    gateway.handle_text("123", "/aprove")

    assert gateway.sent == [("123", "Unrecognized command. Did you mean /approve?")]
