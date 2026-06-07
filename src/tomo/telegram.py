from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import settings
from .gateway import TomoGateway
from .reasoning import (
    effective_reasoning_effort,
    effective_show_reasoning_trace,
    format_reasoning_status,
    parse_reasoning_command_args,
    reasoning_usage_message,
)
from .slash_commands import slash_prefix, telegram_bot_commands, unrecognized_message
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
        text = str(message.get("text") or "").strip()
        if not text:
            return
        self.handle_text(chat_id, text)

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
        pending = self.pending_approvals.get(chat_id)
        if pending is not None:
            self.resolve_approval(chat_id, command or text, pending)
            return
        if command == "/start":
            self.send_message(chat_id, "Tomo is ready. Send a message to chat.")
            return
        if command == "/cancel":
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

    def reply_worker(self, chat_id: str, text: str) -> None:
        done = threading.Event()
        heartbeat = threading.Thread(target=self.work_status_worker, args=(chat_id, done), daemon=True)
        heartbeat.start()
        try:
            reply = self.tomo.send_text_with_events(
                chat_id,
                text,
                on_event=lambda event: self.send_message(chat_id, event.render(full_input=chat_id in self.debug_tool_chats)),
            )
        except Exception as exc:  # noqa: BLE001
            self.send_message(chat_id, f"Error: {exc}")
        else:
            trace_overrides = getattr(self.tomo, "channel_trace_override", {})
            if effective_show_reasoning_trace(
                chat_override=trace_overrides.get(chat_id) if isinstance(trace_overrides, dict) else None
            ) and reply.trace.reasoning_summary:
                self.send_message(chat_id, f"Reasoning summary: {reply.trace.reasoning_summary}")
            for error in reply.trace.tool_errors:
                self.send_message(chat_id, f"Tool error: {error}")
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

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            pid_path.unlink(missing_ok=True)
            print("Telegram gateway stopped.")
            return
        time.sleep(0.1)

    os.kill(pid, signal.SIGKILL)
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


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
