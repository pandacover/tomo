from __future__ import annotations

from unittest.mock import Mock, patch

from tomo.reasoning import parse_reasoning_command_args
from tomo.session_store import create_session
from tomo.tui import PromptChat
from tomo.telegram import TelegramGateway


def test_parse_reasoning_command_args():
    assert parse_reasoning_command_args(None) == ("status", None)
    assert parse_reasoning_command_args("high") == ("effort", "high")
    assert parse_reasoning_command_args("trace on") == ("trace", "on")
    assert parse_reasoning_command_args("trace off") == ("trace", "off")
    assert parse_reasoning_command_args("bogus") == ("invalid", None)


def test_prompt_chat_reasoning_high_rebuilds_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = PromptChat(session=create_session(), agent=Mock(), app=None)
    old_agent = chat.agent

    with patch("tomo.tui.make_agent", return_value=Mock(name="new-agent")) as make_agent_mock:
        assert chat.handle_command("/reasoning high") is True

    make_agent_mock.assert_called_once_with(reasoning_effort="high")
    assert chat.reasoning_effort == "high"
    assert chat.agent is not old_agent
    assert "Reasoning effort set to high" in chat.output.text


def test_prompt_chat_reasoning_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = PromptChat(session=create_session(), agent=Mock(), app=None)
    chat.reasoning_effort = "low"

    assert chat.handle_command("/reasoning") is True

    assert "Reasoning effort: low" in chat.output.text


def test_telegram_reasoning_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RecordingTelegram(TelegramGateway):
        def __init__(self) -> None:
            super().__init__("token", tomo=Mock())
            self.sent: list[tuple[str, str]] = []

        def send_message(self, chat_id: str, text: str) -> list[int]:
            self.sent.append((chat_id, text))
            return [1]

    gateway = RecordingTelegram()
    gateway.tomo.channel_reasoning_effort = {}
    gateway.tomo.channel_trace_override = {}
    gateway.tomo.set_channel_reasoning_effort = Mock()
    gateway.tomo.set_channel_trace_override = Mock()

    gateway.handle_reasoning_command("99", "/reasoning medium")

    gateway.tomo.set_channel_reasoning_effort.assert_called_once_with("99", "medium")
    assert gateway.sent[0][1].startswith("Reasoning effort set to medium")


def test_telegram_reasoning_command_routes_from_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RecordingTelegram(TelegramGateway):
        def __init__(self) -> None:
            super().__init__("token", tomo=Mock())
            self.sent: list[tuple[str, str]] = []

        def send_message(self, chat_id: str, text: str) -> list[int]:
            self.sent.append((chat_id, text))
            return [1]

    gateway = RecordingTelegram()
    gateway.tomo.channel_reasoning_effort = {}
    gateway.tomo.channel_trace_override = {}
    gateway.tomo.set_channel_reasoning_effort = Mock()
    gateway.tomo.set_channel_trace_override = Mock()

    gateway.handle_text("99", "/reasoning@tomo_bot high")
    gateway.handle_text("99", "/reasoning trace on")

    gateway.tomo.set_channel_reasoning_effort.assert_called_once_with("99", "high")
    gateway.tomo.set_channel_trace_override.assert_called_once_with("99", True)
    assert gateway.sent == [
        ("99", "Reasoning effort set to high. Applies to subsequent messages in this chat."),
        ("99", "Reasoning trace enabled for this chat."),
    ]
