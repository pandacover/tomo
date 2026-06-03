from __future__ import annotations

from types import SimpleNamespace

from butler.gateway import ButlerGateway, extract_agent_trace
from butler.tools import ApprovalRequest


class Responder:
    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.requests: list[tuple[str, ApprovalRequest]] = []

    def request_approval(self, channel_id: str, request: ApprovalRequest) -> bool:
        self.requests.append((channel_id, request))
        return self.approved


class InterruptingAgent:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def invoke(self, payload: object, **kwargs: object) -> object:
        self.calls.append(payload)
        if len(self.calls) == 1:
            return {
                "__interrupt__": [
                    SimpleNamespace(
                        value={
                            "action_requests": [
                                {
                                    "name": "write",
                                    "args": {"path": "note.txt", "content": "hi"},
                                    "description": "Tool execution requires approval",
                                }
                            ]
                        }
                    )
                ]
            }
        return {"messages": [{"role": "assistant", "content": "done"}]}


def test_butler_gateway_sends_message_and_saves_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = SimpleNamespace(invoke=lambda *args, **kwargs: {"messages": [{"role": "assistant", "content": "hello"}]})
    gateway = ButlerGateway(responder=Responder(), agent=agent)

    reply = gateway.send_text("chat-1", "hi")

    assert reply == "hello"
    assert gateway.sessions["chat-1"].messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_butler_gateway_resumes_interrupt_with_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responder = Responder(approved=True)
    agent = InterruptingAgent()
    gateway = ButlerGateway(responder=responder, agent=agent)

    reply = gateway.send_text("telegram-1", "write a note")

    assert reply == "done"
    assert responder.requests[0][0] == "telegram-1"
    assert responder.requests[0][1].operation == "write"
    assert len(agent.calls) == 2
    assert agent.calls[1].resume == {"decisions": [{"type": "approve"}]}


def test_extract_agent_trace_reads_reasoning_summary_and_tool_statuses():
    result = {
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "reasoning_summary", "summary": "Checked the file before editing."}],
                "tool_calls": [{"id": "call-1", "name": "read"}],
            },
            {"type": "tool", "name": "read", "tool_call_id": "call-1", "status": "success", "content": "file text"},
            {"role": "assistant", "content": "done"},
        ]
    }

    trace = extract_agent_trace(result)

    assert trace.reasoning_summary == "Checked the file before editing."
    assert trace.render() == "read tool start\nread tool running\nread tool success"
    assert (
        trace.render(include_reasoning=True)
        == "Reasoning summary: Checked the file before editing.\nread tool start\nread tool running\nread tool success"
    )


def test_extract_agent_trace_marks_error_tool_message_failed():
    result = {
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call-1", "name": "web_fetch"}]},
            {"type": "tool", "name": "web_fetch", "tool_call_id": "call-1", "content": "Error: blocked address"},
        ]
    }

    trace = extract_agent_trace(result)

    assert trace.render() == "web_fetch tool start\nweb_fetch tool running\nweb_fetch tool failed"


class StreamingAgent:
    def __init__(self) -> None:
        self.invoked = False

    def stream(self, payload: object, **kwargs: object):
        yield {"messages": [{"role": "assistant", "tool_calls": [{"id": "call-1", "name": "web_search"}]}]}
        yield {
            "messages": [
                {"role": "assistant", "tool_calls": [{"id": "call-1", "name": "web_search"}]},
                {"type": "tool", "name": "web_search", "tool_call_id": "call-1", "status": "success", "content": "results"},
                {"role": "assistant", "content": "done"},
            ]
        }

    def invoke(self, payload: object, **kwargs: object) -> object:
        self.invoked = True
        return {"messages": [{"role": "assistant", "content": "fallback"}]}


def test_butler_gateway_emits_streaming_tool_events_in_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = StreamingAgent()
    gateway = ButlerGateway(responder=Responder(), agent=agent)
    events: list[str] = []

    reply = gateway.send_text_with_events("chat-1", "search", on_event=lambda event: events.append(event.render()))

    assert reply.text == "done"
    assert events == ["web_search tool start", "web_search tool running", "web_search tool success"]
    assert agent.invoked is False


class FakeToolCall:
    tool_name = "web_fetch"
    error = None
    completed = False

    @property
    def output_deltas(self):
        self.completed = True
        return iter(["discarded tool output"])


class FakeEventStream:
    def __init__(self) -> None:
        self.tool_calls = [FakeToolCall()]
        self.output = {"messages": [{"role": "assistant", "content": "final answer"}]}


class EventStreamingAgent:
    def __init__(self) -> None:
        self.stream_called = False

    def stream_events(self, payload: object, **kwargs: object) -> FakeEventStream:
        self.stream_called = True
        return FakeEventStream()

    def invoke(self, payload: object, **kwargs: object) -> object:
        raise AssertionError("invoke fallback should not be used")


def test_butler_gateway_uses_deepagents_stream_events_projection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = EventStreamingAgent()
    gateway = ButlerGateway(responder=Responder(), agent=agent)
    events: list[str] = []

    reply = gateway.send_text_with_events("chat-1", "fetch", on_event=lambda event: events.append(event.render()))

    assert agent.stream_called is True
    assert events == ["web_fetch tool start", "web_fetch tool running", "web_fetch tool success"]
    assert reply.text == "final answer"


class RawEventStream:
    output = {"messages": [{"type": "tool", "name": "web_search", "content": "TOOL OUTPUT SHOULD NOT BE FINAL"}]}

    def __iter__(self):
        yield {
            "method": "tool_calls",
            "params": {"data": [{"id": "call-1", "tool_name": "web_search", "status": "started"}]},
        }
        yield {
            "method": "messages",
            "params": {"data": [{"event": "content-block-delta", "delta": {"type": "text-delta", "text": "Hermes"}}]},
        }
        yield {
            "method": "tool_calls",
            "params": {"data": [{"id": "call-1", "tool_name": "web_search", "completed": True}]},
        }
        yield {
            "method": "messages",
            "params": {"data": [{"event": "content-block-delta", "delta": {"type": "text-delta", "text": " Agent"}}]},
        }


class RawEventAgent:
    def stream_events(self, payload: object, **kwargs: object) -> RawEventStream:
        return RawEventStream()


def test_butler_gateway_streams_raw_events_and_ignores_tool_output_as_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gateway = ButlerGateway(responder=Responder(), agent=RawEventAgent())
    events: list[str] = []
    deltas: list[str] = []

    reply = gateway.send_text_with_events(
        "chat-1",
        "search",
        on_event=lambda event: events.append(event.render()),
        on_text_delta=deltas.append,
    )

    assert events == ["web_search tool start", "web_search tool running", "web_search tool success"]
    assert deltas == ["Hermes", " Agent"]
    assert reply.text == "Hermes Agent"


class FakeToken:
    def __init__(self, blocks: list[dict[str, object]]) -> None:
        self.content_blocks = blocks


class LangChainStreamingAgent:
    def stream(self, payload: object, **kwargs: object):
        yield {
            "type": "updates",
            "data": {
                "model": {
                    "messages": [
                        SimpleNamespace(tool_calls=[{"id": "call-1", "name": "web_search"}])
                    ]
                }
            },
        }
        yield {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        SimpleNamespace(type="tool", name="web_search", tool_call_id="call-1", content="TOOL OUTPUT SHOULD NOT BE FINAL")
                    ]
                }
            },
        }
        yield {"type": "messages", "data": (FakeToken([{"type": "text", "text": "Hermes"}]), {"langgraph_node": "model"})}
        yield {
            "type": "messages",
            "data": (FakeToken([{"type": "text", "text": " ignored tool text"}]), {"langgraph_node": "tools"}),
        }
        yield {"type": "messages", "data": (FakeToken([{"type": "text", "text": " Agent"}]), {"langgraph_node": "model"})}


def test_butler_gateway_uses_messages_updates_stream_without_tool_output_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gateway = ButlerGateway(responder=Responder(), agent=LangChainStreamingAgent())
    events: list[str] = []
    deltas: list[str] = []

    reply = gateway.send_text_with_events(
        "chat-1",
        "search",
        on_event=lambda event: events.append(event.render()),
        on_text_delta=deltas.append,
    )

    assert events == ["web_search tool start", "web_search tool running", "web_search tool success"]
    assert deltas == ["Hermes", " Agent"]
    assert reply.text == "Hermes Agent"
