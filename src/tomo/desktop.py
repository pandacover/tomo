from __future__ import annotations

import html
import os
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, NamedTuple
from uuid import uuid4

from .config import settings
from .gateway import TomoGateway, format_tool_input
from .speech import WindowsSpeechInput
from .tools import ApprovalRequest


DESKTOP_CHANNEL_ID = "desktop:local"
FLYOUT_WIDTH = 420
FLYOUT_HEIGHT = 620
FLYOUT_MARGIN = 12
VOICE_AUTO_SEND_DELAY_SECONDS = 3.0
DesktopEvent = dict[str, Any]


class Rect(NamedTuple):
    left: int
    top: int
    right: int
    bottom: int


class DesktopApprovalResponder:
    def __init__(self, emit: "EventEmitter") -> None:
        self.emit = emit
        self._pending: dict[str, queue.Queue[bool]] = {}
        self._lock = threading.Lock()

    def request_approval(self, channel_id: str, request: ApprovalRequest) -> bool:
        approval_id = str(uuid4())
        answers: queue.Queue[bool] = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[approval_id] = answers
        self.emit(
            {
                "type": "approval_request",
                "id": approval_id,
                "operation": request.operation,
                "target": request.target,
                "reason": request.reason,
            }
        )
        try:
            return answers.get()
        finally:
            with self._lock:
                self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool) -> bool:
        with self._lock:
            answers = self._pending.get(approval_id)
        if answers is None:
            return False
        answers.put(bool(approved))
        self.emit({"type": "approval_resolved", "id": approval_id, "approved": bool(approved)})
        return True


class EventEmitter:
    def __init__(self) -> None:
        self.events: queue.Queue[DesktopEvent] = queue.Queue()

    def __call__(self, event: DesktopEvent) -> None:
        self.events.put(event)

    def drain(self) -> list[DesktopEvent]:
        drained: list[DesktopEvent] = []
        while True:
            try:
                drained.append(self.events.get_nowait())
            except queue.Empty:
                return drained


class DesktopBridge:
    def __init__(
        self,
        tomo: TomoGateway | None = None,
        channel_id: str = DESKTOP_CHANNEL_ID,
        speech_input: object | None = None,
    ) -> None:
        self.channel_id = channel_id
        self.emitter = EventEmitter()
        self.responder = DesktopApprovalResponder(self.emitter)
        self.tomo = tomo or TomoGateway(responder=self.responder)
        self.busy = False
        self.voice_state = "idle"
        self.quitting = False
        self.window: object | None = None
        self.quit_callback: Callable[[], None] | None = None
        self.speech_input = speech_input or WindowsSpeechInput(
            on_state=self._handle_voice_state,
            on_partial=self._handle_voice_partial,
            on_final=self._handle_voice_final,
            on_error=self._handle_voice_error,
        )
        self._voice_send_timer: threading.Timer | None = None
        self._pending_voice_text = ""
        self._lock = threading.Lock()

    def set_window(self, window: object) -> None:
        self.window = window

    def bootstrap(self) -> dict[str, object]:
        session = self.tomo.get_session(self.channel_id)
        return {
            "ok": True,
            "type": "ready",
            "model": settings.model,
            "session": asdict(session.metadata),
            "messages": [message_to_dto(message) for message in session.messages],
            "busy": self.busy,
            "voice_state": self.voice_state,
        }

    def poll_events(self) -> list[DesktopEvent]:
        return self.emitter.drain()

    def send_message(self, text: str) -> dict[str, object]:
        text = text.strip()
        if not text:
            return {"ok": False, "error": "Message cannot be empty."}
        with self._lock:
            if self.busy:
                return {"ok": False, "error": "Tomo is still working."}
            self.busy = True
        self.emitter({"type": "busy", "busy": True})
        self.emitter({"type": "user_message", "text": text})
        thread = threading.Thread(target=self._send_worker, args=(text,), daemon=True)
        thread.start()
        return {"ok": True}

    def _send_worker(self, text: str) -> None:
        try:
            reply = self.tomo.send_text_with_events(
                self.channel_id,
                text,
                on_event=lambda event: self.emitter(
                    {
                        "type": "tool_event",
                        "name": event.name,
                        "input": format_tool_input(event.input),
                    }
                ),
                on_text_delta=lambda delta: self.emitter({"type": "assistant_delta", "text": delta}),
            )
        except Exception as exc:  # noqa: BLE001
            self.emitter({"type": "error", "message": str(exc)})
        else:
            self.emitter({"type": "assistant_message", "text": reply.text, "images": list(reply.images)})
        finally:
            with self._lock:
                self.busy = False
            self.emitter({"type": "busy", "busy": False})

    def resolve_approval(self, approval_id: str, approved: bool) -> dict[str, object]:
        if not self.responder.resolve(approval_id, approved):
            return {"ok": False, "error": "No pending approval."}
        return {"ok": True}

    def start_voice_input(self) -> dict[str, object]:
        self._cancel_pending_voice_send()
        start = getattr(self.speech_input, "start", None)
        if not callable(start):
            return {"ok": False, "error": "Voice input is unavailable."}
        if not start():
            return {"ok": False, "error": "Voice input is already listening."}
        return {"ok": True}

    def stop_voice_input(self) -> dict[str, object]:
        stop = getattr(self.speech_input, "stop", None)
        if callable(stop):
            stop()
        self._cancel_pending_voice_send()
        self._handle_voice_state("idle")
        return {"ok": True}

    def cancel_voice_input(self) -> dict[str, object]:
        cancel = getattr(self.speech_input, "cancel", None)
        if callable(cancel):
            cancel()
        self._cancel_pending_voice_send()
        self._handle_voice_state("idle")
        return {"ok": True}

    def show_window(self) -> dict[str, object]:
        if self.window is not None:
            call_window(self.window, "show")
            call_window(self.window, "restore")
            call_window(self.window, "focus")
        return {"ok": True}

    def hide_window(self) -> dict[str, object]:
        if self.window is not None:
            call_window(self.window, "hide")
        return {"ok": True}

    def quit_app(self) -> dict[str, object]:
        self.quitting = True
        self._shutdown_speech_input()
        if self.quit_callback is not None:
            self.quit_callback()
            return {"ok": True}
        if self.window is not None:
            call_window(self.window, "destroy")
        return {"ok": True}

    def _handle_voice_state(self, state: str) -> None:
        self.voice_state = state
        self.emitter({"type": "voice_state", "state": state})

    def _handle_voice_partial(self, text: str) -> None:
        self.emitter({"type": "voice_partial", "text": text})

    def _handle_voice_final(self, text: str) -> None:
        text = text.strip()
        if not text:
            self._handle_voice_state("idle")
            return
        self._cancel_pending_voice_send()
        self._pending_voice_text = text
        self._handle_voice_state("sending")
        self.emitter({"type": "voice_final", "text": text, "send_delay": VOICE_AUTO_SEND_DELAY_SECONDS})
        self._voice_send_timer = threading.Timer(VOICE_AUTO_SEND_DELAY_SECONDS, self._send_pending_voice_text)
        self._voice_send_timer.daemon = True
        self._voice_send_timer.start()

    def _handle_voice_error(self, message: str) -> None:
        self._cancel_pending_voice_send()
        self.voice_state = "idle"
        self.emitter({"type": "voice_error", "message": message})
        self.emitter({"type": "voice_state", "state": "idle"})

    def _send_pending_voice_text(self) -> None:
        with self._lock:
            text = self._pending_voice_text
            self._pending_voice_text = ""
            self._voice_send_timer = None
        if not text:
            return
        self._handle_voice_state("idle")
        result = self.send_message(text)
        if not result.get("ok"):
            self.emitter({"type": "voice_error", "message": str(result.get("error", "Unable to send voice input."))})
            self._handle_voice_state("idle")

    def _cancel_pending_voice_send(self) -> None:
        with self._lock:
            timer = self._voice_send_timer
            self._voice_send_timer = None
            self._pending_voice_text = ""
        if timer is not None:
            timer.cancel()

    def _shutdown_speech_input(self) -> None:
        self._cancel_pending_voice_send()
        shutdown = getattr(self.speech_input, "shutdown", None)
        if callable(shutdown):
            shutdown()


class DesktopApi:
    __slots__ = ("_bridge",)

    def __init__(self, bridge: DesktopBridge) -> None:
        self._bridge = bridge

    def bootstrap(self) -> dict[str, object]:
        return self._bridge.bootstrap()

    def poll_events(self) -> list[DesktopEvent]:
        return self._bridge.poll_events()

    def send_message(self, text: str) -> dict[str, object]:
        return self._bridge.send_message(text)

    def resolve_approval(self, approval_id: str, approved: bool) -> dict[str, object]:
        return self._bridge.resolve_approval(approval_id, approved)

    def start_voice_input(self) -> dict[str, object]:
        return self._bridge.start_voice_input()

    def stop_voice_input(self) -> dict[str, object]:
        return self._bridge.stop_voice_input()

    def cancel_voice_input(self) -> dict[str, object]:
        return self._bridge.cancel_voice_input()

    def show_window(self) -> dict[str, object]:
        return self._bridge.show_window()

    def hide_window(self) -> dict[str, object]:
        return self._bridge.hide_window()

    def quit_app(self) -> dict[str, object]:
        return self._bridge.quit_app()


class DesktopApp:
    def __init__(self, bridge: DesktopBridge | None = None, *, wsl_mode: bool | None = None) -> None:
        self.bridge = bridge or DesktopBridge()
        self.bridge.quit_callback = self._quit
        self.tray_icon: object | None = None
        self.wsl_mode = is_wsl() if wsl_mode is None else wsl_mode

    def run(self) -> None:
        if self.wsl_mode:
            os.environ.setdefault("QT_API", "pyside6")
            validate_wsl_qt_backend()
        import webview

        window = webview.create_window(
            "Tomo",
            html=DESKTOP_HTML,
            js_api=DesktopApi(self.bridge),
            width=720 if self.wsl_mode else FLYOUT_WIDTH,
            height=860 if self.wsl_mode else FLYOUT_HEIGHT,
            min_size=(420, 520),
            hidden=not self.wsl_mode,
            resizable=self.wsl_mode,
            frameless=not self.wsl_mode,
            easy_drag=False,
            draggable=False,
            maximized=False,
            text_select=True,
        )
        self.bridge.set_window(window)
        window.events.closing += self.on_window_closing
        if not self.wsl_mode:
            self._start_tray()
        if self.wsl_mode:
            print("Tomo desktop is running in WSL window mode; close the window to quit.")
            self.bridge.quitting = True
        start_kwargs = {"gui": "qt"} if self.wsl_mode else {}
        webview.start(**start_kwargs)

    def on_window_closing(self, *_: object) -> bool:
        if self.wsl_mode:
            self.bridge.quitting = True
            return True
        if self.bridge.quitting:
            return True
        self.bridge.quitting = True
        if self.tray_icon is not None:
            threading.Thread(target=lambda: call_window(self.tray_icon, "stop"), daemon=True).start()
        return True

    def _start_tray(self) -> bool:
        try:
            import pystray

            self.tray_icon = pystray.Icon(
                "tomo",
                icon=create_tray_image(),
                title="Tomo",
                menu=pystray.Menu(
                    pystray.MenuItem("Open Tomo", self._show_flyout_from_tray, default=True),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Quit", self._quit_from_tray),
                ),
            )
            self.tray_icon.run_detached()
        except Exception as exc:  # noqa: BLE001
            if self.wsl_mode:
                print(f"Tomo desktop tray integration is unavailable in this WSL session: {exc}")
                return False
            raise
        return True

    def _show_flyout_from_tray(self, *_: object) -> None:
        self._position_flyout()
        self.bridge.show_window()

    def _position_flyout(self) -> None:
        if self.bridge.window is None:
            return
        work_area = get_windows_work_area()
        width = min(FLYOUT_WIDTH, max(320, work_area.right - work_area.left - (FLYOUT_MARGIN * 2)))
        height = min(FLYOUT_HEIGHT, max(420, work_area.bottom - work_area.top - (FLYOUT_MARGIN * 2)))
        x = work_area.right - width - FLYOUT_MARGIN
        y = work_area.bottom - height - FLYOUT_MARGIN
        resize_window(self.bridge.window, width, height)
        move_window(self.bridge.window, x, y)

    def _quit_from_tray(self, *_: object) -> None:
        self._quit()

    def _quit(self) -> None:
        self.bridge.quitting = True
        self.bridge._shutdown_speech_input()
        if self.bridge.window is not None:
            call_window(self.bridge.window, "destroy")
        if self.tray_icon is not None:
            call_window(self.tray_icon, "stop")


def message_to_dto(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role", "assistant"))
    content = message.get("content", "")
    if isinstance(content, str):
        return {"role": role, "text": content, "images": []}
    return {"role": role, "text": structured_content_text(content), "images": structured_content_images(content)}


def structured_content_text(content: object) -> str:
    if not isinstance(content, list):
        return str(content)
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(part for part in parts if part)


def structured_content_images(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    images: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        image_url = item.get("image_url")
        if isinstance(image_url, str):
            images.append(image_url)
        elif isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
            images.append(image_url["url"])
    return images


def call_window(target: object, method_name: str) -> None:
    method = getattr(target, method_name, None)
    if callable(method):
        method()


def move_window(window: object, x: int, y: int) -> None:
    method = getattr(window, "move", None)
    if callable(method):
        method(x, y)


def resize_window(window: object, width: int, height: int) -> None:
    method = getattr(window, "resize", None)
    if callable(method):
        method(width, height)


def get_windows_work_area() -> Rect:
    if os.name != "nt":
        return Rect(0, 0, 1920, 1080)
    import ctypes
    from ctypes import wintypes

    work_area = wintypes.RECT()
    spi_getworkarea = 0x0030
    if ctypes.windll.user32.SystemParametersInfoW(spi_getworkarea, 0, ctypes.byref(work_area), 0):
        return Rect(work_area.left, work_area.top, work_area.right, work_area.bottom)
    return Rect(0, 0, 1920, 1080)


def create_tray_image() -> object:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (22, 26, 30, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((7, 7, 57, 57), radius=13, fill=(238, 241, 235, 255))
    draw.rounded_rectangle((15, 18, 49, 46), radius=8, fill=(22, 26, 30, 255))
    draw.rectangle((28, 11, 36, 21), fill=(22, 26, 30, 255))
    draw.ellipse((22, 28, 27, 33), fill=(130, 225, 177, 255))
    draw.ellipse((37, 28, 42, 33), fill=(130, 225, 177, 255))
    return image


def run_desktop() -> None:
    DesktopApp(wsl_mode=is_wsl()).run()


def validate_wsl_qt_backend() -> None:
    missing: list[str] = []
    for module_name in ("qtpy", "PySide6", "PySide6.QtWebEngineWidgets"):
        try:
            __import__(module_name)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{module_name}: {exc}")
    if missing:
        details = "\n".join(f"- {item}" for item in missing)
        raise RuntimeError(
            "Tomo desktop needs the Qt pywebview backend when running inside WSL.\n"
            "Run `uv sync` to install the Python dependencies. If that still fails, WSL may be missing "
            "Linux GUI system packages required by Qt/WebEngine.\n\n"
            f"Missing backend imports:\n{details}"
        )


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        version = open("/proc/version", encoding="utf-8").read().lower()
    except OSError:
        return False
    return "microsoft" in version or "wsl" in version


DESKTOP_HTML = f"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tomo</title>
<style>
:root {{
  --bg: #eceee8;
  --ink: #171a1d;
  --muted: #66706b;
  --line: #c9cec5;
  --panel: #f7f8f3;
  --user: #123f35;
  --assistant: #ffffff;
  --accent: #2f8f67;
  --danger: #9b2f2f;
  --tool: #26323a;
}}
* {{ box-sizing: border-box; }}
html, body {{ height: 100%; margin: 0; }}
body {{
  background: var(--bg);
  color: var(--ink);
  font-family: "Aptos", "Segoe UI", sans-serif;
  font-size: 14px;
  overflow: hidden;
}}
button, textarea {{ font: inherit; }}
.shell {{ height: 100vh; display: grid; grid-template-rows: auto 1fr auto; }}
header {{
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--line);
  background: #f0f2ec;
}}
.brand {{ display: flex; align-items: baseline; gap: 10px; min-width: 0; }}
.brand strong {{ font-size: 20px; letter-spacing: 0; }}
.meta {{ color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.state {{ align-self: center; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
.state.busy {{ color: var(--accent); }}
main {{ min-height: 0; display: grid; grid-template-rows: 1fr auto; }}
#transcript {{ overflow-y: auto; padding: 18px 16px 20px; }}
.msg {{ max-width: 88%; margin: 0 0 14px; }}
.msg.user {{ margin-left: auto; }}
.bubble {{
  padding: 11px 12px;
  border: 1px solid var(--line);
  background: var(--assistant);
  border-radius: 8px;
  line-height: 1.45;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}}
.user .bubble {{ background: var(--user); color: white; border-color: var(--user); }}
.speaker {{ font-size: 11px; color: var(--muted); margin: 0 0 4px 2px; }}
.user .speaker {{ text-align: right; margin-right: 2px; }}
.images {{ display: grid; gap: 8px; margin-top: 8px; }}
.images img {{ max-width: 100%; border-radius: 6px; border: 1px solid var(--line); }}
#tools {{
  max-height: 120px;
  overflow-y: auto;
  border-top: 1px solid var(--line);
  background: #e4e8df;
  padding: 8px 12px;
  font-family: "Cascadia Mono", "Consolas", monospace;
  font-size: 12px;
  display: none;
}}
#tools.visible {{ display: block; }}
.tool-row {{ color: var(--tool); margin: 0 0 4px; overflow-wrap: anywhere; }}
#approval {{ display: none; border-top: 1px solid var(--line); background: #fff7df; padding: 12px 14px; }}
#approval.visible {{ display: block; }}
#approval-title {{ font-weight: 700; margin-bottom: 5px; }}
#approval-body {{ color: #4b4430; white-space: pre-wrap; overflow-wrap: anywhere; }}
.approval-actions {{ display: flex; gap: 8px; margin-top: 10px; }}
#voice-panel {{
  display: none;
  border-top: 1px solid var(--line);
  background: #edf5f0;
  padding: 10px 12px;
  gap: 8px;
  grid-template-columns: 1fr auto;
  align-items: center;
}}
#voice-panel.visible {{ display: grid; }}
#voice-text {{ color: var(--ink); overflow-wrap: anywhere; }}
#voice-text.muted {{ color: var(--muted); }}
.composer {{
  border-top: 1px solid var(--line);
  background: var(--panel);
  padding: 12px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 10px;
}}
textarea {{
  resize: none;
  height: 42px;
  max-height: 140px;
  padding: 10px 11px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: white;
  color: var(--ink);
  outline: none;
}}
textarea:focus {{ border-color: var(--accent); }}
button {{
  border: 1px solid var(--ink);
  background: var(--ink);
  color: white;
  border-radius: 7px;
  padding: 0 14px;
  min-width: 76px;
  cursor: pointer;
}}
button.secondary {{ background: white; color: var(--ink); border-color: var(--line); }}
button.danger {{ background: var(--danger); border-color: var(--danger); }}
button.icon {{
  min-width: 42px;
  width: 42px;
  padding: 0;
  font-weight: 700;
}}
button.icon.listening {{ background: var(--accent); border-color: var(--accent); }}
button:disabled {{ opacity: .45; cursor: default; }}
.empty {{ color: var(--muted); text-align: center; margin-top: 22vh; }}
</style>
</head>
<body>
<div class="shell">
  <header>
    <div class="brand">
      <strong>Tomo</strong>
      <div class="meta" id="meta">{html.escape(settings.model)}</div>
    </div>
    <div class="state" id="state">Ready</div>
  </header>
  <main>
    <div id="transcript"><div class="empty">No messages yet.</div></div>
    <div>
      <div id="tools"></div>
      <div id="approval">
        <div id="approval-title">Approval required</div>
        <div id="approval-body"></div>
        <div class="approval-actions">
          <button id="approve">Approve</button>
          <button id="deny" class="danger">Deny</button>
        </div>
      </div>
      <div id="voice-panel">
        <div id="voice-text" class="muted"></div>
        <button id="voice-cancel" type="button" class="secondary">Cancel</button>
      </div>
      <form class="composer" id="composer">
        <button id="voice" class="icon secondary" type="button" title="Voice input" aria-label="Voice input">Mic</button>
        <textarea id="input" placeholder="Message Tomo"></textarea>
        <button id="send" type="submit">Send</button>
      </form>
    </div>
  </main>
</div>
<script>
const transcript = document.getElementById('transcript');
const tools = document.getElementById('tools');
const state = document.getElementById('state');
const meta = document.getElementById('meta');
const input = document.getElementById('input');
const send = document.getElementById('send');
const composer = document.getElementById('composer');
const approval = document.getElementById('approval');
const approvalBody = document.getElementById('approval-body');
const approve = document.getElementById('approve');
const deny = document.getElementById('deny');
const voice = document.getElementById('voice');
const voicePanel = document.getElementById('voice-panel');
const voiceText = document.getElementById('voice-text');
const voiceCancel = document.getElementById('voice-cancel');
let busy = false;
let pendingApproval = null;
let voiceState = 'idle';
let streamingBubble = null;

function api() {{ return window.pywebview.api; }}
function setBusy(value) {{
  busy = value;
  state.textContent = value ? 'Working' : pendingApproval ? 'Approval' : voiceState === 'listening' ? 'Listening' : voiceState === 'sending' ? 'Sending' : 'Ready';
  state.classList.toggle('busy', value || !!pendingApproval || voiceState !== 'idle');
  send.disabled = value || !!pendingApproval;
  input.disabled = !!pendingApproval;
  voice.disabled = value || !!pendingApproval || voiceState === 'sending';
}}
function clearEmpty() {{
  const empty = transcript.querySelector('.empty');
  if (empty) empty.remove();
}}
function scrollBottom() {{ transcript.scrollTop = transcript.scrollHeight; }}
function addMessage(role, text, images = []) {{
  clearEmpty();
  streamingBubble = null;
  const row = document.createElement('section');
  row.className = `msg ${{role}}`;
  const speaker = document.createElement('div');
  speaker.className = 'speaker';
  speaker.textContent = role === 'user' ? 'You' : 'Tomo';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text || '';
  row.append(speaker, bubble);
  if (images.length) {{
    const imageWrap = document.createElement('div');
    imageWrap.className = 'images';
    for (const url of images) {{
      const img = document.createElement('img');
      img.src = url;
      img.alt = 'Generated image';
      imageWrap.appendChild(img);
    }}
    row.appendChild(imageWrap);
  }}
  transcript.appendChild(row);
  scrollBottom();
  return bubble;
}}
function addDelta(text) {{
  if (!streamingBubble) streamingBubble = addMessage('assistant', '');
  streamingBubble.textContent += text;
  scrollBottom();
}}
function addTool(event) {{
  tools.classList.add('visible');
  const row = document.createElement('div');
  row.className = 'tool-row';
  row.textContent = `${{event.name}}: "${{event.input || ''}}"`;
  tools.appendChild(row);
  tools.scrollTop = tools.scrollHeight;
}}
function showApproval(event) {{
  pendingApproval = event.id;
  approvalBody.textContent = `${{event.operation}} ${{event.target}}\\n\\n${{event.reason}}`;
  approval.classList.add('visible');
  setBusy(busy);
}}
function hideApproval() {{
  pendingApproval = null;
  approval.classList.remove('visible');
  setBusy(busy);
}}
function setVoiceState(nextState) {{
  voiceState = nextState || 'idle';
  voice.classList.toggle('listening', voiceState === 'listening');
  voice.textContent = voiceState === 'listening' ? 'Stop' : 'Mic';
  if (voiceState === 'idle') {{
    voicePanel.classList.remove('visible');
    voiceText.textContent = '';
    voiceText.classList.add('muted');
  }}
  setBusy(busy);
}}
function setVoiceText(text, muted = false) {{
  voicePanel.classList.add('visible');
  voiceText.textContent = text;
  voiceText.classList.toggle('muted', muted);
}}
async function handleEvent(event) {{
  if (event.type === 'busy') setBusy(event.busy);
  if (event.type === 'user_message') addMessage('user', event.text);
  if (event.type === 'assistant_delta') addDelta(event.text);
  if (event.type === 'assistant_message') {{
    if (streamingBubble) {{
      if (event.images && event.images.length) {{
        const imageWrap = document.createElement('div');
        imageWrap.className = 'images';
        for (const url of event.images) {{
          const img = document.createElement('img');
          img.src = url;
          img.alt = 'Generated image';
          imageWrap.appendChild(img);
        }}
        streamingBubble.parentElement.appendChild(imageWrap);
      }}
      streamingBubble = null;
    }} else {{
      addMessage('assistant', event.text, event.images || []);
    }}
  }}
  if (event.type === 'tool_event') addTool(event);
  if (event.type === 'approval_request') showApproval(event);
  if (event.type === 'approval_resolved') hideApproval();
  if (event.type === 'error') addMessage('assistant', `Error: ${{event.message}}`);
  if (event.type === 'voice_state') {{
    setVoiceState(event.state);
    if (event.state === 'listening') setVoiceText('Listening...', true);
  }}
  if (event.type === 'voice_partial') setVoiceText(event.text);
  if (event.type === 'voice_final') {{
    input.value = event.text;
    setVoiceText(`Sending in ${{event.send_delay}}s: ${{event.text}}`);
  }}
  if (event.type === 'voice_error') {{
    setVoiceState('idle');
    addMessage('assistant', `Voice input error: ${{event.message}}`);
  }}
}}
async function poll() {{
  try {{
    const events = await api().poll_events();
    for (const event of events) await handleEvent(event);
  }} finally {{
    setTimeout(poll, 180);
  }}
}}
composer.addEventListener('submit', async (event) => {{
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  const result = await api().send_message(text);
  if (result.ok) input.value = '';
  else addMessage('assistant', result.error || 'Unable to send message.');
}});
input.addEventListener('keydown', (event) => {{
  if (event.key === 'Enter' && !event.shiftKey) {{
    event.preventDefault();
    composer.requestSubmit();
  }}
}});
voice.addEventListener('click', async () => {{
  if (voiceState === 'listening') {{
    await api().cancel_voice_input();
    setVoiceState('idle');
    return;
  }}
  const result = await api().start_voice_input();
  if (!result.ok) addMessage('assistant', result.error || 'Unable to start voice input.');
}});
voiceCancel.addEventListener('click', async () => {{
  await api().cancel_voice_input();
  setVoiceState('idle');
}});
approve.addEventListener('click', () => pendingApproval && api().resolve_approval(pendingApproval, true));
deny.addEventListener('click', () => pendingApproval && api().resolve_approval(pendingApproval, false));
window.addEventListener('pywebviewready', async () => {{
  const data = await api().bootstrap();
  if (data.ok) {{
    meta.textContent = `${{data.model}} - ${{data.session.name}} - ${{data.session.id.slice(0, 8)}}`;
    for (const message of data.messages) addMessage(message.role === 'user' ? 'user' : 'assistant', message.text, message.images || []);
    setVoiceState(data.voice_state || 'idle');
    setBusy(data.busy);
  }}
  poll();
}});
</script>
</body>
</html>
"""
