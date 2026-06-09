from __future__ import annotations

import html
import os
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from .config import settings
from .gateway import TomoGateway, format_tool_input
from .tools import ApprovalRequest


DESKTOP_CHANNEL_ID = "desktop:local"
DesktopEvent = dict[str, Any]


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
    def __init__(self, tomo: TomoGateway | None = None, channel_id: str = DESKTOP_CHANNEL_ID) -> None:
        self.channel_id = channel_id
        self.emitter = EventEmitter()
        self.responder = DesktopApprovalResponder(self.emitter)
        self.tomo = tomo or TomoGateway(responder=self.responder)
        self.busy = False
        self.quitting = False
        self.window: object | None = None
        self.quit_callback: Callable[[], None] | None = None
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

    def show_window(self) -> dict[str, object]:
        if self.window is not None:
            call_window(self.window, "show")
            call_window(self.window, "restore")
        return {"ok": True}

    def hide_window(self) -> dict[str, object]:
        if self.window is not None:
            call_window(self.window, "hide")
        return {"ok": True}

    def quit_app(self) -> dict[str, object]:
        self.quitting = True
        if self.quit_callback is not None:
            self.quit_callback()
            return {"ok": True}
        if self.window is not None:
            call_window(self.window, "destroy")
        return {"ok": True}


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
            js_api=self.bridge,
            width=720,
            height=860,
            min_size=(420, 520),
            hidden=not self.wsl_mode,
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
        if self.bridge.window is not None:
            call_window(self.bridge.window, "hide")
        return False

    def _start_tray(self) -> bool:
        try:
            import pystray

            self.tray_icon = pystray.Icon(
                "tomo",
                icon=create_tray_image(),
                title="Tomo",
                menu=pystray.Menu(
                    pystray.MenuItem("Open Tomo", self._open_from_tray, default=True),
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

    def _open_from_tray(self, *_: object) -> None:
        self.bridge.show_window()

    def _quit_from_tray(self, *_: object) -> None:
        self._quit()

    def _quit(self) -> None:
        self.bridge.quitting = True
        if self.tray_icon is not None:
            call_window(self.tray_icon, "stop")
        if self.bridge.window is not None:
            call_window(self.bridge.window, "destroy")


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
.composer {{
  border-top: 1px solid var(--line);
  background: var(--panel);
  padding: 12px;
  display: grid;
  grid-template-columns: 1fr auto;
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
      <form class="composer" id="composer">
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
let busy = false;
let pendingApproval = null;
let streamingBubble = null;

function api() {{ return window.pywebview.api; }}
function setBusy(value) {{
  busy = value;
  state.textContent = value ? 'Working' : pendingApproval ? 'Approval' : 'Ready';
  state.classList.toggle('busy', value || !!pendingApproval);
  send.disabled = value || !!pendingApproval;
  input.disabled = !!pendingApproval;
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
approve.addEventListener('click', () => pendingApproval && api().resolve_approval(pendingApproval, true));
deny.addEventListener('click', () => pendingApproval && api().resolve_approval(pendingApproval, false));
window.addEventListener('pywebviewready', async () => {{
  const data = await api().bootstrap();
  if (data.ok) {{
    meta.textContent = `${{data.model}} - ${{data.session.name}} - ${{data.session.id.slice(0, 8)}}`;
    for (const message of data.messages) addMessage(message.role === 'user' ? 'user' : 'assistant', message.text, message.images || []);
    setBusy(data.busy);
  }}
  poll();
}});
</script>
</body>
</html>
"""
