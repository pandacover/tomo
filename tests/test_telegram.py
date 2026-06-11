from __future__ import annotations

import os
import signal
import threading
import time

from tomo.gateway import AgentTrace, GatewayReply, ToolCallLifecycleEvent
from tomo.config import settings
from tomo.slash_commands import command_argument
from tomo.telegram import (
    TelegramGateway,
    YOLO_ENABLED_MESSAGE,
    YOLO_STATUS_ENABLED,
    decode_data_url,
    parse_allowed_chat_ids,
    process_is_running,
    read_pid,
    split_message,
    start_telegram,
    stop_telegram,
    telegram_command,
    telegram_log_path,
    telegram_pid_path,
    write_pid,
)
from tomo.telegram_config import (
    TelegramConfig,
    delete_telegram_config,
    load_telegram_config,
    resolved_telegram_config,
    save_telegram_config,
)
from tomo.session_store import ChatSession, create_session, list_sessions, load_session, save_session


class FakeTomo:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []
        self.sessions: dict[str, ChatSession] = {}

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

    def send_user_content_with_events(self, channel_id: str, content: object, on_event=None, on_text_delta=None) -> GatewayReply:
        self.messages.append((channel_id, content))
        return GatewayReply(text="image reply", trace=AgentTrace())

    def list_channel_sessions(self, channel_id: str) -> list[ChatSession]:
        return list_sessions()

    def set_channel_session(self, channel_id: str, session_id: str) -> ChatSession:
        session = load_session(session_id)
        self.sessions[channel_id] = session
        return session


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


class ImageTelegram(FakeTelegram):
    def telegram_image_data_url(self, file_id: str) -> str:
        return f"data:image/jpeg;base64,{file_id}"


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


def test_start_telegram_spawns_background_gateway(tmp_path, monkeypatch, capsys):
    popen_calls = []

    class FakeProcess:
        pid = 12345

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("tomo.token_store.load_tokens", lambda: object())
    monkeypatch.setattr(
        "tomo.telegram.resolved_telegram_config",
        lambda: TelegramConfig(bot_token="token", allowed_chat_ids=[1]),
    )
    monkeypatch.setattr("tomo.telegram.process_is_running", lambda pid: False)
    monkeypatch.setattr("tomo.telegram.find_telegram_process_ids", lambda: [])
    monkeypatch.setattr(
        "tomo.telegram.subprocess.Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)) or FakeProcess(),
    )

    start_telegram()

    assert read_pid(telegram_pid_path()) == 12345
    assert telegram_log_path() == tmp_path / "telegram.log"
    assert popen_calls[0][0][0][-2:] == ["-c", "from tomo.telegram import run_telegram; run_telegram()"]
    assert popen_calls[0][1]["start_new_session"] is True
    assert "Telegram gateway started with PID 12345." in capsys.readouterr().out


def test_start_telegram_refuses_when_gateway_is_already_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("tomo.token_store.load_tokens", lambda: object())
    monkeypatch.setattr(
        "tomo.telegram.resolved_telegram_config",
        lambda: TelegramConfig(bot_token="token", allowed_chat_ids=[]),
    )
    monkeypatch.setattr("tomo.telegram.process_is_running", lambda pid: True)
    write_pid(telegram_pid_path(), 12345)

    start_telegram()

    assert "already running with PID 12345" in capsys.readouterr().out


def test_stop_telegram_terminates_running_gateway(tmp_path, monkeypatch, capsys):
    killed = []
    running = {12345}

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("tomo.telegram.os.name", "posix", raising=False)
    monkeypatch.setattr("tomo.telegram.process_is_running", lambda pid: pid in running)

    def fake_kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        running.discard(pid)

    monkeypatch.setattr("tomo.telegram.os.kill", fake_kill)
    monkeypatch.setattr("tomo.telegram.find_telegram_process_ids", lambda: [])
    write_pid(telegram_pid_path(), 12345)

    stop_telegram()

    assert killed == [(12345, getattr(signal, "SIGKILL", signal.SIGTERM))]
    assert not telegram_pid_path().exists()
    assert "Telegram gateway stopped." in capsys.readouterr().out


def test_process_is_running_uses_open_process_on_windows(monkeypatch):
    import ctypes

    monkeypatch.setattr(os, "name", "nt", raising=False)
    calls: list[int] = []

    class FakeKernel32:
        @staticmethod
        def OpenProcess(_access: int, _inherit: bool, pid: int) -> int:
            calls.append(pid)
            return 1 if pid == 12345 else 0

        @staticmethod
        def CloseHandle(_handle: int) -> int:
            return 1

    monkeypatch.setattr(ctypes.windll, "kernel32", FakeKernel32())

    assert process_is_running(12345) is True
    assert process_is_running(99999) is False
    assert calls == [12345, 99999]


def test_stop_telegram_removes_stale_windows_pid_file(tmp_path, monkeypatch, capsys):
    calls: list[list[str]] = []

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("tomo.telegram.os.name", "nt", raising=False)
    monkeypatch.setattr("tomo.telegram.find_telegram_process_ids", lambda: [])

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        return object()

    monkeypatch.setattr("tomo.telegram.subprocess.run", fake_run)
    write_pid(telegram_pid_path(), 12345)

    stop_telegram()

    assert calls == [["taskkill", "/PID", "12345", "/T", "/F"]]
    assert not telegram_pid_path().exists()
    assert "Telegram gateway stopped." in capsys.readouterr().out


def test_stop_telegram_stops_orphan_without_pid_file(tmp_path, monkeypatch, capsys):
    calls: list[list[str]] = []
    running = {54321}
    find_results = iter([[54321], []])

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("tomo.telegram.os.name", "nt", raising=False)
    monkeypatch.setattr("tomo.telegram.process_is_running", lambda pid: pid in running)
    monkeypatch.setattr("tomo.telegram.find_telegram_process_ids", lambda: next(find_results, []))

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        running.discard(int(command[2]))
        return object()

    monkeypatch.setattr("tomo.telegram.subprocess.run", fake_run)

    stop_telegram()

    assert calls == [["taskkill", "/PID", "54321", "/T", "/F"]]
    assert "Telegram gateway stopped." in capsys.readouterr().out


def test_start_telegram_repairs_pid_file_for_orphan(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("tomo.telegram.ensure_logged_in", lambda: True)
    monkeypatch.setattr(
        "tomo.telegram.resolved_telegram_config",
        lambda: TelegramConfig(bot_token="token", allowed_chat_ids=[1]),
    )
    monkeypatch.setattr("tomo.telegram.find_telegram_process_ids", lambda: [777])
    monkeypatch.setattr("tomo.telegram.process_is_running", lambda pid: False)

    start_telegram()

    assert telegram_pid_path().read_text(encoding="utf-8").strip() == "777"
    assert "already running with PID 777" in capsys.readouterr().out


def test_stop_telegram_uses_taskkill_on_windows(tmp_path, monkeypatch, capsys):
    calls: list[list[str]] = []
    running = {12345}

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("tomo.telegram.os.name", "nt", raising=False)
    monkeypatch.setattr("tomo.telegram.process_is_running", lambda pid: pid in running)

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        running.discard(int(command[2]))
        return object()

    monkeypatch.setattr("tomo.telegram.subprocess.run", fake_run)
    monkeypatch.setattr("tomo.telegram.find_telegram_process_ids", lambda: [])
    write_pid(telegram_pid_path(), 12345)

    stop_telegram()

    assert calls == [["taskkill", "/PID", "12345", "/T", "/F"]]
    assert not telegram_pid_path().exists()
    assert "Telegram gateway stopped." in capsys.readouterr().out


def test_stop_telegram_kills_all_orphan_processes(tmp_path, monkeypatch, capsys):
    calls: list[list[str]] = []
    find_results = iter([[111, 222], []])

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("tomo.telegram.os.name", "nt", raising=False)
    monkeypatch.setattr("tomo.telegram.find_telegram_process_ids", lambda: next(find_results, []))

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        return object()

    monkeypatch.setattr("tomo.telegram.subprocess.run", fake_run)

    stop_telegram()

    assert calls == [
        ["taskkill", "/PID", "111", "/T", "/F"],
        ["taskkill", "/PID", "222", "/T", "/F"],
    ]
    assert "Telegram gateway stopped." in capsys.readouterr().out


def test_telegram_config_round_trips_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    save_telegram_config(TelegramConfig(bot_token="123:token", allowed_chat_ids=[111, 222]))

    assert load_telegram_config() == TelegramConfig(bot_token="123:token", allowed_chat_ids=[111, 222])
    if os.name != "nt":
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


def test_command_argument_reads_first_argument():
    assert command_argument("/yolo enable") == "enable"
    assert command_argument("/yolo@tomo_bot DISABLE") == "disable"
    assert command_argument("/yolo") is None


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
        ("sendMessage", 'Tool calls (1)\n• web_search("{"query":"hello"}")'),
        ("sendMessage", "streamed reply"),
    ]
    assert all("link_preview_options" not in payload for _, payload in gateway.calls)


def test_telegram_gateway_routes_photo_to_tomo():
    tomo = FakeTomo()
    gateway = ImageTelegram(tomo=tomo)

    gateway.handle_update(
        {
            "message": {
                "chat": {"id": 123},
                "caption": "what is in this image?",
                "photo": [
                    {"file_id": "small", "width": 100, "file_size": 10},
                    {"file_id": "large", "width": 1000, "file_size": 100},
                ],
            }
        }
    )
    deadline = time.monotonic() + 1
    while not tomo.messages and time.monotonic() < deadline:
        time.sleep(0.01)

    assert tomo.messages == [
        (
            "123",
            [
                {"type": "text", "text": "what is in this image?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,large"}},
            ],
        )
    ]
    assert gateway.sent == [("123", "image reply")]


def test_telegram_gateway_session_command_lists_saved_sessions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = create_session("Project A")
    save_session(session)
    gateway = FakeTelegram()

    gateway.handle_text("123", "/session")

    assert "123" in gateway.pending_session_choices
    assert gateway.sent == [
        (
            "123",
            f"Saved sessions:\n1. Project A - {session.metadata.updated_date} - {session.metadata.id[:8]}\n"
            "Reply with a session number to load it, or /cancel.",
        )
    ]


def test_telegram_gateway_session_selection_switches_chat_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = create_session("First")
    first.messages.append({"role": "user", "content": "first"})
    save_session(first)
    second = create_session("Second")
    second.messages.append({"role": "user", "content": "second"})
    save_session(second)
    gateway = FakeTelegram()

    gateway.handle_text("123", "/session")
    selected = gateway.pending_session_choices["123"][0]
    gateway.handle_text("123", "1")

    assert gateway.tomo.sessions["123"].metadata.id == selected.metadata.id
    assert "123" not in gateway.pending_session_choices
    assert gateway.sent[-1] == ("123", f"Loaded session: {selected.metadata.name} - {selected.metadata.id[:8]}")


def test_telegram_gateway_session_selection_validates_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_session(create_session("Project A"))
    gateway = FakeTelegram()

    gateway.handle_text("123", "/session")
    gateway.handle_text("123", "bogus")
    gateway.handle_text("123", "99")

    assert gateway.sent[-2:] == [
        ("123", "Reply with a session number, or /cancel."),
        ("123", "Choose a number from 1 to 1, or /cancel."),
    ]
    assert "123" in gateway.pending_session_choices


def test_telegram_gateway_session_selection_can_be_cancelled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_session(create_session("Project A"))
    gateway = FakeTelegram()

    gateway.handle_text("123", "/session")
    gateway.handle_text("123", "/cancel")

    assert "123" not in gateway.pending_session_choices
    assert gateway.sent[-1] == ("123", "Session selection cancelled.")


class EmptyReplyTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        return GatewayReply(text="", trace=AgentTrace())


class ToolErrorTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        return GatewayReply(text="done", trace=AgentTrace(tool_errors=("terminal: Error: command failed",)))


class ReasoningTomo:
    channel_trace_override = {"123": True}

    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        return GatewayReply(text="done", trace=AgentTrace(reasoning_summary="Inspecting code\nChecking tests"))


class LongToolTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        if on_event is not None:
            on_event(ToolCallLifecycleEvent(name="terminal", input={"command": "x" * 80}))
        return GatewayReply(text="done", trace=AgentTrace())


class MultiToolTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        if on_event is not None:
            on_event(ToolCallLifecycleEvent(name="files_search", input={"query": "telegram"}))
            on_event(ToolCallLifecycleEvent(name="read_file", input={"path": "src/tomo/telegram.py"}))
        return GatewayReply(text="done", trace=AgentTrace())


class PhotoReplyTomo:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        return GatewayReply(text="done", trace=AgentTrace(), images=("https://example.com/image.png",))


def test_telegram_gateway_does_not_send_empty_reply():
    gateway = FakeTelegram(tomo=EmptyReplyTomo())

    gateway.reply_worker("123", "hello")

    assert gateway.calls == []


def test_telegram_gateway_surfaces_tool_errors_before_final_reply():
    gateway = FakeTelegram(tomo=ToolErrorTomo())

    gateway.reply_worker("123", "hello")

    assert gateway.sent == [
        ("123", "Tool errors (1)\n• terminal: Error: command failed"),
        ("123", "done"),
    ]


def test_telegram_gateway_surfaces_reasoning_trace_as_tree_before_final_reply():
    gateway = FakeTelegram(tomo=ReasoningTomo())

    gateway.reply_worker("123", "hello")

    assert gateway.sent == [
        ("123", "Thinking\nInspecting code\nChecking tests"),
        ("123", "done"),
    ]


def test_telegram_gateway_groups_consecutive_tool_calls_by_editing_same_message():
    gateway = FakeTelegram(tomo=MultiToolTomo())

    gateway.reply_worker("123", "hello")

    assert [(method, payload["text"]) for method, payload in gateway.calls] == [
        ("sendMessage", 'Tool calls (1)\n• files_search("{"query":"telegram"}")'),
        (
            "editMessageText",
            'Tool calls (2)\n'
            '• files_search("{"query":"telegram"}")\n'
            '• read_file("{"path":"src/tomo/telegram.py"}")',
        ),
        ("sendMessage", "done"),
    ]


def test_telegram_gateway_sends_reply_images_as_photos():
    gateway = FakeTelegram(tomo=PhotoReplyTomo())

    gateway.reply_worker("123", "draw")

    assert ("sendPhoto", {"chat_id": "123", "photo": "https://example.com/image.png"}) in gateway.calls
    assert ("123", "done") in gateway.sent


def test_decode_data_url_reads_media_type_and_bytes():
    media_type, data = decode_data_url("data:image/png;base64,aGk=")

    assert media_type == "image/png"
    assert data == b"hi"


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

    assert truncated_tool_message.endswith('...")')
    assert len(full_tool_message) > len(truncated_tool_message)
    assert full_tool_message == 'Tool calls (1)\n• terminal("{"command":"' + ("x" * 80) + '"}")'
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
