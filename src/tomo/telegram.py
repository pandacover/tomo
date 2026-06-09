from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from base64 import b64decode, b64encode
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import settings
from .gateway import ToolCallLifecycleEvent, TomoGateway, UserContent, format_tool_input
from .reasoning import (
    effective_reasoning_effort,
    effective_show_reasoning_trace,
    format_reasoning_status,
    parse_reasoning_command_args,
    reasoning_usage_message,
)
from .slash_commands import slash_prefix, telegram_bot_commands, unrecognized_message
from .session_store import ChatSession
from .telegram_config import parse_allowed_chat_ids, resolved_telegram_config
from .token_store import load_tokens
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


@dataclass
class PendingTelegramApproval:
    request: ApprovalRequest
    answers: queue.Queue[bool] = field(default_factory=queue.Queue)


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
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            return
        chat_id = str(chat["id"])
        if self.allowed_chat_ids and int(chat_id) not in self.allowed_chat_ids:
            self.send_message(chat_id, "This chat is not allowed to use Tomo.")
            return
        content = self.extract_message_content(message)
        if content is None:
            return
        if isinstance(content, str):
            self.handle_text(chat_id, content)
            return
        self.handle_user_content(chat_id, content)

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
        if chat_id in self.busy_chats:
            self.send_message(chat_id, "Tomo is still working on the previous message.")
            return

        self.busy_chats.add(chat_id)
        thread = threading.Thread(target=self.reply_worker, args=(chat_id, text), daemon=True)
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
        argument = telegram_command_argument(text)
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
        argument = telegram_command_argument(text)
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
        action, value = parse_reasoning_command_args(telegram_command_argument(text))
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
    args = format_tool_input(event.input, limit=None if full_input else 50)
    return f'{event.name}("{args}")'


def telegram_command(text: str) -> str | None:
    prefix = slash_prefix(text)
    if prefix is None:
        return None
    command = prefix.split("@", 1)[0]
    return command.lower()


def telegram_command_argument(text: str) -> str | None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip().lower() or None


def run_telegram() -> None:
    if load_tokens() is None:
        print("Not logged in. Run `uv run tomo login` first.")
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
    if load_tokens() is None:
        print("Not logged in. Run `uv run tomo login` first.")
        return
    if resolved_telegram_config() is None:
        print("Run `uv run tomo telegram-config set --bot-token TOKEN --chat-ids CHAT_ID` before starting the gateway.")
        return

    pid_path = telegram_pid_path()
    existing_pid = read_pid(pid_path)
    if existing_pid is not None and process_is_running(existing_pid):
        print(f"Telegram gateway is already running with PID {existing_pid}.")
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
    pid = read_pid(pid_path)
    if pid is None:
        print("Telegram gateway is not running.")
        return
    if not process_is_running(pid):
        pid_path.unlink(missing_ok=True)
        print("Telegram gateway was not running; removed stale PID file.")
        return

    stop_process(pid, force=False)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            pid_path.unlink(missing_ok=True)
            print("Telegram gateway stopped.")
            return
        time.sleep(0.1)

    stop_process(pid, force=True)
    pid_path.unlink(missing_ok=True)
    print("Telegram gateway stopped forcefully.")


def restart_telegram() -> None:
    stop_telegram()
    start_telegram()


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
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def process_is_running(pid: int) -> bool:
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
