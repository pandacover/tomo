from __future__ import annotations

import os
import sys
import threading
import time
from types import SimpleNamespace

import tomo.desktop as desktop
from tomo.desktop import (
    DesktopApi,
    DesktopApp,
    DesktopBridge,
    DesktopApprovalResponder,
    EventEmitter,
    Rect,
    run_desktop,
    validate_wsl_qt_backend,
)
from tomo.gateway import AgentTrace, GatewayReply, ToolCallLifecycleEvent
from tomo.session_store import create_session
from tomo.tools import ApprovalRequest


class FakeTomo:
    def __init__(self) -> None:
        self.session = create_session("Desktop")
        self.messages: list[tuple[str, str]] = []

    def get_session(self, channel_id: str):
        return self.session

    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        self.messages.append((channel_id, text))
        if on_event is not None:
            on_event(ToolCallLifecycleEvent(name="web_search", input={"query": text}))
        if on_text_delta is not None:
            on_text_delta("streamed ")
            on_text_delta("reply")
        return GatewayReply(text="streamed reply", trace=AgentTrace(), images=("https://example.com/image.png",))


class SlowTomo(FakeTomo):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        self.started.set()
        self.release.wait(timeout=1)
        return GatewayReply(text="done", trace=AgentTrace())


class FakeWindow:
    def __init__(self) -> None:
        self.hidden = False
        self.shown = False
        self.restored = False
        self.focused = False
        self.destroyed = False
        self.position: tuple[int, int] | None = None
        self.size: tuple[int, int] | None = None

    def hide(self) -> None:
        self.hidden = True

    def show(self) -> None:
        self.shown = True

    def restore(self) -> None:
        self.restored = True

    def focus(self) -> None:
        self.focused = True

    def move(self, x: int, y: int) -> None:
        self.position = (x, y)

    def resize(self, width: int, height: int) -> None:
        self.size = (width, height)

    def destroy(self) -> None:
        self.destroyed = True


class FakeTrayIcon:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeSpeechInput:
    def __init__(self, start_result: bool = True) -> None:
        self.start_result = start_result
        self.started = False
        self.stopped = False
        self.canceled = False
        self.shutdown_called = False

    def start(self) -> bool:
        self.started = True
        return self.start_result

    def stop(self) -> None:
        self.stopped = True

    def cancel(self) -> None:
        self.canceled = True

    def shutdown(self) -> None:
        self.shutdown_called = True


def wait_for_idle(bridge: DesktopBridge) -> None:
    deadline = time.monotonic() + 2
    while bridge.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert bridge.busy is False


def test_desktop_bridge_bootstrap_returns_session_metadata_and_messages():
    tomo = FakeTomo()
    tomo.session.messages.append({"role": "user", "content": "hello"})
    tomo.session.messages.append({"role": "assistant", "content": "hi"})
    bridge = DesktopBridge(tomo=tomo)

    data = bridge.bootstrap()

    assert data["ok"] is True
    assert data["session"]["name"] == "Desktop"
    assert data["messages"] == [
        {"role": "user", "text": "hello", "images": []},
        {"role": "assistant", "text": "hi", "images": []},
    ]


def test_desktop_api_exposes_only_narrow_bridge_methods():
    bridge = DesktopBridge(tomo=FakeTomo())
    api = DesktopApi(bridge)

    public_names = [name for name in dir(api) if not name.startswith("_")]

    assert public_names == [
        "bootstrap",
        "cancel_voice_input",
        "hide_window",
        "poll_events",
        "quit_app",
        "resize_flyout",
        "resolve_approval",
        "send_message",
        "show_window",
        "start_voice_input",
        "stop_voice_input",
    ]
    assert api.bootstrap()["ok"] is True
    assert api.poll_events() == []


def test_desktop_bridge_send_message_rejects_empty_text():
    bridge = DesktopBridge(tomo=FakeTomo())

    assert bridge.send_message("   ") == {"ok": False, "error": "Message cannot be empty."}


def test_desktop_bridge_send_message_rejects_when_busy():
    tomo = SlowTomo()
    bridge = DesktopBridge(tomo=tomo)

    assert bridge.send_message("first") == {"ok": True}
    assert tomo.started.wait(timeout=1)

    assert bridge.send_message("second") == {"ok": False, "error": "Tomo is still working."}

    tomo.release.set()
    wait_for_idle(bridge)


def test_desktop_bridge_start_and_cancel_voice_input():
    speech = FakeSpeechInput()
    bridge = DesktopBridge(tomo=FakeTomo(), speech_input=speech)

    assert bridge.start_voice_input() == {"ok": True}
    assert speech.started is True

    assert bridge.cancel_voice_input() == {"ok": True}
    assert speech.canceled is True
    assert {"type": "voice_state", "state": "idle"} in bridge.poll_events()


def test_desktop_bridge_rejects_duplicate_voice_input():
    speech = FakeSpeechInput(start_result=False)
    bridge = DesktopBridge(tomo=FakeTomo(), speech_input=speech)

    assert bridge.start_voice_input() == {"ok": False, "error": "Voice input is already listening."}


def test_desktop_bridge_resize_flyout_clamps_and_bottom_aligns(monkeypatch):
    bridge = DesktopBridge(tomo=FakeTomo())
    window = FakeWindow()
    bridge.set_window(window)
    monkeypatch.setattr("tomo.desktop.get_windows_work_area", lambda: Rect(0, 0, 1440, 900))

    assert bridge.resize_flyout(900) == {"ok": True, "height": 700}

    assert window.size == (420, 700)
    assert window.position == (1008, 188)


def test_desktop_bridge_resize_flyout_uses_minimum_height(monkeypatch):
    bridge = DesktopBridge(tomo=FakeTomo())
    window = FakeWindow()
    bridge.set_window(window)
    monkeypatch.setattr("tomo.desktop.get_windows_work_area", lambda: Rect(0, 0, 1440, 900))

    assert bridge.resize_flyout(20) == {"ok": True, "height": 96}

    assert window.size == (420, 96)
    assert window.position == (1008, 792)


def test_desktop_bridge_voice_final_auto_sends_to_agent(monkeypatch):
    monkeypatch.setattr(desktop, "VOICE_AUTO_SEND_DELAY_SECONDS", 0.01)
    tomo = FakeTomo()
    bridge = DesktopBridge(tomo=tomo, speech_input=FakeSpeechInput())

    bridge._handle_voice_final(" send this ")
    wait_for_idle(bridge)

    deadline = time.monotonic() + 1
    while not tomo.messages and time.monotonic() < deadline:
        time.sleep(0.01)
    wait_for_idle(bridge)

    assert tomo.messages == [("desktop:local", "send this")]
    events = bridge.poll_events()
    assert {"type": "voice_state", "state": "sending"} in events
    assert {"type": "voice_final", "text": "send this", "send_delay": 0.01} in events
    assert {"type": "user_message", "text": "send this"} in events


def test_desktop_bridge_cancel_voice_input_prevents_pending_auto_send(monkeypatch):
    monkeypatch.setattr(desktop, "VOICE_AUTO_SEND_DELAY_SECONDS", 0.05)
    tomo = FakeTomo()
    bridge = DesktopBridge(tomo=tomo, speech_input=FakeSpeechInput())

    bridge._handle_voice_final("do not send")
    bridge.cancel_voice_input()
    time.sleep(0.08)

    assert tomo.messages == []


def test_desktop_bridge_successful_worker_queues_events():
    bridge = DesktopBridge(tomo=FakeTomo())

    assert bridge.send_message("hello") == {"ok": True}
    wait_for_idle(bridge)

    events = bridge.poll_events()
    assert events[0] == {"type": "busy", "busy": True}
    assert events[1] == {"type": "user_message", "text": "hello"}
    assert {"type": "assistant_delta", "text": "streamed "} in events
    assert {"type": "assistant_delta", "text": "reply"} in events
    assert {
        "type": "assistant_message",
        "text": "streamed reply",
        "images": ["https://example.com/image.png"],
    } in events
    assert events[-1] == {"type": "busy", "busy": False}


def test_desktop_bridge_tool_callback_queues_tool_event():
    bridge = DesktopBridge(tomo=FakeTomo())

    bridge.send_message("hello")
    wait_for_idle(bridge)

    assert {"type": "tool_event", "name": "web_search", "input": '{"query":"hello"}'} in bridge.poll_events()


def test_desktop_approval_responder_resolves_approve():
    emitter = EventEmitter()
    responder = DesktopApprovalResponder(emitter)
    result: dict[str, bool] = {}

    thread = threading.Thread(
        target=lambda: result.setdefault(
            "approved",
            responder.request_approval("desktop:local", ApprovalRequest("write", "tool call", "needs approval")),
        )
    )
    thread.start()
    deadline = time.monotonic() + 1
    approval_id = None
    while approval_id is None and time.monotonic() < deadline:
        for event in emitter.drain():
            if event["type"] == "approval_request":
                approval_id = event["id"]
        time.sleep(0.01)
    assert isinstance(approval_id, str)

    assert responder.resolve(approval_id, True) is True
    thread.join(timeout=1)

    assert result["approved"] is True


def test_desktop_approval_responder_resolves_deny():
    emitter = EventEmitter()
    responder = DesktopApprovalResponder(emitter)
    result: dict[str, bool] = {}

    thread = threading.Thread(
        target=lambda: result.setdefault(
            "approved",
            responder.request_approval("desktop:local", ApprovalRequest("write", "tool call", "needs approval")),
        )
    )
    thread.start()
    deadline = time.monotonic() + 1
    approval_id = None
    while approval_id is None and time.monotonic() < deadline:
        for event in emitter.drain():
            if event["type"] == "approval_request":
                approval_id = event["id"]
        time.sleep(0.01)
    assert isinstance(approval_id, str)

    assert responder.resolve(approval_id, False) is True
    thread.join(timeout=1)

    assert result["approved"] is False


def test_desktop_app_close_exits_on_native_windows():
    bridge = DesktopBridge(tomo=FakeTomo())
    window = FakeWindow()
    bridge.set_window(window)
    app = DesktopApp(bridge=bridge, wsl_mode=False)

    assert app.on_window_closing() is True
    assert bridge.quitting is True
    assert window.hidden is False


def test_desktop_app_close_allows_explicit_quit():
    bridge = DesktopBridge(tomo=FakeTomo())
    window = FakeWindow()
    bridge.set_window(window)
    app = DesktopApp(bridge=bridge, wsl_mode=False)
    bridge.quitting = True

    assert app.on_window_closing() is True
    assert window.hidden is False


def test_desktop_app_quit_destroys_window_and_stops_tray():
    bridge = DesktopBridge(tomo=FakeTomo())
    window = FakeWindow()
    tray = FakeTrayIcon()
    bridge.set_window(window)
    speech = FakeSpeechInput()
    bridge.speech_input = speech
    app = DesktopApp(bridge=bridge, wsl_mode=False)
    app.tray_icon = tray

    app._quit()

    assert bridge.quitting is True
    assert window.destroyed is True
    assert tray.stopped is True
    assert speech.shutdown_called is True


def test_desktop_app_tray_click_shows_bottom_right_flyout(monkeypatch):
    bridge = DesktopBridge(tomo=FakeTomo())
    window = FakeWindow()
    bridge.set_window(window)
    app = DesktopApp(bridge=bridge, wsl_mode=False)
    monkeypatch.setattr("tomo.desktop.get_windows_work_area", lambda: Rect(0, 0, 1440, 900))

    app._show_flyout_from_tray()

    assert window.size == (420, 132)
    assert window.position == (1008, 756)
    assert window.shown is True
    assert window.restored is True
    assert window.focused is True


def test_cli_desktop_refuses_when_not_logged_in(monkeypatch, capsys):
    from tomo import cli

    monkeypatch.setattr(sys, "argv", ["tomo", "desktop"])
    monkeypatch.setattr(cli, "load_tokens", lambda: None)
    calls = []
    monkeypatch.setattr(cli, "run_desktop", lambda: calls.append("desktop"))

    cli.main()

    assert "Not logged in. Run `uv run tomo login` first." in capsys.readouterr().out
    assert calls == []


def test_cli_desktop_calls_run_desktop_when_logged_in(monkeypatch):
    from tomo import cli

    calls = []
    monkeypatch.setattr(sys, "argv", ["tomo", "desktop"])
    monkeypatch.setattr(cli, "load_tokens", lambda: object())
    monkeypatch.setattr(cli, "run_desktop", lambda: calls.append("desktop"))

    cli.main()

    assert calls == ["desktop"]


def test_run_desktop_starts_app_in_wsl_mode(monkeypatch):
    calls = []
    monkeypatch.setattr("tomo.desktop.is_wsl", lambda: True)
    monkeypatch.setattr("tomo.desktop.DesktopApp.run", lambda self: calls.append(self.wsl_mode))

    run_desktop()

    assert calls == [True]


def test_desktop_app_wsl_uses_qt_and_visible_window(monkeypatch, capsys):
    created = {}
    started = {}
    tray_calls = []

    class FakeEvents:
        def __init__(self) -> None:
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class FakeWindow:
        def __init__(self) -> None:
            self.events = SimpleNamespace(closing=FakeEvents(), shown=FakeEvents())

    fake_window = FakeWindow()
    def create_window(*args, **kwargs):
        created["kwargs"] = kwargs
        return fake_window

    fake_webview = SimpleNamespace(
        create_window=create_window,
        start=lambda **kwargs: started.update(kwargs),
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr("tomo.desktop.DesktopApp._start_tray", lambda self: tray_calls.append("tray"))
    monkeypatch.setattr("tomo.desktop.validate_wsl_qt_backend", lambda: None)
    monkeypatch.delenv("QT_API", raising=False)

    DesktopApp(bridge=DesktopBridge(tomo=FakeTomo()), wsl_mode=True).run()

    assert os.environ["QT_API"] == "pyside6"
    assert created["kwargs"]["hidden"] is False
    assert created["kwargs"]["resizable"] is True
    assert created["kwargs"]["frameless"] is False
    assert started == {"gui": "qt"}
    assert tray_calls == []
    assert "WSL window mode" in capsys.readouterr().out


def test_desktop_app_native_windows_starts_hidden_with_default_webview(monkeypatch):
    created = {}
    started = {"called": False}

    class FakeEvents:
        def __init__(self) -> None:
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class FakeWindow:
        def __init__(self) -> None:
            self.events = SimpleNamespace(closing=FakeEvents(), shown=FakeEvents())

    fake_window = FakeWindow()
    def create_window(*args, **kwargs):
        created["kwargs"] = kwargs
        return fake_window

    fake_webview = SimpleNamespace(
        create_window=create_window,
        start=lambda **kwargs: started.update({"called": True, "kwargs": kwargs}),
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr("tomo.desktop.DesktopApp._start_tray", lambda self: True)
    monkeypatch.setattr("tomo.desktop.validate_qt_backend", lambda: None)
    monkeypatch.delenv("QT_API", raising=False)

    DesktopApp(bridge=DesktopBridge(tomo=FakeTomo()), wsl_mode=False).run()

    assert os.environ["QT_API"] == "pyside6"
    assert created["kwargs"]["hidden"] is True
    assert created["kwargs"]["width"] == 420
    assert created["kwargs"]["height"] == 132
    assert created["kwargs"]["min_size"] == (320, 96)
    assert created["kwargs"]["resizable"] is False
    assert created["kwargs"]["frameless"] is True
    assert created["kwargs"]["transparent"] is True
    assert created["kwargs"]["background_color"] == "#000000"
    assert created["kwargs"]["easy_drag"] is False
    assert created["kwargs"]["draggable"] is False
    assert created["kwargs"]["maximized"] is False
    assert isinstance(created["kwargs"]["js_api"], DesktopApi)
    assert not isinstance(created["kwargs"]["js_api"], DesktopBridge)
    assert started == {"called": True, "kwargs": {"gui": "qt"}}


def test_desktop_app_wsl_close_exits_instead_of_hiding():
    bridge = DesktopBridge(tomo=FakeTomo())
    window = FakeWindow()
    bridge.set_window(window)
    app = DesktopApp(bridge=bridge, wsl_mode=True)

    assert app.on_window_closing() is True
    assert bridge.quitting is True
    assert window.hidden is False


def test_validate_wsl_qt_backend_reports_missing_import(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "qtpy":
            raise ModuleNotFoundError("No module named 'qtpy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    try:
        validate_wsl_qt_backend()
    except RuntimeError as exc:
        assert "Run `uv sync`" in str(exc)
        assert "qtpy" in str(exc)
    else:
        raise AssertionError("validate_wsl_qt_backend should fail when qtpy is missing")
