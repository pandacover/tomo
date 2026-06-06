from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

import httpx

from .config import settings
from .gateway import TomoGateway
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
            if settings.show_reasoning_summary and reply.trace.reasoning_summary:
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
