from __future__ import annotations

import os
import queue
import math
import re
import signal
import subprocess
import sys
import threading
import time
from base64 import b64decode, b64encode
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from .config import settings
from .cross_gateway_bridge import get_cross_gateway_bridge
from .gateway import ToolCallLifecycleEvent, TomoGateway, UserContent, format_tool_input
from .reasoning import (
    effective_reasoning_effort,
    effective_show_reasoning_trace,
    format_reasoning_status,
    parse_reasoning_command_args,
    reasoning_usage_message,
)
from .slash_commands import command_argument, slash_prefix, telegram_bot_commands, unrecognized_message
from .session_store import ChatSession
from .telegram_config import parse_allowed_chat_ids, resolved_telegram_config
from .token_store import ensure_logged_in, load_tokens
from .tools import ApprovalRequest


APPROVE_WORDS = {"y", "yes", "approve", "/approve"}
DENY_WORDS = {"", "n", "no", "deny", "/deny"}
MAX_TELEGRAM_MESSAGE = 4096
TELEGRAM_TYPING_INTERVAL_SECONDS = 5
TELEGRAM_STILL_WORKING_INTERVAL_SECONDS = 30
YOLO_ENABLED_MESSAGE = "YOLO mode enabled for approval prompts. Dotfile deny rules still apply."
YOLO_STATUS_ENABLED = "YOLO mode is enabled for approval prompts. Dotfile deny rules still apply."
TELEGRAM_PID_FILENAME = "telegram.pid"
TELEGRAM_LOG_FILENAME = "telegram.log"
DEFAULT_LOCATION_WATCH_RADIUS_METERS = 500.0


@dataclass
class PendingTelegramApproval:
    request: ApprovalRequest
    answers: queue.Queue[bool] = field(default_factory=queue.Queue)


@dataclass(frozen=True)
class TelegramLocation:
    latitude: float
    longitude: float
    live_period: int | None = None
    horizontal_accuracy: float | None = None
    heading: int | None = None
    proximity_alert_radius: int | None = None

    @property
    def kind(self) -> str:
        return "live" if self.live_period is not None else "static"

    def context_text(self) -> str:
        parts = [
            f"latitude {self.latitude}",
            f"longitude {self.longitude}",
        ]
        if self.horizontal_accuracy is not None:
            parts.append(f"horizontal accuracy {self.horizontal_accuracy} meters")
        if self.heading is not None:
            parts.append(f"heading {self.heading} degrees")
        if self.proximity_alert_radius is not None:
            parts.append(f"proximity alert radius {self.proximity_alert_radius} meters")
        if self.live_period is not None:
            parts.append(f"live period {self.live_period} seconds")
        return f"Telegram pinned location ({self.kind}): " + ", ".join(parts) + "."


@dataclass(frozen=True)
class LocationWatch:
    label: str
    target: TelegramLocation
    radius_meters: float = DEFAULT_LOCATION_WATCH_RADIUS_METERS


@dataclass(frozen=True)
class MovementWatch:
    origin: TelegramLocation
    threshold_meters: float


@dataclass
class TelegramTraceBlock:
    chat_id: str
    gateway: TelegramGateway
    full_tool_input: bool = False
    tool_events: list[ToolCallLifecycleEvent] = field(default_factory=list)
    message_id: int | None = None

    def add_tool_event(self, event: ToolCallLifecycleEvent) -> None:
        self.tool_events.append(event)
        self.publish_tool_block()

    def publish_tool_block(self) -> None:
        text = fit_editable_message(render_tool_events_tree(self.tool_events, full_input=self.full_tool_input))
        if self.message_id is None:
            self.message_id = self.gateway.send_message_blob(self.chat_id, text)
            return
        self.gateway.edit_message_blob(self.chat_id, self.message_id, text)

    def send_reasoning(self, summary: str) -> None:
        self.gateway.send_message(self.chat_id, render_reasoning_tree(summary))

    def send_tool_errors(self, errors: tuple[str, ...]) -> None:
        if errors:
            self.gateway.send_message(self.chat_id, render_tool_errors_tree(errors))


class TelegramGateway:
    def __init__(
        self,
        token: str,
        *,
        allowed_chat_ids: Iterable[int] | None = None,
        client: httpx.Client | None = None,
        tomo: TomoGateway | None = None,
    ) -> None:
        self.token = token
        self.allowed_chat_ids = set(allowed_chat_ids or [])
        self.client = client or httpx.Client(timeout=60)
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.tomo = tomo or TomoGateway(responder=self)
        self.pending_approvals: dict[str, PendingTelegramApproval] = {}
        self.pending_session_choices: dict[str, list[ChatSession]] = {}
        self.busy_chats: set[str] = set()
        self.yolo_chats: set[str] = set()
        self.debug_tool_chats: set[str] = set()
        self.chat_locations: dict[str, TelegramLocation] = {}
        self.location_watches: dict[str, list[LocationWatch]] = {}
        self.movement_watches: dict[str, list[MovementWatch]] = {}
        channels = self._cross_gateway_channels()
        get_cross_gateway_bridge().attach(
            "telegram",
            self,
            default_channel_id=channels[0] if channels else "telegram:unknown",
            channels=channels,
        )

    def _cross_gateway_channels(self) -> list[str]:
        allowed = [str(chat_id) for chat_id in sorted(self.allowed_chat_ids)]
        if allowed:
            return allowed
        sessions = getattr(self.tomo, "sessions", {})
        if isinstance(sessions, dict):
            return sorted(str(channel_id) for channel_id in sessions)
        return []

    def deliver_cross_gateway_message(self, channel_id: str, text: str, *, source_gateway: str) -> None:
        self.send_message(channel_id, f"[from {source_gateway}] {text}")

    def get_cross_gateway_context(self, channel_id: str) -> dict[str, object]:
        session = self.tomo.get_session(channel_id)
        return {
            "session": asdict(session.metadata),
            "messages": session.messages,
        }

    def list_cross_gateway_channels(self) -> list[str]:
        sessions = getattr(self.tomo, "sessions", {})
        session_channels = sorted(str(channel_id) for channel_id in sessions) if isinstance(sessions, dict) else []
        return list(dict.fromkeys([*self._cross_gateway_channels(), *session_channels]))

    def run(self) -> None:
        offset: int | None = None
        try:
            self.register_bot_commands()
            self.send_startup_notice()
            while True:
                params: dict[str, object] = {"timeout": 50}
                if offset is not None:
                    params["offset"] = offset
                updates = self.api("getUpdates", params).get("result", [])
                for update in updates:
                    offset = max(offset or 0, int(update["update_id"]) + 1)
                    self.handle_update(update)
        except KeyboardInterrupt:
            print("Telegram gateway stopped.")
        finally:
            self.close()

    def close(self) -> None:
        self.client.close()

    def register_bot_commands(self) -> None:
        self.api("setMyCommands", {"commands": telegram_bot_commands()})

    def send_startup_notice(self) -> None:
        for chat_id in self.allowed_chat_ids:
            self.send_message(str(chat_id), "Tomo Telegram gateway is online.")

    def handle_update(self, update: dict[str, object]) -> None:
        message = update.get("message")
        edited_message = update.get("edited_message")
        if not isinstance(message, dict) and isinstance(edited_message, dict):
            self.handle_edited_message(edited_message)
            return
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            return
        chat_id = str(chat["id"])
        if self.allowed_chat_ids and int(chat_id) not in self.allowed_chat_ids:
            self.send_message(chat_id, "This chat is not allowed to use Tomo.")
            return
        location = telegram_location(message.get("location"))
        if location is not None:
            self.chat_locations[chat_id] = location
            self.check_location_watches(chat_id, location)
            self.check_movement_watches(chat_id, location)
        content = self.extract_message_content(message)
        if content is None:
            if location is not None:
                self.send_message(chat_id, f"Location pinned for this chat ({location.kind}).")
            return
        if isinstance(content, str):
            self.handle_text(chat_id, content)
            return
        self.handle_user_content(chat_id, self.with_location_context(chat_id, content))

    def handle_edited_message(self, message: dict[str, object]) -> None:
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            return
        chat_id = str(chat["id"])
        if self.allowed_chat_ids and int(chat_id) not in self.allowed_chat_ids:
            return
        location = telegram_location(message.get("location"))
        if location is not None:
            self.chat_locations[chat_id] = location
            self.check_location_watches(chat_id, location)
            self.check_movement_watches(chat_id, location)

    def extract_message_content(self, message: dict[str, object]) -> UserContent | None:
        text = str(message.get("text") or "").strip()
        if text:
            return text

        photos = message.get("photo")
        if not isinstance(photos, list) or not photos:
            return None
        caption = str(message.get("caption") or "").strip() or "Describe this image."
        photo = largest_telegram_photo(photos)
        file_id = photo.get("file_id") if photo is not None else None
        if not isinstance(file_id, str):
            return None
        image_url = self.telegram_image_data_url(file_id)
        return [
            {"type": "text", "text": caption},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]

    def with_location_context(self, chat_id: str, content: UserContent) -> UserContent:
        location = self.chat_locations.get(chat_id)
        if location is None:
            return content
        context = location.context_text()
        if isinstance(content, str):
            return f"{context}\n\nUser message: {content}"
        return [{"type": "text", "text": context}, *content]

    def handle_text(self, chat_id: str, text: str) -> None:
        command = telegram_command(text)
        if command == "/yolo":
            self.handle_yolo_command(chat_id, text)
            return
        if command in {"/debug-tool", "/debug_tool"}:
            self.handle_debug_tool_command(chat_id, text)
            return
        if command == "/reasoning":
            self.handle_reasoning_command(chat_id, text)
            return
        if command == "/session":
            self.handle_session_command(chat_id)
            return
        pending = self.pending_approvals.get(chat_id)
        if pending is not None:
            self.resolve_approval(chat_id, command or text, pending)
            return
        if chat_id in self.pending_session_choices:
            self.resolve_session_selection(chat_id, text)
            return
        if command == "/start":
            self.send_message(chat_id, "Tomo is ready. Send a message to chat.")
            return
        if command == "/cancel":
            if self.cancel_session_selection(chat_id):
                return
            self.send_message(chat_id, "No pending approval to cancel.")
            return
        if command in {"/approve", "/deny"}:
            self.send_message(chat_id, "No pending approval.")
            return
        if command is not None:
            self.send_message(chat_id, unrecognized_message(text, "gateway"))
            return
        if self.handle_location_watch_request(chat_id, text):
            return
        if self.handle_movement_watch_request(chat_id, text):
            return
        if chat_id in self.busy_chats:
            self.send_message(chat_id, "Tomo is still working on the previous message.")
            return

        self.busy_chats.add(chat_id)
        thread = threading.Thread(target=self.reply_worker, args=(chat_id, self.with_location_context(chat_id, text)), daemon=True)
        thread.start()

    def handle_user_content(self, chat_id: str, content: UserContent) -> None:
        if chat_id in self.pending_approvals:
            self.send_message(chat_id, "Reply /approve or /deny.")
            return
        if chat_id in self.pending_session_choices:
            self.send_message(chat_id, "Reply with a session number, or /cancel.")
            return
        if chat_id in self.busy_chats:
            self.send_message(chat_id, "Tomo is still working on the previous message.")
            return

        self.busy_chats.add(chat_id)
        thread = threading.Thread(target=self.reply_worker, args=(chat_id, content), daemon=True)
        thread.start()

    def handle_location_watch_request(self, chat_id: str, text: str) -> bool:
        request = parse_location_watch_request(text)
        if request is None:
            return False
        label, radius_meters = request
        try:
            target = self.resolve_location_watch_target(label)
        except Exception:  # noqa: BLE001
            self.send_message(chat_id, f"I couldn't look up {label} right now. Try again in a bit.")
            return True
        if target is None:
            self.send_message(chat_id, f"I couldn't find a location for {label}. Send a more specific place name.")
            return True
        watch = LocationWatch(label=label, target=target, radius_meters=radius_meters)
        self.location_watches.setdefault(chat_id, []).append(watch)
        distance_text = format_distance(radius_meters)
        if chat_id in self.chat_locations:
            self.check_location_watches(chat_id, self.chat_locations[chat_id])
            if watch not in self.location_watches.get(chat_id, []):
                return True
        self.send_message(chat_id, f"Got it. I'll let you know when you're within {distance_text} of {label}.")
        return True

    def resolve_location_watch_target(self, label: str) -> TelegramLocation | None:
        response = self.client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": label, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": "tomo-telegram-location-watch/1.0"},
            timeout=15,
        )
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list) or not results:
            return None
        result = results[0]
        if not isinstance(result, dict):
            return None
        try:
            return TelegramLocation(latitude=float(result["lat"]), longitude=float(result["lon"]))
        except (KeyError, TypeError, ValueError):
            return None

    def check_location_watches(self, chat_id: str, location: TelegramLocation) -> None:
        watches = self.location_watches.get(chat_id)
        if not watches:
            return
        remaining: list[LocationWatch] = []
        for watch in watches:
            distance = distance_meters(location, watch.target)
            if distance <= watch.radius_meters:
                self.send_message(
                    chat_id,
                    f"You're near {watch.label} now, about {format_distance(distance)} away.",
                )
                continue
            remaining.append(watch)
        if remaining:
            self.location_watches[chat_id] = remaining
        else:
            self.location_watches.pop(chat_id, None)

    def handle_movement_watch_request(self, chat_id: str, text: str) -> bool:
        threshold_meters = parse_movement_watch_request(text)
        if threshold_meters is None:
            return False
        origin = self.chat_locations.get(chat_id)
        if origin is None:
            self.send_message(chat_id, "Share your live location first, then ask me to watch for movement.")
            return True
        watch = MovementWatch(origin=origin, threshold_meters=threshold_meters)
        self.movement_watches.setdefault(chat_id, []).append(watch)
        self.send_message(chat_id, f"Got it. I'll text you when you move at least {format_distance(threshold_meters)}.")
        return True

    def check_movement_watches(self, chat_id: str, location: TelegramLocation) -> None:
        watches = self.movement_watches.get(chat_id)
        if not watches:
            return
        remaining: list[MovementWatch] = []
        for watch in watches:
            distance = distance_meters(watch.origin, location)
            if distance >= watch.threshold_meters:
                self.send_message(chat_id, f"You've moved about {format_distance(distance)}.")
                continue
            remaining.append(watch)
        if remaining:
            self.movement_watches[chat_id] = remaining
        else:
            self.movement_watches.pop(chat_id, None)

    def reply_worker(self, chat_id: str, content: UserContent) -> None:
        done = threading.Event()
        heartbeat = threading.Thread(target=self.work_status_worker, args=(chat_id, done), daemon=True)
        heartbeat.start()
        try:
            trace_block = TelegramTraceBlock(
                chat_id=chat_id,
                gateway=self,
                full_tool_input=chat_id in self.debug_tool_chats,
            )
            if isinstance(content, str):
                reply = self.tomo.send_text_with_events(chat_id, content, on_event=trace_block.add_tool_event)
            else:
                reply = self.tomo.send_user_content_with_events(chat_id, content, on_event=trace_block.add_tool_event)
        except Exception as exc:  # noqa: BLE001
            self.send_message(chat_id, f"Error: {exc}")
        else:
            trace_overrides = getattr(self.tomo, "channel_trace_override", {})
            if effective_show_reasoning_trace(
                chat_override=trace_overrides.get(chat_id) if isinstance(trace_overrides, dict) else None
            ) and reply.trace.reasoning_summary:
                trace_block.send_reasoning(reply.trace.reasoning_summary)
            trace_block.send_tool_errors(reply.trace.tool_errors)
            for image_url in reply.images:
                self.send_photo(chat_id, image_url)
            if reply.text:
                self.send_message(chat_id, reply.text)
        finally:
            done.set()
            self.busy_chats.discard(chat_id)

    def work_status_worker(self, chat_id: str, done: threading.Event) -> None:
        started_at = time.monotonic()
        next_notice_at = started_at + TELEGRAM_STILL_WORKING_INTERVAL_SECONDS
        while not done.wait(TELEGRAM_TYPING_INTERVAL_SECONDS):
            now = time.monotonic()
            self.send_chat_action(chat_id, "typing")
            if now >= next_notice_at:
                elapsed = int(now - started_at)
                minutes, seconds = divmod(elapsed, 60)
                if minutes and seconds:
                    duration = f"{minutes}min {seconds}s"
                elif minutes:
                    duration = f"{minutes}min"
                else:
                    duration = f"{seconds}s"
                self.send_message(chat_id, f"Still working... no final reply after {duration}.")
                next_notice_at += TELEGRAM_STILL_WORKING_INTERVAL_SECONDS

    def request_approval(self, channel_id: str, request: ApprovalRequest) -> bool:
        if channel_id in self.yolo_chats:
            return True
        pending = PendingTelegramApproval(request=request)
        self.pending_approvals[channel_id] = pending
        self.send_message(
            channel_id,
            f"Approval required for {request.operation} {request.target}\n\n{request.reason}\n\nReply /approve or /deny.",
        )
        try:
            return pending.answers.get()
        finally:
            self.pending_approvals.pop(channel_id, None)

    def resolve_approval(self, chat_id: str, text: str, pending: PendingTelegramApproval) -> None:
        answer = text.strip().lower()
        if answer in APPROVE_WORDS:
            pending.answers.put(True)
            self.send_message(chat_id, "Approved.")
            return
        if answer in DENY_WORDS:
            pending.answers.put(False)
            self.send_message(chat_id, "Denied.")
            return
        self.send_message(chat_id, "Reply /approve or /deny.")

    def handle_yolo_command(self, chat_id: str, text: str) -> None:
        argument = command_argument(text)
        if argument is None:
            if chat_id in self.yolo_chats:
                self.send_message(chat_id, YOLO_STATUS_ENABLED)
            else:
                self.send_message(chat_id, "YOLO mode is disabled.")
            return
        if argument == "enable":
            self.yolo_chats.add(chat_id)
            pending = self.pending_approvals.get(chat_id)
            if pending is not None:
                pending.answers.put(True)
                self.send_message(chat_id, f"{YOLO_ENABLED_MESSAGE} Approved pending tool call.")
                return
            self.send_message(chat_id, YOLO_ENABLED_MESSAGE)
            return
        if argument == "disable":
            self.yolo_chats.discard(chat_id)
            self.send_message(chat_id, "YOLO mode disabled.")
            return
        self.send_message(chat_id, "Usage: /yolo enable, /yolo disable, or /yolo.")

    def handle_debug_tool_command(self, chat_id: str, text: str) -> None:
        argument = command_argument(text)
        if argument == "enable":
            self.debug_tool_chats.add(chat_id)
            self.send_message(chat_id, "Tool debug output enabled.")
            return
        if argument == "disable":
            self.debug_tool_chats.discard(chat_id)
            self.send_message(chat_id, "Tool debug output disabled.")
            return
        self.send_message(chat_id, "Usage: /debug-tool enable or /debug-tool disable.")

    def handle_reasoning_command(self, chat_id: str, text: str) -> None:
        action, value = parse_reasoning_command_args(command_argument(text))
        if action == "status":
            effort_map = getattr(self.tomo, "channel_reasoning_effort", {})
            trace_map = getattr(self.tomo, "channel_trace_override", {})
            effort = (effort_map.get(chat_id) if isinstance(effort_map, dict) else None) or effective_reasoning_effort()
            trace = effective_show_reasoning_trace(
                chat_override=trace_map.get(chat_id) if isinstance(trace_map, dict) else None
            )
            self.send_message(chat_id, format_reasoning_status(effort=effort, trace=trace))
            return
        if action == "effort" and value is not None:
            self.tomo.set_channel_reasoning_effort(chat_id, value)
            self.send_message(chat_id, f"Reasoning effort set to {value}. Applies to subsequent messages in this chat.")
            return
        if action == "trace" and value is not None:
            enabled = value == "on"
            self.tomo.set_channel_trace_override(chat_id, enabled)
            self.send_message(chat_id, f"Reasoning trace {'enabled' if enabled else 'disabled'} for this chat.")
            return
        self.send_message(chat_id, reasoning_usage_message())

    def handle_session_command(self, chat_id: str) -> None:
        sessions = self.tomo.list_channel_sessions(chat_id)
        if not sessions:
            self.send_message(chat_id, "No saved sessions.")
            return
        self.pending_session_choices[chat_id] = sessions
        lines = ["Saved sessions:"]
        for index, session in enumerate(sessions, start=1):
            lines.append(f"{index}. {session.metadata.name} - {session.metadata.updated_date} - {session.metadata.id[:8]}")
        lines.append("Reply with a session number to load it, or /cancel.")
        self.send_message(chat_id, "\n".join(lines))

    def resolve_session_selection(self, chat_id: str, text: str) -> None:
        choices = self.pending_session_choices.get(chat_id)
        if not choices:
            return
        stripped = text.strip()
        if telegram_command(stripped) == "/cancel":
            self.cancel_session_selection(chat_id)
            return
        try:
            index = int(stripped)
        except ValueError:
            self.send_message(chat_id, "Reply with a session number, or /cancel.")
            return
        if index < 1 or index > len(choices):
            self.send_message(chat_id, f"Choose a number from 1 to {len(choices)}, or /cancel.")
            return

        selected = self.tomo.set_channel_session(chat_id, choices[index - 1].metadata.id)
        self.pending_session_choices.pop(chat_id, None)
        self.send_message(chat_id, f"Loaded session: {selected.metadata.name} - {selected.metadata.id[:8]}")

    def cancel_session_selection(self, chat_id: str) -> bool:
        if chat_id not in self.pending_session_choices:
            return False
        self.pending_session_choices.pop(chat_id, None)
        self.send_message(chat_id, "Session selection cancelled.")
        return True

    def send_message(self, chat_id: str, text: str) -> list[int]:
        message_ids: list[int] = []
        chunks = split_message(text)
        for chunk in chunks:
            message_ids.append(self.send_message_blob(chat_id, chunk))
        return message_ids

    def send_message_blob(self, chat_id: str, text: str) -> int:
        data = self.api("sendMessage", {"chat_id": chat_id, "text": text})
        result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("message_id"), int):
            return result["message_id"]
        return 0

    def send_photo(self, chat_id: str, image_url: str, caption: str | None = None) -> int:
        if image_url.startswith("data:"):
            media_type, image_bytes = decode_data_url(image_url)
            filename = f"image.{extension_for_media_type(media_type)}"
            data: dict[str, object] = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
            response = self.client.post(
                f"{self.api_url}/sendPhoto",
                data=data,
                files={"photo": (filename, image_bytes, media_type)},
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API error calling sendPhoto: {payload}")
            result = payload.get("result")
        else:
            payload: dict[str, object] = {"chat_id": chat_id, "photo": image_url}
            if caption:
                payload["caption"] = caption
            data = self.api("sendPhoto", payload)
            result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("message_id"), int):
            return result["message_id"]
        return 0

    def edit_message_blob(self, chat_id: str, message_id: int, text: str) -> None:
        self.api(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text},
        )

    def send_chat_action(self, chat_id: str, action: str) -> None:
        try:
            self.api("sendChatAction", {"chat_id": chat_id, "action": action})
        except Exception:
            return

    def telegram_image_data_url(self, file_id: str) -> str:
        file_data = self.api("getFile", {"file_id": file_id})
        result = file_data.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("file_path"), str):
            raise RuntimeError("Telegram did not return a file path for the image.")
        response = self.client.get(f"https://api.telegram.org/file/bot{self.token}/{result['file_path']}")
        response.raise_for_status()
        return f"data:image/jpeg;base64,{b64encode(response.content).decode('ascii')}"

    def api(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        response = self.client.post(f"{self.api_url}/{method}", json=payload)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error calling {method}: {data}")
        return data


def split_message(text: str) -> list[str]:
    if len(text) <= MAX_TELEGRAM_MESSAGE:
        return [text]
    return [text[index : index + MAX_TELEGRAM_MESSAGE] for index in range(0, len(text), MAX_TELEGRAM_MESSAGE)]


def largest_telegram_photo(photos: list[object]) -> dict[str, object] | None:
    candidates = [photo for photo in photos if isinstance(photo, dict)]
    if not candidates:
        return None
    return max(candidates, key=lambda photo: int(photo.get("file_size") or photo.get("width") or 0))


def telegram_location(value: object) -> TelegramLocation | None:
    if not isinstance(value, dict):
        return None
    try:
        latitude = float(value["latitude"])
        longitude = float(value["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return TelegramLocation(
        latitude=latitude,
        longitude=longitude,
        live_period=optional_int(value.get("live_period")),
        horizontal_accuracy=optional_float(value.get("horizontal_accuracy")),
        heading=optional_int(value.get("heading")),
        proximity_alert_radius=optional_int(value.get("proximity_alert_radius")),
    )


def parse_location_watch_request(text: str) -> tuple[str, float] | None:
    normalized = " ".join(text.strip().split())
    lowered = normalized.lower()
    if not re.search(r"\b(let me know|notify me|alert me|tell me|remind me)\b", lowered):
        return None
    if not re.search(r"\b(near|nearby|close to|around|within)\b", lowered):
        return None

    radius = parse_location_watch_radius(normalized) or DEFAULT_LOCATION_WATCH_RADIUS_METERS
    target = parse_location_watch_target(normalized)
    if target is None:
        return None
    return target, radius


def parse_movement_watch_request(text: str) -> float | None:
    normalized = " ".join(text.strip().split())
    lowered = normalized.lower()
    if not re.search(r"\b(text me|let me know|notify me|alert me|tell me|remind me)\b", lowered):
        return None
    if not re.search(r"\b(move|moved|moving|walk|walked|travel|traveled|travelled)\b", lowered):
        return None
    match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(m|meter|meters|metre|metres|km|kilometer|kilometers|kilometre|kilometres)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"km", "kilometer", "kilometers", "kilometre", "kilometres"}:
        return value * 1000
    return value


def parse_location_watch_target(text: str) -> str | None:
    patterns = [
        r"\b(?:nearby|near|close to|around)\s+(.+)$",
        r"\bwithin\s+.+?\s+(?:of|from)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        target = clean_location_watch_target(match.group(1))
        if target:
            return target
    return None


def clean_location_watch_target(target: str) -> str:
    target = re.sub(r"^[\s:,-]+", "", target)
    target = re.sub(r"[.?!\s]+$", "", target)
    target = re.sub(r"\b(?:please|pls|thanks|thank you)\b[.?!\s]*$", "", target, flags=re.IGNORECASE)
    return target.strip()


def parse_location_watch_radius(text: str) -> float | None:
    match = re.search(
        r"\bwithin\s+(\d+(?:\.\d+)?)\s*(m|meter|meters|metre|metres|km|kilometer|kilometers|kilometre|kilometres)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"km", "kilometer", "kilometers", "kilometre", "kilometres"}:
        return value * 1000
    return value


def distance_meters(first: TelegramLocation, second: TelegramLocation) -> float:
    radius = 6371000.0
    first_lat = math.radians(first.latitude)
    second_lat = math.radians(second.latitude)
    delta_lat = math.radians(second.latitude - first.latitude)
    delta_lon = math.radians(second.longitude - first.longitude)
    a = math.sin(delta_lat / 2) ** 2 + math.cos(first_lat) * math.cos(second_lat) * math.sin(delta_lon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def format_distance(meters: float) -> str:
    if meters >= 1000:
        kilometers = meters / 1000
        return f"{kilometers:.1f} km" if kilometers < 10 else f"{kilometers:.0f} km"
    return f"{max(1, round(meters))} m"


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decode_data_url(data_url: str) -> tuple[str, bytes]:
    header, encoded = data_url.split(",", 1)
    media_type = header[5:].split(";", 1)[0] or "application/octet-stream"
    return media_type, b64decode(encoded)


def extension_for_media_type(media_type: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }.get(media_type, "bin")


def fit_editable_message(text: str) -> str:
    if len(text) <= MAX_TELEGRAM_MESSAGE:
        return text
    suffix = "\n... truncated"
    return text[: MAX_TELEGRAM_MESSAGE - len(suffix)] + suffix


def render_tool_events_tree(events: list[ToolCallLifecycleEvent], *, full_input: bool = False) -> str:
    lines = [f"Tool calls ({len(events)})"]
    for event in events:
        lines.append(f"• {format_tool_event(event, full_input=full_input)}")
    return "\n".join(lines)


def render_reasoning_tree(summary: str) -> str:
    lines = ["Thinking"]
    summary_lines = summary.splitlines() or [summary]
    lines.extend(summary_lines)
    return "\n".join(lines)


def render_tool_errors_tree(errors: tuple[str, ...]) -> str:
    lines = [f"Tool errors ({len(errors)})"]
    for error in errors:
        lines.append(f"• {error}")
    return "\n".join(lines)


def format_tool_event(event: ToolCallLifecycleEvent, *, full_input: bool = False) -> str:
    if not full_input:
        return event.summary
    args = format_tool_input(event.input, limit=None if full_input else 50)
    return f'{event.summary} ("{args}")'


def telegram_command(text: str) -> str | None:
    prefix = slash_prefix(text)
    if prefix is None:
        return None
    command = prefix.split("@", 1)[0]
    return command.lower()


def run_telegram() -> None:
    if not ensure_logged_in():
        return
    config = resolved_telegram_config()
    if config is None:
        print("Run `uv run tomo telegram-config set --bot-token TOKEN --chat-ids CHAT_ID` before starting the gateway.")
        return
    TelegramGateway(
        config.bot_token,
        allowed_chat_ids=config.allowed_chat_ids,
    ).run()


def start_telegram() -> None:
    if not ensure_logged_in():
        return
    if resolved_telegram_config() is None:
        print("Run `uv run tomo telegram-config set --bot-token TOKEN --chat-ids CHAT_ID` before starting the gateway.")
        return

    pid_path = telegram_pid_path()
    existing_pid = read_pid(pid_path)
    if existing_pid is not None and process_is_running(existing_pid):
        print(f"Telegram gateway is already running with PID {existing_pid}.")
        return
    orphan_pids = find_telegram_process_ids()
    if orphan_pids:
        orphan_pid = orphan_pids[0]
        write_pid(pid_path, orphan_pid)
        print(f"Telegram gateway is already running with PID {orphan_pid}.")
        return
    if existing_pid is not None:
        pid_path.unlink(missing_ok=True)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log_path = telegram_log_path()
    log_file = log_path.open("ab")
    process = subprocess.Popen(
        [sys.executable, "-c", "from tomo.telegram import run_telegram; run_telegram()"],
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    write_pid(pid_path, process.pid)
    print(f"Telegram gateway started with PID {process.pid}.")
    print(f"Logs: {log_path}")


def stop_telegram() -> None:
    pid_path = telegram_pid_path()
    targets = telegram_process_targets()
    if not targets:
        pid_path.unlink(missing_ok=True)
        print("Telegram gateway is not running.")
        return

    stop_telegram_pids(targets)
    pid_path.unlink(missing_ok=True)

    remaining = find_telegram_process_ids()
    if remaining:
        stop_telegram_pids(remaining)
        remaining = find_telegram_process_ids()

    if remaining:
        print(
            "Telegram gateway could not be fully stopped. "
            f"Remaining PIDs: {', '.join(str(pid) for pid in remaining)}"
        )
        return
    print("Telegram gateway stopped.")


def restart_telegram() -> None:
    stop_telegram()
    start_telegram()


def telegram_process_targets() -> list[int]:
    targets = set(find_telegram_process_ids())
    recorded_pid = read_pid(telegram_pid_path())
    if recorded_pid is not None:
        targets.add(recorded_pid)
    current_pid = os.getpid()
    return sorted(pid for pid in targets if pid != current_pid)


def stop_telegram_pids(pids: list[int]) -> None:
    current_pid = os.getpid()
    for pid in pids:
        if pid == current_pid:
            continue
        stop_process(pid, force=True)


def find_telegram_process_ids() -> list[int]:
    if os.name != "nt":
        return []

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.Name -eq 'python.exe' -and "
            "$_.CommandLine -like '*tomo.telegram import run_telegram*' "
            "} | "
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


def telegram_pid_path() -> Path:
    return settings.data_dir / TELEGRAM_PID_FILENAME


def telegram_log_path() -> Path:
    return settings.data_dir / TELEGRAM_LOG_FILENAME


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
    termination_signal = signal.SIGTERM
    if force:
        termination_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    os.kill(pid, termination_signal)


def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
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
    return True
