from __future__ import annotations

import threading
import time

from tomo.gateway import AgentTrace, GatewayReply, ToolCallLifecycleEvent
from tomo.config import settings
from tomo.telegram import (
    TelegramGateway,
    YOLO_ENABLED_MESSAGE,
    YOLO_STATUS_ENABLED,
    parse_allowed_chat_ids,
    split_message,
    telegram_command,
    telegram_command_argument,
)
from tomo.telegram_config import (
    TelegramConfig,
    delete_telegram_config,
    load_telegram_config,
    resolved_telegram_config,
    save_telegram_config,
)


class FakeTomo:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        self.messages.append((channel_id, text))
        if on_event is not None:
            on_event(ToolCallLifecycleEvent(name="web_search", input={"query": "hello"}))
        if on_text_delta is not None:
            on_text_delta("streamed ")
            on_text_delta("reply")
        return GatewayReply(
            text="streamed reply",
            trace=AgentTrace(reasoning_summary="hidden by default"),
        )


class FakeTelegram(TelegramGateway):
    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("tomo", FakeTomo())
        super().__init__("token", **kwargs)
        self.sent: list[tuple[str, str]] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.next_message_id = 1

    def api(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, payload))
        if method == "sendMessage":
            self.sent.append((str(payload["chat_id"]), str(payload["text"])))
            message_id = self.next_message_id
            self.next_message_id += 1
            return {"ok": True, "result": {"message_id": message_id}}
        if method == "editMessageText":
            self.sent.append((str(payload["chat_id"]), str(payload["text"])))
            return {"ok": True, "result": True}
        return {"ok": True, "result": []}


class ClosableClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class InterruptingTelegram(FakeTelegram):
    def __init__(self) -> None:
        self.fake_client = ClosableClient()
        super().__init__(client=self.fake_client)

    def api(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        if method == "getUpdates":
            raise KeyboardInterrupt
        return super().api(method, payload)


def test_parse_allowed_chat_ids_accepts_comma_list():
    assert parse_allowed_chat_ids("1, 2,3") == [1, 2, 3]
    assert parse_allowed_chat_ids(None) == []


def test_telegram_gateway_ctrl_c_stops_without_traceback(capsys):
    gateway = InterruptingTelegram()

    gateway.run()

    assert gateway.fake_client.closed is True
    assert "Telegram gateway stopped." in capsys.readouterr().out


def test_telegram_config_round_trips_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    save_telegram_config(TelegramConfig(bot_token="123:token", allowed_chat_ids=[111, 222]))

    assert load_telegram_config() == TelegramConfig(bot_token="123:token", allowed_chat_ids=[111, 222])
    assert (tmp_path / "telegram.json").stat().st_mode & 0o777 == 0o600

    delete_telegram_config()

    assert load_telegram_config() is None


def test_resolved_telegram_config_uses_saved_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "telegram_bot_token", None)
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", None)
    save_telegram_config(TelegramConfig(bot_token="saved-token", allowed_chat_ids=[123]))

    assert resolved_telegram_config() == TelegramConfig(bot_token="saved-token", allowed_chat_ids=[123])


def test_resolved_telegram_config_allows_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "telegram_bot_token", "env-token")
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "456,789")
    save_telegram_config(TelegramConfig(bot_token="saved-token", allowed_chat_ids=[123]))

    assert resolved_telegram_config() == TelegramConfig(bot_token="env-token", allowed_chat_ids=[456, 789])


def test_split_message_chunks_telegram_limit():
    chunks = split_message("x" * 4100)

    assert [len(chunk) for chunk in chunks] == [4096, 4]


def test_telegram_command_normalizes_bot_qualified_commands():
    assert telegram_command("/approve@tomo_bot") == "/approve"
    assert telegram_command("/DENY@TomoBot extra") == "/deny"
    assert telegram_command("hello") is None


def test_telegram_command_argument_reads_first_argument():
    assert telegram_command_argument("/yolo enable") == "enable"
    assert telegram_command_argument("/yolo@tomo_bot DISABLE") == "disable"
    assert telegram_command_argument("/yolo") is None


def test_telegram_gateway_rejects_unallowed_chat():
    gateway = FakeTelegram(allowed_chat_ids=[1])

    gateway.handle_update({"message": {"chat": {"id": 2}, "text": "hi"}})

    assert gateway.sent == [("2", "This chat is not allowed to use Tomo.")]


def test_telegram_gateway_routes_text_to_tomo():
    tomo = FakeTomo()
    gateway = FakeTelegram(tomo=tomo)

    gateway.reply_worker("123", "hello")

    assert tomo.messages == [("123", "hello")]
    assert [(method, payload["text"]) for method, payload in gateway.calls] == [
        ("sendMessage", 'web_search: "{"query":"hello"}"'),
        ("sendMessage", "streamed reply"),
    ]
    assert all("link_preview_options" not in payload for _, payload in gateway.calls)


class EmptyReplyTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        return GatewayReply(text="", trace=AgentTrace())


class ToolErrorTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        return GatewayReply(text="done", trace=AgentTrace(tool_errors=("terminal: Error: command failed",)))


class LongToolTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        if on_event is not None:
            on_event(ToolCallLifecycleEvent(name="terminal", input={"command": "x" * 80}))
        return GatewayReply(text="done", trace=AgentTrace())


def test_telegram_gateway_does_not_send_empty_reply():
    gateway = FakeTelegram(tomo=EmptyReplyTomo())

    gateway.reply_worker("123", "hello")

    assert gateway.calls == []


def test_telegram_gateway_surfaces_tool_errors_before_final_reply():
    gateway = FakeTelegram(tomo=ToolErrorTomo())

    gateway.reply_worker("123", "hello")

    assert gateway.sent == [
        ("123", "Tool error: terminal: Error: command failed"),
        ("123", "done"),
    ]


class SlowTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        time.sleep(0.05)
        return GatewayReply(text="done", trace=AgentTrace())


def test_telegram_gateway_sends_typing_and_still_working_status(monkeypatch):
    monkeypatch.setattr("tomo.telegram.TELEGRAM_TYPING_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("tomo.telegram.TELEGRAM_STILL_WORKING_INTERVAL_SECONDS", 0.02)
    gateway = FakeTelegram(tomo=SlowTomo())

    gateway.reply_worker("123", "hello")

    assert ("sendChatAction", {"chat_id": "123", "action": "typing"}) in gateway.calls
    assert any(text.startswith("Still working... no final reply after") for _, text in gateway.sent)
    assert ("123", "done") in gateway.sent


class LongDeltaTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        if on_text_delta is not None:
            on_text_delta("x" * 4090)
            on_text_delta("y" * 10)
        return GatewayReply(text="unused", trace=AgentTrace())


def test_telegram_gateway_ignores_text_deltas_and_sends_final_reply_once():
    gateway = FakeTelegram(tomo=LongDeltaTomo())

    gateway.reply_worker("123", "hello")

    send_calls = [(method, payload) for method, payload in gateway.calls if method == "sendMessage"]
    edit_calls = [(method, payload) for method, payload in gateway.calls if method == "editMessageText"]
    assert [(method, payload["text"]) for method, payload in send_calls] == [("sendMessage", "unused")]
    assert edit_calls == []


def test_telegram_gateway_approval_waits_for_chat_reply():
    gateway = FakeTelegram()
    result: dict[str, bool] = {}
    request = type("Request", (), {"operation": "write", "target": "tool call", "reason": "needs write"})()

    thread = threading.Thread(target=lambda: result.setdefault("approved", gateway.request_approval("123", request)), daemon=True)
    thread.start()
    assert "123" in gateway.pending_approvals

    gateway.handle_text("123", "/approve")
    thread.join(timeout=1)

    assert result["approved"] is True
    assert ("123", "Approved.") in gateway.sent


def test_telegram_gateway_approval_accepts_bot_qualified_command():
    gateway = FakeTelegram()
    result: dict[str, bool] = {}
    request = type("Request", (), {"operation": "write", "target": "tool call", "reason": "needs write"})()

    thread = threading.Thread(target=lambda: result.setdefault("approved", gateway.request_approval("123", request)), daemon=True)
    thread.start()
    assert "123" in gateway.pending_approvals

    gateway.handle_text("123", "/approve@tomo_bot")
    thread.join(timeout=1)

    assert result["approved"] is True
    assert ("123", "Approved.") in gateway.sent


def test_telegram_gateway_yolo_status_and_toggle():
    gateway = FakeTelegram()

    gateway.handle_text("123", "/yolo")
    gateway.handle_text("123", "/yolo enable")
    gateway.handle_text("123", "/yolo")
    gateway.handle_text("123", "/yolo disable")

    assert gateway.sent == [
        ("123", "YOLO mode is disabled."),
        ("123", YOLO_ENABLED_MESSAGE),
        ("123", YOLO_STATUS_ENABLED),
        ("123", "YOLO mode disabled."),
    ]


def test_telegram_gateway_debug_tool_toggle_controls_tool_event_truncation():
    gateway = FakeTelegram(tomo=LongToolTomo())

    gateway.reply_worker("123", "run")
    truncated_tool_message = gateway.sent[0][1]

    gateway.handle_text("123", "/debug_tool enable")
    gateway.reply_worker("123", "run")
    full_tool_message = gateway.sent[-2][1]

    assert truncated_tool_message.endswith('..."')
    assert len(full_tool_message) > len(truncated_tool_message)
    assert full_tool_message == 'terminal: "{"command":"' + ("x" * 80) + '"}"'
    assert ("123", "Tool debug output enabled.") in gateway.sent

    gateway.handle_text("123", "/debug-tool disable")
    assert ("123", "Tool debug output disabled.") in gateway.sent


def test_telegram_gateway_debug_tool_requires_known_argument():
    gateway = FakeTelegram()

    gateway.handle_text("123", "/debug-tool maybe")

    assert gateway.sent == [("123", "Usage: /debug-tool enable or /debug-tool disable.")]


def test_telegram_gateway_yolo_requires_known_argument():
    gateway = FakeTelegram()

    gateway.handle_text("123", "/yolo maybe")

    assert gateway.sent == [("123", "Usage: /yolo enable, /yolo disable, or /yolo.")]


def test_telegram_gateway_yolo_auto_approves_without_prompt():
    gateway = FakeTelegram()
    request = type("Request", (), {"operation": "write", "target": "tool call", "reason": "needs write"})()

    gateway.handle_text("123", "/yolo enable")

    assert gateway.request_approval("123", request) is True
    assert "123" not in gateway.pending_approvals
    assert gateway.sent == [("123", YOLO_ENABLED_MESSAGE)]


def test_telegram_gateway_yolo_enable_approves_pending_request():
    gateway = FakeTelegram()
    result: dict[str, bool] = {}
    request = type("Request", (), {"operation": "write", "target": "tool call", "reason": "needs write"})()

    thread = threading.Thread(target=lambda: result.setdefault("approved", gateway.request_approval("123", request)), daemon=True)
    thread.start()
    assert "123" in gateway.pending_approvals

    gateway.handle_text("123", "/yolo enable")
    thread.join(timeout=1)

    assert result["approved"] is True
    assert ("123", f"{YOLO_ENABLED_MESSAGE} Approved pending tool call.") in gateway.sent


def test_telegram_gateway_does_not_start_new_task_during_pending_approval():
    tomo = FakeTomo()
    gateway = FakeTelegram(tomo=tomo)
    request = type("Request", (), {"operation": "terminal", "target": "tool call", "reason": "needs shell"})()

    thread = threading.Thread(target=lambda: gateway.request_approval("123", request), daemon=True)
    thread.start()
    assert "123" in gateway.pending_approvals

    gateway.handle_text("123", "wassup?")

    assert tomo.messages == []
    assert ("123", "Reply /approve or /deny.") in gateway.sent
