from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.types import Command

from tomo.langgraph_agent import LANGGRAPH_SYSTEM_PROMPT, make_langgraph_agent


@dataclass
class FakeModel:
    responses: list[AIMessage]
    calls: int = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[index]


@dataclass
class CapturingModel:
    response: AIMessage
    messages: list[object] | None = None

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.response


def config():
    return {"configurable": {"thread_id": "test-thread"}}


def final_model(text: str = "done") -> FakeModel:
    return FakeModel([AIMessage(content=text)])


@tool("files_search")
def fake_files_search(query: str, path: str = ".", k: int = 20) -> str:
    """Fake local search."""
    return f"local:{query}:{path}:{k}"


@tool("web_search")
def fake_web_search(query: str, k: int = 5, fetch_results: bool = True) -> str:
    """Fake web search."""
    return f"web:{query}:{k}:{fetch_results}"


@tool("read_memory")
def fake_read_memory(query: str, k: int = 8) -> str:
    """Fake memory search."""
    return f"memory:{query}:{k}"


@tool("web_fetch")
def fake_web_fetch(url: str) -> str:
    """Fake fetch."""
    return "fetch ok"


@tool("terminal")
def fake_terminal(command: str) -> str:
    """Fake terminal."""
    return "Exit code: 0\nok"


@tool("edit_file")
def fake_edit_file(path: str, old_text: str, new_text: str) -> str:
    """Fake edit."""
    return f"Edited {path}."


@tool("read_file")
def fake_read_file(path: str) -> str:
    """Fake file read."""
    return "file text"


def test_local_code_question_forces_files_search_before_final_answer():
    agent = make_langgraph_agent(model=final_model(), tools=[fake_files_search])

    result = agent.invoke({"messages": [HumanMessage(content="Which file implements Tomo agent code?")]}, config=config())

    tool_names = [getattr(message, "name", None) for message in result["messages"]]
    assert "files_search" in tool_names
    assert result["messages"][-1].content == "done"


def test_langgraph_system_prompt_points_browser_work_to_local_skill():
    assert "skills/browser-tool/SKILL.md" in LANGGRAPH_SYSTEM_PROMPT
    assert "Before using the browser tool" in LANGGRAPH_SYSTEM_PROMPT


def test_langgraph_model_receives_browser_skill_instruction():
    model = CapturingModel(AIMessage(content="done"))
    agent = make_langgraph_agent(model=model, tools=[])

    agent.invoke({"messages": [HumanMessage(content="Take a browser screenshot.")]}, config=config())

    assert model.messages is not None
    assert "skills/browser-tool/SKILL.md" in model.messages[0].content


def test_current_docs_question_forces_web_search_path():
    agent = make_langgraph_agent(model=final_model(), tools=[fake_web_search])

    result = agent.invoke({"messages": [HumanMessage(content="Check the latest LangGraph docs.")]}, config=config())

    assert "web_search" in [getattr(message, "name", None) for message in result["messages"]]


def test_memory_relevant_prompt_routes_through_read_memory():
    agent = make_langgraph_agent(model=final_model(), tools=[fake_read_memory])

    result = agent.invoke({"messages": [HumanMessage(content="Remember my previous project preference?")]}, config=config())

    assert "read_memory" in [getattr(message, "name", None) for message in result["messages"]]


def test_simple_stable_question_skips_tools():
    agent = make_langgraph_agent(model=final_model("Paris"), tools=[fake_files_search, fake_web_search, fake_read_memory])

    result = agent.invoke({"messages": [HumanMessage(content="What is the capital of France?")]}, config=config())

    assert [getattr(message, "name", None) for message in result["messages"] if getattr(message, "type", None) == "tool"] == []
    assert result["messages"][-1].content == "Paris"


def test_invalid_grep_tool_call_routes_back_to_model_correction():
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "grep", "args": {"query": "x"}}]),
            AIMessage(content="Used files_search instead."),
        ]
    )
    agent = make_langgraph_agent(model=model, tools=[fake_files_search])

    result = agent.invoke({"messages": [HumanMessage(content="Find x in the repo code.")]}, config=config())

    assert model.calls == 2
    assert any("`grep` is not available" in getattr(message, "content", "") for message in result["messages"])
    assert result["messages"][-1].content == "Used files_search instead."


def test_terminal_call_triggers_interrupt_and_resume_executes_tool():
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "terminal", "args": {"command": "pwd"}}]),
            AIMessage(content="validated"),
        ]
    )
    agent = make_langgraph_agent(model=model, tools=[fake_terminal])
    cfg = config()

    interrupted = agent.invoke({"messages": [HumanMessage(content="Run pwd.")]}, config=cfg)

    assert "__interrupt__" in interrupted
    action = interrupted["__interrupt__"][0].value["action_requests"][0]
    assert action["name"] == "terminal"

    result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=cfg)

    assert "terminal" in [getattr(message, "name", None) for message in result["messages"]]
    assert result["messages"][-1].content == "validated"


def test_rejected_approval_prevents_success_claim_and_repairs():
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "terminal", "args": {"command": "pwd"}}]),
            AIMessage(content="I cannot claim success after denial."),
        ]
    )
    agent = make_langgraph_agent(model=model, tools=[fake_terminal])
    cfg = config()
    agent.invoke({"messages": [HumanMessage(content="Run pwd.")]}, config=cfg)

    result = agent.invoke(Command(resume={"decisions": [{"type": "reject", "message": "no"}]}), config=cfg)

    assert any(getattr(message, "name", None) == "terminal" and "denied" in message.content for message in result["messages"])
    assert result["messages"][-1].content == "I cannot claim success after denial."


@tool("bad_fetch")
def bad_fetch(url: str) -> str:
    """Always fails."""
    return "Error: network failed"


def test_tool_error_triggers_repair_loop():
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "bad_fetch", "args": {"url": "https://example.com"}}]),
            AIMessage(content="Repaired with another source."),
        ]
    )
    agent = make_langgraph_agent(model=model, tools=[bad_fetch])

    result = agent.invoke({"messages": [HumanMessage(content="Fetch this URL.")]}, config=config())

    assert model.calls == 2
    assert any("Rectify the failure" in getattr(message, "content", "") for message in result["messages"])
    assert result["messages"][-1].content == "Repaired with another source."


def test_persistent_tool_error_returns_unresolved_failure():
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "bad_fetch", "args": {"url": "https://example.com/1"}}]),
            AIMessage(content="", tool_calls=[{"id": "call-2", "name": "bad_fetch", "args": {"url": "https://example.com/2"}}]),
            AIMessage(content="", tool_calls=[{"id": "call-3", "name": "bad_fetch", "args": {"url": "https://example.com/3"}}]),
        ]
    )
    agent = make_langgraph_agent(model=model, tools=[bad_fetch])

    result = agent.invoke({"messages": [HumanMessage(content="Fetch this URL.")]}, config=config())

    assert "I could not complete the task" in result["messages"][-1].content


@tool("sometimes_missing")
def sometimes_missing(path: str) -> str:
    """Missing file probe."""
    return f"Error: {path} does not exist."


def test_prior_exploratory_tool_errors_do_not_poison_later_final_answer():
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "sometimes_missing", "args": {"path": "index.html"}}]),
            AIMessage(content="I checked a different project shape and completed the task."),
        ]
    )
    agent = make_langgraph_agent(model=model, tools=[sometimes_missing])

    result = agent.invoke({"messages": [HumanMessage(content="Inspect this project.")]}, config=config())

    assert result["messages"][-1].content == "I checked a different project shape and completed the task."
    assert "I could not complete the task" not in result["messages"][-1].content


def test_edit_requires_validation_before_final_success():
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "read_file", "args": {"path": "a.txt"}}]),
            AIMessage(content="", tool_calls=[{"id": "call-2", "name": "edit_file", "args": {"path": "a.txt", "old_text": "a", "new_text": "b"}}]),
            AIMessage(content="Done without validation."),
            AIMessage(content="Validated after prompt."),
        ]
    )
    agent = make_langgraph_agent(model=model, tools=[fake_read_file, fake_edit_file])
    cfg = config()

    result = agent.invoke({"messages": [HumanMessage(content="Edit the file a.txt.")]}, config=cfg)
    assert "__interrupt__" in result
    result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=cfg)

    assert any(getattr(message, "name", None) == "tomo_validator" for message in result["messages"])
    assert result["messages"][-1].content == "Validated after prompt."


def test_final_answer_cannot_be_emitted_while_validation_is_missing():
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "terminal", "args": {"command": "touch x"}}]),
            AIMessage(content="Done without validation."),
            AIMessage(content="Validation handled."),
        ]
    )
    agent = make_langgraph_agent(model=model, tools=[fake_terminal])
    cfg = config()
    agent.invoke({"messages": [HumanMessage(content="Run a shell command.")]}, config=cfg)

    result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=cfg)

    assert any(getattr(message, "name", None) == "tomo_validator" for message in result["messages"])
    assert result["messages"][-1].content == "Validation handled."
