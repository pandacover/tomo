from __future__ import annotations

from pathlib import Path

import json
import os
import queue
import logging
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from functools import cache
from typing import Any, NamedTuple
from uuid import uuid4

from .config import settings
from .cross_gateway_bridge import get_cross_gateway_bridge
from .gateway import TomoGateway, UserContent, format_tool_input
from .reasoning import effective_show_reasoning_trace
from .speech import WindowsSpeechInput
from .session_store import save_session
from .tools import ApprovalRequest

DESKTOP_CHANNEL_ID = "desktop:local"
FLYOUT_WIDTH = 420
FLYOUT_INITIAL_HEIGHT = 132
FLYOUT_MAX_HEIGHT = 700
FLYOUT_MIN_HEIGHT = 96
FLYOUT_MARGIN = 12
VOICE_AUTO_SEND_DELAY_SECONDS = 3.0
VOICE_HOTKEY_ID = 0x544F
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
DESKTOP_PID_FILENAME = "desktop.pid"
DESKTOP_LOG_FILENAME = "desktop.log"
DesktopEvent = dict[str, Any]
LOGGER = logging.getLogger("tomo.desktop")


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
        self.emit(
            {"type": "approval_resolved", "id": approval_id, "approved": bool(approved)}
        )
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
        self._pending_message_images: list[str] = []
        self._flyout_height = FLYOUT_INITIAL_HEIGHT
        self._flyout_geometry: dict[str, int] = {}
        self._lock = threading.Lock()
        get_cross_gateway_bridge().attach(
            "desktop",
            self,
            default_channel_id=self.channel_id,
            channels=[self.channel_id],
        )

    def deliver_cross_gateway_message(self, channel_id: str, text: str, *, source_gateway: str) -> None:
        display_text = f"[from {source_gateway}] {text}"
        session = self.tomo.get_session(channel_id)
        session.messages.append({"role": "assistant", "content": display_text})
        save_session(session)
        event: DesktopEvent = {
            "type": "cross_gateway_message",
            "channel_id": channel_id,
            "source": source_gateway,
            "text": text,
        }
        LOGGER.info(
            "bridge.cross_gateway_message source=%s channel=%s chars=%s",
            source_gateway,
            channel_id,
            len(text),
        )
        self.emitter(event)
        self._push_event_to_ui(event)
        self.show_window()

    def _push_event_to_ui(self, event: DesktopEvent) -> None:
        if self.window is None:
            return
        payload = json.dumps(event, ensure_ascii=True)
        evaluate_js(
            self.window,
            f"window.__tomoDispatchEvent && window.__tomoDispatchEvent({payload})",
        )

    def get_cross_gateway_context(self, channel_id: str) -> dict[str, object]:
        session = self.tomo.get_session(channel_id)
        return {
            "session": asdict(session.metadata),
            "messages": session.messages,
        }

    def list_cross_gateway_channels(self) -> list[str]:
        return [self.channel_id]

    def set_window(self, window: object) -> None:
        LOGGER.info("bridge.set_window window=%s", type(window).__name__)
        self.window = window

    def bootstrap(self) -> dict[str, object]:
        LOGGER.info("bridge.bootstrap busy=%s voice_state=%s", self.busy, self.voice_state)
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
        events = self.emitter.drain()
        if events:
            LOGGER.info("bridge.poll_events count=%s types=%s", len(events), [event.get("type") for event in events])
        return events

    def set_pending_message_images(self, images: list[str] | None) -> dict[str, object]:
        self._pending_message_images = normalize_message_images(images)
        LOGGER.info("bridge.set_pending_message_images count=%s", len(self._pending_message_images))
        return {"ok": True}

    def send_message(self, text: str, images: list[str] | None = None) -> dict[str, object]:
        text = text.strip()
        normalized_images = normalize_message_images(images)
        if not text and not normalized_images:
            return {"ok": False, "error": "Message cannot be empty."}
        with self._lock:
            if self.busy:
                return {"ok": False, "error": "Tomo is still working."}
            self.busy = True
        display_text = text or ("attached images" if normalized_images else "")
        content = build_user_content(text, normalized_images)
        LOGGER.info(
            "bridge.send_message accepted chars=%s images=%s",
            len(text),
            len(normalized_images),
        )
        self.emitter({"type": "busy", "busy": True})
        self.emitter(
            {
                "type": "user_message",
                "text": display_text,
                "images": normalized_images,
            }
        )
        thread = threading.Thread(target=self._send_worker, args=(content,), daemon=True)
        thread.start()
        return {"ok": True}

    def _send_worker(self, content: UserContent) -> None:
        try:
            LOGGER.info("bridge.worker.start multimodal=%s", isinstance(content, list))
            reply = self.tomo.send_user_content_with_events(
                self.channel_id,
                content,
                on_event=lambda event: self.emitter(
                    {
                        "type": "tool_event",
                        "name": event.name,
                        "input": format_tool_input(event.input),
                    }
                ),
                on_text_delta=lambda delta: self.emitter(
                    {"type": "assistant_delta", "text": delta}
                ),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("bridge.worker.error")
            self.emitter({"type": "error", "message": str(exc)})
        else:
            LOGGER.info("bridge.worker.reply text_chars=%s images=%s", len(reply.text), len(reply.images))
            trace_overrides = getattr(self.tomo, "channel_trace_override", {})
            if effective_show_reasoning_trace(
                chat_override=trace_overrides.get(self.channel_id)
                if isinstance(trace_overrides, dict)
                else None
            ) and reply.trace.reasoning_summary:
                self.emitter(
                    {
                        "type": "reasoning_event",
                        "text": reply.trace.reasoning_summary,
                    }
                )
            self.emitter(
                {
                    "type": "assistant_message",
                    "text": reply.text,
                    "images": list(reply.images),
                }
            )
        finally:
            with self._lock:
                self.busy = False
            LOGGER.info("bridge.worker.done")
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

    def toggle_voice_input(self) -> dict[str, object]:
        if self.voice_state == "listening":
            return self.cancel_voice_input()
        return self.start_voice_input()

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

    def resize_flyout(self, height: int | float) -> dict[str, object]:
        if self.window is None:
            LOGGER.warning("bridge.resize_flyout rejected window_unavailable requested=%r", height)
            return {"ok": False, "error": "Window is unavailable."}
        try:
            requested_height = int(height)
        except (TypeError, ValueError):
            LOGGER.warning("bridge.resize_flyout rejected invalid_height=%r", height)
            return {"ok": False, "error": "Invalid flyout height."}
        actual_height = position_flyout_window(
            self.window,
            requested_height,
            geometry=self._flyout_geometry,
        )
        self._flyout_height = actual_height
        LOGGER.info("bridge.resize_flyout requested=%s actual=%s", requested_height, actual_height)
        return {"ok": True, "height": actual_height}

    def show_window(self) -> dict[str, object]:
        LOGGER.info("bridge.show_window window_available=%s height=%s", self.window is not None, self._flyout_height)
        if self.window is not None:
            position_flyout_window(
                self.window,
                self._flyout_height,
                geometry=self._flyout_geometry,
            )
            call_window(self.window, "show")
            call_window(self.window, "restore")
            call_window(self.window, "focus")
            evaluate_js(self.window, "window.scheduleResize && window.scheduleResize()")
            LOGGER.info("bridge.show_window completed")
        return {"ok": True}

    def hide_window(self) -> dict[str, object]:
        LOGGER.info("bridge.hide_window window_available=%s", self.window is not None)
        if self.window is not None:
            call_window(self.window, "hide")
        return {"ok": True}

    def quit_app(self) -> dict[str, object]:
        LOGGER.info("bridge.quit_app")
        self.quitting = True
        self._shutdown_speech_input()
        if self.quit_callback is not None:
            self.quit_callback()
            return {"ok": True}
        if self.window is not None:
            call_window(self.window, "destroy")
        return {"ok": True}

    def _handle_voice_state(self, state: str) -> None:
        LOGGER.info("bridge.voice_state state=%s", state)
        self.voice_state = state
        self.emitter({"type": "voice_state", "state": state})

    def _handle_voice_partial(self, text: str) -> None:
        self.emitter({"type": "voice_partial", "text": text})

    def _handle_voice_final(self, text: str) -> None:
        text = text.strip()
        if not text and not self._pending_message_images:
            self._handle_voice_state("idle")
            return
        self._cancel_pending_voice_send()
        self._pending_voice_text = text
        self._handle_voice_state("sending")
        self.emitter(
            {
                "type": "voice_final",
                "text": text,
                "send_delay": VOICE_AUTO_SEND_DELAY_SECONDS,
            }
        )
        self._voice_send_timer = threading.Timer(
            VOICE_AUTO_SEND_DELAY_SECONDS, self._send_pending_voice_text
        )
        self._voice_send_timer.daemon = True
        self._voice_send_timer.start()

    def _handle_voice_error(self, message: str) -> None:
        LOGGER.warning("bridge.voice_error message=%s", message)
        self._cancel_pending_voice_send()
        self.voice_state = "idle"
        self.emitter({"type": "voice_error", "message": message})
        self.emitter({"type": "voice_state", "state": "idle"})

    def _send_pending_voice_text(self) -> None:
        with self._lock:
            text = self._pending_voice_text
            images = list(self._pending_message_images)
            self._pending_voice_text = ""
            self._pending_message_images = []
            self._voice_send_timer = None
        if not text and not images:
            return
        self._handle_voice_state("idle")
        result = self.send_message(text, images or None)
        if not result.get("ok"):
            self.emitter(
                {
                    "type": "voice_error",
                    "message": str(result.get("error", "Unable to send voice input.")),
                }
            )
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

    def send_message(self, text: str, images: list[str] | None = None) -> dict[str, object]:
        return self._bridge.send_message(text, images)

    def set_pending_message_images(self, images: list[str] | None) -> dict[str, object]:
        return self._bridge.set_pending_message_images(images)

    def resolve_approval(self, approval_id: str, approved: bool) -> dict[str, object]:
        return self._bridge.resolve_approval(approval_id, approved)

    def start_voice_input(self) -> dict[str, object]:
        return self._bridge.start_voice_input()

    def toggle_voice_input(self) -> dict[str, object]:
        return self._bridge.toggle_voice_input()

    def stop_voice_input(self) -> dict[str, object]:
        return self._bridge.stop_voice_input()

    def cancel_voice_input(self) -> dict[str, object]:
        return self._bridge.cancel_voice_input()

    def resize_flyout(self, height: int | float) -> dict[str, object]:
        return self._bridge.resize_flyout(height)

    def show_window(self) -> dict[str, object]:
        return self._bridge.show_window()

    def hide_window(self) -> dict[str, object]:
        return self._bridge.hide_window()

    def quit_app(self) -> dict[str, object]:
        return self._bridge.quit_app()

    def log_client_event(self, message: str, details: object | None = None) -> dict[str, object]:
        LOGGER.info("client.%s details=%r", message, details)
        return {"ok": True}


class DesktopApp:
    def __init__(
        self, bridge: DesktopBridge | None = None, *, wsl_mode: bool | None = None
    ) -> None:
        self.bridge = bridge or DesktopBridge()
        self.bridge.quit_callback = self._quit
        self.tray_icon: object | None = None
        self.hotkey: GlobalVoiceHotkey | None = None
        self.wsl_mode = is_wsl() if wsl_mode is None else wsl_mode

    def run(self) -> None:
        configure_desktop_logging()
        LOGGER.info("app.run start pid=%s wsl_mode=%s", os.getpid(), self.wsl_mode)
        os.environ.setdefault("QT_API", "pyside6")
        validate_qt_backend()
        import webview

        options = self._window_options()
        LOGGER.info(
            "app.create_window hidden=%s frameless=%s transparent=%s size=%sx%s html_chars=%s",
            options.get("hidden"),
            options.get("frameless"),
            options.get("transparent"),
            options.get("width"),
            options.get("height"),
            len(str(options.get("html", ""))),
        )
        window = webview.create_window("Tomo", **options)
        self.bridge.set_window(window)
        window.events.closing += self.on_window_closing
        window.events.shown += self._on_window_shown
        LOGGER.info("app.webview.start")
        webview.start(self._on_webview_started, window, gui="qt")
        LOGGER.info("app.webview.stopped")

    def _window_options(self) -> dict[str, object]:
        if self.wsl_mode:
            return {
                "html": DESKTOP_HTML,
                "js_api": DesktopApi(self.bridge),
                "width": 720,
                "height": 860,
                "min_size": (420, 120),
                "hidden": False,
                "resizable": True,
                "frameless": False,
                "transparent": False,
                "easy_drag": False,
                "draggable": False,
                "maximized": False,
                "background_color": "#FFFFFF",
                "text_select": True,
            }
        return {
            "html": DESKTOP_HTML,
            "js_api": DesktopApi(self.bridge),
            "width": FLYOUT_WIDTH,
            "height": FLYOUT_INITIAL_HEIGHT,
            "min_size": (320, FLYOUT_MIN_HEIGHT),
            "hidden": True,
            "resizable": False,
            "frameless": True,
            "transparent": True,
            "easy_drag": False,
            "draggable": False,
            "maximized": False,
            "background_color": "#000000",
            "text_select": True,
        }

    def _on_webview_started(self, window: object) -> None:
        LOGGER.info("app.webview.started callback")
        self.bridge.set_window(window)
        if self.wsl_mode:
            print(
                "Tomo desktop is running in WSL window mode; close the window to quit."
            )
            self.bridge.quitting = True
            return
        self._position_flyout()
        self._start_tray()
        self._start_hotkey()

    def on_window_closing(self, *_: object) -> bool:
        LOGGER.info("app.window.closing wsl_mode=%s quitting=%s", self.wsl_mode, self.bridge.quitting)
        if self.wsl_mode:
            self.bridge.quitting = True
            return True
        if self.bridge.quitting:
            return True
        self.bridge.hide_window()
        return False

    def _on_window_shown(self, *_: object) -> None:
        LOGGER.info("app.window.shown")
        if self.bridge.window is not None:
            evaluate_js(self.bridge.window, "window.scheduleResize && window.scheduleResize()")

    def _start_tray(self) -> bool:
        try:
            import pystray

            self.tray_icon = pystray.Icon(
                "tomo",
                icon=create_tray_image(),
                title="Tomo",
                menu=pystray.Menu(
                    pystray.MenuItem(
                        "Open Tomo", self._show_flyout_from_tray, default=True
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Quit", self._quit_from_tray),
                ),
            )
            self.tray_icon.run_detached()
            LOGGER.info("app.tray.started")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("app.tray.error")
            if self.wsl_mode:
                print(
                    f"Tomo desktop tray integration is unavailable in this WSL session: {exc}"
                )
                return False
            raise
        return True

    def _show_flyout_from_tray(self, *_: object) -> None:
        LOGGER.info("app.tray.open_clicked")
        self.bridge.show_window()

    def _toggle_voice_from_hotkey(self) -> dict[str, object]:
        LOGGER.info("app.hotkey.toggle_voice")
        self.bridge.show_window()
        return self.bridge.toggle_voice_input()

    def _position_flyout(self) -> None:
        if self.bridge.window is None:
            LOGGER.warning("app.position_flyout skipped window_unavailable")
            return
        LOGGER.info("app.position_flyout height=%s", self.bridge._flyout_height)
        position_flyout_window(
            self.bridge.window,
            self.bridge._flyout_height,
            geometry=self.bridge._flyout_geometry,
        )

    def _quit_from_tray(self, *_: object) -> None:
        LOGGER.info("app.tray.quit_clicked")
        self._quit()

    def _quit(self) -> None:
        LOGGER.info("app.quit")
        self.bridge.quitting = True
        if self.hotkey is not None:
            self.hotkey.stop()
        self.bridge._shutdown_speech_input()
        if self.bridge.window is not None:
            call_window(self.bridge.window, "destroy")
        if self.tray_icon is not None:
            call_window(self.tray_icon, "stop")

    def _start_hotkey(self) -> bool:
        self.hotkey = GlobalVoiceHotkey(self._toggle_voice_from_hotkey)
        try:
            started = self.hotkey.start()
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("app.hotkey.error")
            print(f"Tomo desktop hotkey Ctrl+Space is unavailable: {exc}")
            return False
        if not started:
            LOGGER.warning("app.hotkey.unavailable error=%s", self.hotkey.error or "registration failed")
            print(
                "Tomo desktop hotkey Ctrl+Space is unavailable: "
                f"{self.hotkey.error or 'registration failed'}"
            )
        return started


class GlobalVoiceHotkey:
    def __init__(self, callback: Callable[[], object]) -> None:
        self.callback = callback
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.error = ""
        self._started = threading.Event()
        self._registered = False

    def start(self) -> bool:
        if os.name != "nt":
            self.error = "global hotkeys are only available on native Windows"
            return False
        if self.thread is not None:
            return self._registered
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._started.wait(timeout=1)
        return self._registered

    def stop(self) -> None:
        if os.name != "nt" or self.thread_id == 0:
            return
        import ctypes

        ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetLastError.restype = wintypes.DWORD
        self.thread_id = kernel32.GetCurrentThreadId()

        modifiers = MOD_CONTROL | MOD_NOREPEAT
        self._registered = bool(
            user32.RegisterHotKey(None, VOICE_HOTKEY_ID, modifiers, VK_SPACE)
        )
        if not self._registered:
            self.error = ctypes.FormatError(kernel32.GetLastError())
            self._started.set()
            return
        self._started.set()

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY and msg.wParam == VOICE_HOTKEY_ID:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, VOICE_HOTKEY_ID)


def normalize_message_images(images: list[str] | None) -> list[str]:
    if not images:
        return []
    normalized: list[str] = []
    for image in images:
        if not isinstance(image, str):
            continue
        url = image.strip()
        if url:
            normalized.append(url)
    return normalized


def build_user_content(text: str, images: list[str]) -> UserContent:
    if not images:
        return text
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for url in images:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def message_to_dto(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role", "assistant"))
    content = message.get("content", "")
    if isinstance(content, str):
        return {"role": role, "text": content, "images": []}
    return {
        "role": role,
        "text": structured_content_text(content),
        "images": structured_content_images(content),
    }


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
        LOGGER.info("window.%s begin", method_name)
        try:
            method()
        except Exception:  # noqa: BLE001
            LOGGER.exception("window.%s error", method_name)
            raise
        LOGGER.info("window.%s done", method_name)
        return
    LOGGER.warning("window.%s unavailable target=%s", method_name, type(target).__name__)


def move_window(window: object, x: int, y: int) -> None:
    method = getattr(window, "move", None)
    if callable(method):
        LOGGER.info("window.move x=%s y=%s", x, y)
        method(x, y)
        return
    LOGGER.warning("window.move unavailable target=%s", type(window).__name__)


def resize_window(window: object, width: int, height: int) -> None:
    method = getattr(window, "resize", None)
    if callable(method):
        LOGGER.info("window.resize width=%s height=%s", width, height)
        method(width, height)
        return
    LOGGER.warning("window.resize unavailable target=%s", type(window).__name__)


def evaluate_js(window: object, script: str) -> None:
    method = getattr(window, "evaluate_js", None)
    if callable(method):
        try:
            LOGGER.info("window.evaluate_js script=%s", script)
            method(script)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("window.evaluate_js ignored_error script=%s", script)
            print(f"Tomo desktop ignored JavaScript evaluation error: {exc}")
        return
    LOGGER.warning("window.evaluate_js unavailable target=%s", type(window).__name__)


def clamp_flyout_height(height: int, work_area: Rect) -> int:
    available_height = max(
        FLYOUT_MIN_HEIGHT, work_area.bottom - work_area.top - (FLYOUT_MARGIN * 2)
    )
    return min(FLYOUT_MAX_HEIGHT, available_height, max(FLYOUT_MIN_HEIGHT, height))


def position_flyout_window(
    window: object,
    requested_height: int,
    *,
    geometry: dict[str, int] | None = None,
) -> int:
    work_area = get_windows_work_area()
    width = min(
        FLYOUT_WIDTH,
        max(320, work_area.right - work_area.left - (FLYOUT_MARGIN * 2)),
    )
    height = clamp_flyout_height(requested_height, work_area)
    work_width = work_area.right - work_area.left
    work_height = work_area.bottom - work_area.top
    x = work_area.left + (work_width - width) // 2
    y = work_area.top + (work_height - height) // 2
    if geometry is not None:
        unchanged = (
            geometry.get("width") == width
            and geometry.get("height") == height
            and geometry.get("x") == x
            and geometry.get("y") == y
        )
        if unchanged:
            LOGGER.debug(
                "window.position skipped unchanged width=%s height=%s x=%s y=%s",
                width,
                height,
                x,
                y,
            )
            return height
    LOGGER.info(
        "window.position requested_height=%s actual_width=%s actual_height=%s x=%s y=%s work_area=%s",
        requested_height,
        width,
        height,
        x,
        y,
        work_area,
    )
    resize_window(window, width, height)
    move_window(window, x, y)
    if geometry is not None:
        geometry.update(width=width, height=height, x=x, y=y)
    return height


def get_windows_work_area() -> Rect:
    if os.name != "nt":
        return Rect(0, 0, 1920, 1080)
    import ctypes
    from ctypes import wintypes

    work_area = wintypes.RECT()
    spi_getworkarea = 0x0030
    if ctypes.windll.user32.SystemParametersInfoW(
        spi_getworkarea, 0, ctypes.byref(work_area), 0
    ):
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


def configure_desktop_logging() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(process)d:%(threadName)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(desktop_log_path(), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    LOGGER.info("logging.configured log_path=%s", desktop_log_path())


def start_desktop() -> None:
    pid_path = desktop_pid_path()
    existing_pid = read_pid(pid_path)
    if existing_pid is not None and process_is_running(existing_pid):
        print(f"Tomo desktop is already running with PID {existing_pid}.")
        return
    if existing_pid is not None:
        pid_path.unlink(missing_ok=True)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log_path = desktop_log_path()
    log_file = log_path.open("ab")
    log_file.write(
        f"\n--- Tomo desktop start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode(
            "utf-8"
        )
    )
    log_file.flush()
    process = subprocess.Popen(
        [sys.executable, "-c", "from tomo.desktop import run_desktop; run_desktop()"],
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=background_creation_flags(),
        start_new_session=os.name != "nt",
    )
    log_file.close()
    write_pid(pid_path, process.pid)
    print(f"Tomo desktop started with PID {process.pid}.")
    print(f"Logs: {log_path}")


def stop_desktop() -> None:
    pid_path = desktop_pid_path()
    pid = read_pid(pid_path)
    if pid is None:
        orphan_pids = find_desktop_process_ids()
        if not orphan_pids:
            print("Tomo desktop is not running.")
            return
        stop_desktop_pids(orphan_pids)
        print("Tomo desktop stopped.")
        return
    if not process_is_running(pid):
        pid_path.unlink(missing_ok=True)
        orphan_pids = find_desktop_process_ids()
        if not orphan_pids:
            print("Tomo desktop was not running; removed stale PID file.")
            return
        stop_desktop_pids(orphan_pids)
        print("Tomo desktop stopped; removed stale PID file.")
        return

    stop_process(pid, force=False)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            pid_path.unlink(missing_ok=True)
            print("Tomo desktop stopped.")
            return
        time.sleep(0.1)

    stop_process(pid, force=True)
    pid_path.unlink(missing_ok=True)
    print("Tomo desktop stopped forcefully.")


def stop_desktop_pids(pids: list[int]) -> None:
    current_pid = os.getpid()
    for pid in pids:
        if pid == current_pid or not process_is_running(pid):
            continue
        stop_process(pid, force=True)


def find_desktop_process_ids() -> list[int]:
    if os.name != "nt":
        return []

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like '*from tomo.desktop import run_desktop*' } | "
            "Select-Object -ExpandProperty ProcessId"
        ),
    ]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=10,
            shell=False,
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("desktop.find_process_ids.error")
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid != os.getpid():
            pids.append(pid)
    return sorted(set(pids))


def restart_desktop() -> None:
    stop_desktop()
    start_desktop()


def desktop_pid_path() -> Path:
    return settings.data_dir / DESKTOP_PID_FILENAME


def desktop_log_path() -> Path:
    return settings.data_dir / DESKTOP_LOG_FILENAME


def background_creation_flags() -> int:
    if os.name != "nt":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return flags | create_no_window


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def stop_process(pid: int, *, force: bool) -> None:
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(command, text=True, capture_output=True, timeout=30)
        return
    import signal

    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        if os.name == "nt":
            return False
        raise
    return True


def validate_qt_backend() -> None:
    missing: list[str] = []
    for module_name in ("qtpy", "PySide6", "PySide6.QtWebEngineWidgets"):
        try:
            __import__(module_name)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{module_name}: {exc}")
    if missing:
        details = "\n".join(f"- {item}" for item in missing)
        raise RuntimeError(
            "Tomo desktop needs the Qt pywebview backend for transparent flyout rendering.\n"
            "Run `uv sync` to install the Python dependencies. If that still fails, WSL may be missing "
            "Linux GUI system packages required by Qt/WebEngine.\n\n"
            f"Missing backend imports:\n{details}"
        )


def validate_wsl_qt_backend() -> None:
    validate_qt_backend()


@cache
def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        version = open("/proc/version", encoding="utf-8").read().lower()
    except OSError:
        return False
    return "microsoft" in version or "wsl" in version

# The desktop UI now prefers the React/Vite build in desktop_dist, while keeping
# desktop.html as a migration fallback when the frontend has not been built.
_DESKTOP_DIST_INDEX = Path(__file__).with_name("desktop_dist").joinpath("index.html")
_DESKTOP_HTML_PATH = Path(__file__).with_name("desktop.html")


def load_desktop_html() -> str:
    if _DESKTOP_DIST_INDEX.exists():
        return inline_desktop_dist_html(_DESKTOP_DIST_INDEX)
    return _DESKTOP_HTML_PATH.read_text(encoding="utf-8")


def inline_desktop_dist_html(index_path: Path) -> str:
    html = index_path.read_text(encoding="utf-8")
    dist_dir = index_path.parent

    def inline_script(match: re.Match[str]) -> str:
        src = match.group("src")
        script_path = (dist_dir / src).resolve()
        if not script_path.is_file() or not script_path.is_relative_to(dist_dir.resolve()):
            return match.group(0)
        script = script_path.read_text(encoding="utf-8")
        return f'<script type="module">{script}</script>'

    def inline_stylesheet(match: re.Match[str]) -> str:
        href = match.group("href")
        stylesheet_path = (dist_dir / href).resolve()
        if not stylesheet_path.is_file() or not stylesheet_path.is_relative_to(dist_dir.resolve()):
            return match.group(0)
        stylesheet = stylesheet_path.read_text(encoding="utf-8")
        return f"<style>{stylesheet}</style>"

    html = re.sub(
        r'<script\b(?=[^>]*\bsrc="(?P<src>\./assets/[^"]+\.js)")(?=[^>]*\btype="module")[^>]*></script>',
        inline_script,
        html,
    )
    html = re.sub(
        r'<link\b(?=[^>]*\bhref="(?P<href>\./assets/[^"]+\.css)")(?=[^>]*\brel="stylesheet")[^>]*>',
        inline_stylesheet,
        html,
    )
    return html


DESKTOP_HTML: str = load_desktop_html()

