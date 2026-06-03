from __future__ import annotations

import queue
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field

import httpx

from .config import settings
from .gateway import ButlerGateway
from .token_store import load_tokens
from .tools import ApprovalRequest


APPROVE_WORDS = {"y", "yes", "approve", "/approve"}
DENY_WORDS = {"", "n", "no", "deny", "/deny"}
MAX_TELEGRAM_MESSAGE = 4096


@dataclass
class PendingTelegramApproval:
    request: ApprovalRequest
    answers: queue.Queue[bool] = field(default_factory=queue.Queue)


@dataclass
class TelegramResponseStream:
    gateway: TelegramGateway
    chat_id: str
    current_text: str = ""
    current_message_id: int | None = None

    def append(self, delta: str) -> None:
        if not delta:
            return
        remaining = delta
        while remaining:
            space = MAX_TELEGRAM_MESSAGE - len(self.current_text)
            if self.current_message_id is None or space <= 0:
                chunk = remaining[:MAX_TELEGRAM_MESSAGE]
                self.current_text = chunk
                self.current_message_id = self.gateway.send_message_blob(self.chat_id, chunk)
                remaining = remaining[MAX_TELEGRAM_MESSAGE:]
                continue
            chunk = remaining[:space]
            self.current_text += chunk
            self.gateway.edit_message_blob(self.chat_id, self.current_message_id, self.current_text)
            remaining = remaining[space:]


class TelegramGateway:
    def __init__(
        self,
        token: str,
        *,
        allowed_chat_ids: Iterable[int] | None = None,
        client: httpx.Client | None = None,
        butler: ButlerGateway | None = None,
    ) -> None:
        self.token = token
        self.allowed_chat_ids = set(allowed_chat_ids or [])
        self.client = client or httpx.Client(timeout=60)
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.butler = butler or ButlerGateway(responder=self)
        self.pending_approvals: dict[str, PendingTelegramApproval] = {}
        self.busy_chats: set[str] = set()

    def run(self) -> None:
        offset: int | None = None
        self.send_startup_notice()
        while True:
            params: dict[str, object] = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            updates = self.api("getUpdates", params).get("result", [])
            for update in updates:
                offset = max(offset or 0, int(update["update_id"]) + 1)
                self.handle_update(update)

    def send_startup_notice(self) -> None:
        for chat_id in self.allowed_chat_ids:
            self.send_message(str(chat_id), "Butler Telegram gateway is online.")

    def handle_update(self, update: dict[str, object]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            return
        chat_id = str(chat["id"])
        if self.allowed_chat_ids and int(chat_id) not in self.allowed_chat_ids:
            self.send_message(chat_id, "This chat is not allowed to use Butler.")
            return
        text = str(message.get("text") or "").strip()
        if not text:
            return
        self.handle_text(chat_id, text)

    def handle_text(self, chat_id: str, text: str) -> None:
        pending = self.pending_approvals.get(chat_id)
        if pending is not None:
            self.resolve_approval(chat_id, text, pending)
            return
        if text == "/start":
            self.send_message(chat_id, "Butler is ready. Send a message to chat.")
            return
        if text == "/cancel":
            self.send_message(chat_id, "No pending approval to cancel.")
            return
        if chat_id in self.busy_chats:
            self.send_message(chat_id, "Butler is still working on the previous message.")
            return

        self.busy_chats.add(chat_id)
        thread = threading.Thread(target=self.reply_worker, args=(chat_id, text), daemon=True)
        thread.start()

    def reply_worker(self, chat_id: str, text: str) -> None:
        streamed_reply_parts: list[str] = []
        response_stream = TelegramResponseStream(self, chat_id)
        try:
            reply = self.butler.send_text_with_events(
                chat_id,
                text,
                on_event=lambda event: self.send_message(chat_id, event.render()),
                on_text_delta=lambda delta: (streamed_reply_parts.append(delta), response_stream.append(delta)),
            )
        except Exception as exc:  # noqa: BLE001
            self.send_message(chat_id, f"Error: {exc}")
        else:
            if settings.show_reasoning_summary and reply.trace.reasoning_summary:
                self.send_message(chat_id, f"Reasoning summary: {reply.trace.reasoning_summary}")
            response_text = "".join(streamed_reply_parts) if streamed_reply_parts else reply.text
            if not streamed_reply_parts:
                self.send_message(chat_id, response_text)
        finally:
            self.busy_chats.discard(chat_id)

    def request_approval(self, channel_id: str, request: ApprovalRequest) -> bool:
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
        self.api("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})

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


def parse_allowed_chat_ids(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def run_telegram() -> None:
    if load_tokens() is None:
        print("Not logged in. Run `uv run butler login` first.")
        return
    if not settings.telegram_bot_token:
        print("Set BUTLER_TELEGRAM_BOT_TOKEN before running the Telegram gateway.")
        return
    TelegramGateway(
        settings.telegram_bot_token,
        allowed_chat_ids=parse_allowed_chat_ids(settings.telegram_allowed_chat_ids),
    ).run()
