from __future__ import annotations

import threading

from butler.gateway import AgentTrace, GatewayReply, ToolCallLifecycleEvent
from butler.telegram import TelegramGateway, parse_allowed_chat_ids, split_message


class FakeButler:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        self.messages.append((channel_id, text))
        if on_event is not None:
            on_event(ToolCallLifecycleEvent(name="web_search", status="start"))
            on_event(ToolCallLifecycleEvent(name="web_search", status="running"))
            on_event(ToolCallLifecycleEvent(name="web_search", status="success"))
        if on_text_delta is not None:
            on_text_delta("streamed ")
            on_text_delta("reply")
        return GatewayReply(
            text="streamed reply",
            trace=AgentTrace(reasoning_summary="hidden by default"),
        )


class FakeTelegram(TelegramGateway):
    def __init__(self, **kwargs: object) -> None:
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


def test_parse_allowed_chat_ids_accepts_comma_list():
    assert parse_allowed_chat_ids("1, 2,3") == [1, 2, 3]
    assert parse_allowed_chat_ids(None) == []


def test_split_message_chunks_telegram_limit():
    chunks = split_message("x" * 4100)

    assert [len(chunk) for chunk in chunks] == [4096, 4]


def test_telegram_gateway_rejects_unallowed_chat():
    gateway = FakeTelegram(allowed_chat_ids=[1])

    gateway.handle_update({"message": {"chat": {"id": 2}, "text": "hi"}})

    assert gateway.sent == [("2", "This chat is not allowed to use Butler.")]


def test_telegram_gateway_routes_text_to_butler():
    butler = FakeButler()
    gateway = FakeTelegram(butler=butler)

    gateway.reply_worker("123", "hello")

    assert butler.messages == [("123", "hello")]
    assert [(method, payload["text"]) for method, payload in gateway.calls] == [
        ("sendMessage", "web_search tool start"),
        ("sendMessage", "web_search tool running"),
        ("sendMessage", "web_search tool success"),
        ("sendMessage", "streamed "),
        ("editMessageText", "streamed reply"),
    ]


class LongDeltaButler:
    def send_text_with_events(self, channel_id: str, text: str, on_event=None, on_text_delta=None) -> GatewayReply:
        if on_text_delta is not None:
            on_text_delta("x" * 4090)
            on_text_delta("y" * 10)
        return GatewayReply(text="unused", trace=AgentTrace())


def test_telegram_gateway_coalesces_streamed_reply_until_size_split():
    gateway = FakeTelegram(butler=LongDeltaButler())

    gateway.reply_worker("123", "hello")

    send_calls = [(method, payload) for method, payload in gateway.calls if method == "sendMessage"]
    edit_calls = [(method, payload) for method, payload in gateway.calls if method == "editMessageText"]
    assert [len(str(payload["text"])) for _, payload in send_calls] == [4090, 4]
    assert [len(str(payload["text"])) for _, payload in edit_calls] == [4096]
    assert edit_calls[0][1]["text"] == ("x" * 4090) + ("y" * 6)
    assert send_calls[1][1]["text"] == "y" * 4


def test_telegram_gateway_approval_waits_for_chat_reply():
    gateway = FakeTelegram()
    result: dict[str, bool] = {}
    request = type("Request", (), {"operation": "write", "target": "tool call", "reason": "needs write"})()

    thread = threading.Thread(target=lambda: result.setdefault("approved", gateway.request_approval("123", request)))
    thread.start()
    assert "123" in gateway.pending_approvals

    gateway.handle_text("123", "/approve")
    thread.join(timeout=1)

    assert result["approved"] is True
    assert ("123", "Approved.") in gateway.sent
