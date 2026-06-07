from __future__ import annotations

from types import SimpleNamespace

from tomo.gateway import TomoGateway, ToolCallLifecycleEvent, extract_agent_trace, format_tool_input
from tomo.tools import ApprovalRequest


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


def test_tomo_gateway_sends_message_and_saves_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = SimpleNamespace(invoke=lambda *args, **kwargs: {"messages": [{"role": "assistant", "content": "hello"}]})
    gateway = TomoGateway(responder=Responder(), agent=agent)

    reply = gateway.send_text("chat-1", "hi")

    assert reply == "hello"
    assert gateway.sessions["chat-1"].messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_tomo_gateway_resumes_interrupt_with_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responder = Responder(approved=True)
    agent = InterruptingAgent()
    gateway = TomoGateway(responder=responder, agent=agent)

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
                "tool_calls": [{"id": "call-1", "name": "read", "args": {"path": "notes.txt"}}],
            },
            {"type": "tool", "name": "read", "tool_call_id": "call-1", "status": "success", "content": "file text"},
            {"role": "assistant", "content": "done"},
        ]
    }

    trace = extract_agent_trace(result)

    assert trace.reasoning_summary == "Checked the file before editing."
    assert trace.render() == 'read: "{"path":"notes.txt"}"'
    assert (
        trace.render(include_reasoning=True)
        == 'Reasoning summary: Checked the file before editing.\nread: "{"path":"notes.txt"}"'
    )


def test_extract_agent_trace_marks_error_tool_message_failed():
    result = {
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call-1", "name": "web_fetch", "args": {"url": "https://example.com"}}]},
            {"type": "tool", "name": "web_fetch", "tool_call_id": "call-1", "content": "Error: blocked address"},
        ]
    }

    trace = extract_agent_trace(result)

    assert trace.render() == 'web_fetch: "{"url":"https://example.com"}"\nTool error: web_fetch: Error: blocked address'
    assert trace.tool_errors == ("web_fetch: Error: blocked address",)


class FailedToolThenRepairAgent:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def invoke(self, payload: object, **kwargs: object) -> object:
        self.calls.append(payload)
        if len(self.calls) == 1:
            return {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": "call-1", "name": "terminal", "args": {"command": "pytest"}}],
                    },
                    {
                        "type": "tool",
                        "name": "terminal",
                        "tool_call_id": "call-1",
                        "content": "Error: tests failed",
                    },
                    {"role": "assistant", "content": "Task complete."},
                ]
            }
        return {"messages": [{"role": "assistant", "content": "Fixed the failure and validated it."}]}


def test_tomo_gateway_rectifies_failed_tool_output_before_saving_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = FailedToolThenRepairAgent()
    gateway = TomoGateway(responder=Responder(), agent=agent)

    reply = gateway.send_text_with_trace("chat-1", "do the task")

    assert reply.text == "Fixed the failure and validated it."
    assert len(agent.calls) == 2
    repair_messages = agent.calls[1]["messages"]
    assert repair_messages[-1]["role"] == "user"
    assert "Do not mark any related todo/task complete yet" in repair_messages[-1]["content"]
    assert "terminal: Error: tests failed" in repair_messages[-1]["content"]
    assert gateway.sessions["chat-1"].messages == [
        {"role": "user", "content": "do the task"},
        {"role": "assistant", "content": "Fixed the failure and validated it."},
    ]


class ExitCodeOneThenRepairAgent:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def invoke(self, payload: object, **kwargs: object) -> object:
        self.calls.append(payload)
        if len(self.calls) == 1:
            return {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": "call-1", "name": "terminal", "args": {"command": "false"}}],
                    },
                    {
                        "type": "tool",
                        "name": "terminal",
                        "tool_call_id": "call-1",
                        "content": "Exit code: 1",
                    },
                    {"role": "assistant", "content": "Task complete."},
                ]
            }
        return {"messages": [{"role": "assistant", "content": "Rectified and validated."}]}


def test_tomo_gateway_rectifies_terminal_exit_code_one_before_completion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = ExitCodeOneThenRepairAgent()
    gateway = TomoGateway(responder=Responder(), agent=agent)

    reply = gateway.send_text_with_trace("chat-1", "run the command")

    assert reply.text == "Rectified and validated."
    assert len(agent.calls) == 2
    repair_messages = agent.calls[1]["messages"]
    assert "terminal: Exit code: 1" in repair_messages[-1]["content"]
    assert gateway.sessions["chat-1"].messages[-1] == {"role": "assistant", "content": "Rectified and validated."}


class AlwaysFailedToolAgent:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, payload: object, **kwargs: object) -> object:
        self.calls += 1
        return {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [{"id": f"call-{self.calls}", "name": "terminal", "args": {"command": "pytest"}}],
                },
                {
                    "type": "tool",
                    "name": "terminal",
                    "tool_call_id": f"call-{self.calls}",
                    "content": "Error: tests failed",
                },
                {"role": "assistant", "content": "Task complete."},
            ]
        }


def test_tomo_gateway_reports_unresolved_failure_after_rectification_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = AlwaysFailedToolAgent()
    gateway = TomoGateway(responder=Responder(), agent=agent)

    reply = gateway.send_text_with_trace("chat-1", "do the task")

    assert agent.calls == 3
    assert reply.text == "I could not complete the task because validation/tool execution is still failing:\n- terminal: Error: tests failed"
    assert reply.trace.tool_errors == ("terminal: Error: tests failed",)
    assert gateway.sessions["chat-1"].messages[-1] == {"role": "assistant", "content": reply.text}


def test_format_tool_input_normalizes_and_truncates_single_line():
    text = "alpha\n" + ("x" * 60)

    assert format_tool_input(text) == "alpha xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx..."
    assert format_tool_input(text, limit=None) == "alpha " + ("x" * 60)


def test_tool_event_render_can_show_full_input():
    event = ToolCallLifecycleEvent(name="terminal", input={"command": "x" * 80})

    assert event.render().endswith('..."')
    assert len(event.render()) < len(event.render(full_input=True))
    assert event.render(full_input=True) == 'terminal: "{"command":"' + ("x" * 80) + '"}"'


class StreamingAgent:
    def __init__(self) -> None:
        self.invoked = False

    def stream(self, payload: object, **kwargs: object):
        yield {"messages": [{"role": "assistant", "tool_calls": [{"id": "call-1", "name": "web_search", "args": {"query": "hermes"}}]}]}
        yield {
            "messages": [
                {"role": "assistant", "tool_calls": [{"id": "call-1", "name": "web_search", "args": {"query": "hermes"}}]},
                {"type": "tool", "name": "web_search", "tool_call_id": "call-1", "status": "success", "content": "results"},
                {"role": "assistant", "content": "done"},
            ]
        }

    def invoke(self, payload: object, **kwargs: object) -> object:
        self.invoked = True
        return {"messages": [{"role": "assistant", "content": "fallback"}]}


def test_tomo_gateway_emits_streaming_tool_events_in_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = StreamingAgent()
    gateway = TomoGateway(responder=Responder(), agent=agent)
    events: list[str] = []

    reply = gateway.send_text_with_events("chat-1", "search", on_event=lambda event: events.append(event.render()))

    assert reply.text == "done"
    assert events == ['web_search: "{"query":"hermes"}"']
    assert agent.invoked is False


class FakeToolCall:
    tool_name = "web_fetch"
    args = {"url": "https://example.com/long/path"}
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


def test_tomo_gateway_uses_stream_events_projection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = EventStreamingAgent()
    gateway = TomoGateway(responder=Responder(), agent=agent)
    events: list[str] = []

    reply = gateway.send_text_with_events("chat-1", "fetch", on_event=lambda event: events.append(event.render()))

    assert agent.stream_called is True
    assert events == ['web_fetch: "{"url":"https://example.com/long/path"}"']
    assert reply.text == "final answer"


class RawEventStream:
    output = {"messages": [{"type": "tool", "name": "web_search", "content": "TOOL OUTPUT SHOULD NOT BE FINAL"}]}

    def __iter__(self):
        yield {
            "method": "tool_calls",
            "params": {"data": [{"id": "call-1", "tool_name": "web_search", "status": "started", "args": {"query": "Hermes Agent"}}]},
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


def test_tomo_gateway_streams_raw_events_and_ignores_tool_output_as_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gateway = TomoGateway(responder=Responder(), agent=RawEventAgent())
    events: list[str] = []
    deltas: list[str] = []

    reply = gateway.send_text_with_events(
        "chat-1",
        "search",
        on_event=lambda event: events.append(event.render()),
        on_text_delta=deltas.append,
    )

    assert events == ['web_search: "{"query":"Hermes Agent"}"']
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
                        SimpleNamespace(tool_calls=[{"id": "call-1", "name": "web_search", "args": {"query": "Hermes Agent"}}])
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


class StreamingInterruptAgent:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def stream(self, payload: object, **kwargs: object):
        self.calls.append(payload)
        if len(self.calls) == 1:
            yield {
                "type": "updates",
                "data": {
                    "__interrupt__": [
                        SimpleNamespace(
                            value={
                                "action_requests": [
                                    {
                                        "name": "terminal",
                                        "args": {"command": "pwd"},
                                        "description": "Shell command requires approval",
                                    }
                                ]
                            }
                        )
                    ]
                },
            }
            return
        yield {"type": "messages", "data": (FakeToken([{"type": "text", "text": "done"}]), {"langgraph_node": "model"})}


class StreamingTextThenInterruptAgent:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def stream(self, payload: object, **kwargs: object):
        self.calls.append(payload)
        if len(self.calls) == 1:
            yield {"type": "messages", "data": (FakeToken([{"type": "text", "text": "Need approval first."}]), {"langgraph_node": "model"})}
            yield {
                "type": "updates",
                "data": {
                    "__interrupt__": [
                        SimpleNamespace(
                            value={
                                "action_requests": [
                                    {
                                        "name": "terminal",
                                        "args": {"command": "pwd"},
                                        "description": "Shell command requires approval",
                                    }
                                ]
                            }
                        )
                    ]
                },
            }
            return
        yield {"type": "messages", "data": (FakeToken([{"type": "text", "text": "done"}]), {"langgraph_node": "model"})}


def test_tomo_gateway_uses_messages_updates_stream_without_tool_output_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gateway = TomoGateway(responder=Responder(), agent=LangChainStreamingAgent())
    events: list[str] = []
    deltas: list[str] = []

    reply = gateway.send_text_with_events(
        "chat-1",
        "search",
        on_event=lambda event: events.append(event.render()),
        on_text_delta=deltas.append,
    )

    assert events == ['web_search: "{"query":"Hermes Agent"}"']
    assert deltas == ["Hermes", " Agent"]
    assert reply.text == "Hermes Agent"


class LangGraphFinalMessageStreamingAgent:
    def stream(self, payload: object, **kwargs: object):
        yield {"type": "messages", "data": (FakeToken([{"type": "text", "text": "Hello"}]), {"langgraph_node": "model"})}
        final = SimpleNamespace(type="ai", content="Hello", tool_calls=[])
        yield {"type": "messages", "data": (final, {"langgraph_node": "model"})}
        yield {"type": "updates", "data": {"model": {"messages": [final]}}}
        yield {"type": "updates", "data": {"final_response": None}}


def test_tomo_gateway_does_not_stream_final_langgraph_message_twice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gateway = TomoGateway(responder=Responder(), agent=LangGraphFinalMessageStreamingAgent())
    deltas: list[str] = []

    reply = gateway.send_text_with_events("chat-1", "say hello", on_text_delta=deltas.append)

    assert deltas == ["Hello"]
    assert reply.text == "Hello"


def test_tomo_gateway_preserves_streaming_interrupts_for_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responder = Responder(approved=True)
    agent = StreamingInterruptAgent()
    gateway = TomoGateway(responder=responder, agent=agent)

    reply = gateway.send_text("chat-1", "pwd")

    assert reply == "done"
    assert responder.requests[0][1].operation == "terminal"
    assert len(agent.calls) == 2
    assert agent.calls[1].resume == {"decisions": [{"type": "approve"}]}


def test_tomo_gateway_preserves_interrupt_when_stream_also_emits_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responder = Responder(approved=True)
    agent = StreamingTextThenInterruptAgent()
    gateway = TomoGateway(responder=responder, agent=agent)

    reply = gateway.send_text("chat-1", "pwd")

    assert reply == "done"
    assert responder.requests[0][1].operation == "terminal"
    assert len(agent.calls) == 2
