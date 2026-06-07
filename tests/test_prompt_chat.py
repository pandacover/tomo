from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

from prompt_toolkit.widgets import TextArea

from tomo.session_store import create_session
from tomo.tools import ApprovalRequest
from tomo.tui import PromptChat


class FakeApp:
    def __init__(self) -> None:
        self.invalidations = 0
        self.exited = False

    def run(self) -> None:
        return None

    def exit(self) -> None:
        self.exited = True

    def invalidate(self) -> None:
        self.invalidations += 1


def make_chat(session=None) -> PromptChat:
    return PromptChat(session=session or create_session(), agent=Mock(), app=FakeApp())


def test_prompt_chat_uses_bottom_input_text_area():
    chat = make_chat()

    assert isinstance(chat.input, TextArea)
    assert chat.input.window.height.preferred == 1
    assert chat.input.buffer.multiline() is False
    assert chat.input.accept_handler == chat.handle_input


def test_prompt_chat_output_is_scrollable_and_bottom_pinned():
    chat = make_chat()
    chat.output.text = "\n".join(f"line {index}" for index in range(50))

    chat.scroll_chat_to_bottom()
    assert chat.output.window.vertical_scroll == 49

    chat.scroll_chat(-10)
    assert chat.output.window.vertical_scroll == 39

    chat.scroll_chat_to_top()
    assert chat.output.window.vertical_scroll == 0

    chat.scroll_chat(-10)
    assert chat.output.window.vertical_scroll == 0


def test_prompt_chat_approval_accepts_yes_answers():
    chat = make_chat()
    result: dict[str, bool] = {}

    thread = threading.Thread(
        target=lambda: result.setdefault("approved", chat._approval_handler(ApprovalRequest("write", ".env", "touches dotfile")))
    )
    thread.start()
    assert chat.pending_approval is not None

    chat.resolve_approval_text("y")
    thread.join(timeout=1)

    assert result["approved"] is True
    assert chat.pending_approval is None


def test_prompt_chat_approval_rejects_no_answers():
    chat = make_chat()
    result: dict[str, bool] = {}

    thread = threading.Thread(
        target=lambda: result.setdefault("approved", chat._approval_handler(ApprovalRequest("write", ".env", "touches dotfile")))
    )
    thread.start()
    assert chat.pending_approval is not None

    chat.resolve_approval_text("n")
    thread.join(timeout=1)

    assert result["approved"] is False
    assert chat.pending_approval is None


def test_prompt_chat_processes_clear_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = create_session()
    session.messages.append({"role": "user", "content": "hi"})
    chat = make_chat(session=session)

    result = chat.handle_command("/clear")

    assert result is True
    assert session.messages == []


def test_prompt_chat_debug_tool_command_toggles_full_tool_output():
    chat = make_chat()

    assert chat.handle_command("/debug-tool enable") is True
    assert chat.debug_tool is True
    assert "Tool debug output enabled." in chat.output.text

    assert chat.handle_command("/debug-tool disable") is True
    assert chat.debug_tool is False
    assert "Tool debug output disabled." in chat.output.text


def test_prompt_chat_selects_saved_session_by_number(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    current = create_session("Current")
    older = create_session("Older")
    older.messages.append({"role": "user", "content": "from older"})
    newer = create_session("Newer")
    newer.messages.append({"role": "assistant", "content": "from newer"})

    from tomo.session_store import save_session

    save_session(older)
    save_session(newer)
    chat = make_chat(session=current)

    assert chat.handle_command("/session") is True
    assert chat.awaiting_session_selection is True

    first_choice_id = chat.session_choices[0].metadata.id
    chat.handle_input(type("Buffer", (), {"text": "1"})())

    assert chat.session.metadata.id == first_choice_id
    assert chat.awaiting_session_selection is False
    assert "System: Loaded session:" in chat.output.text


def test_prompt_chat_adds_dollar_skill_context(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "planner"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: planner\n---\nUse planning.", encoding="utf-8")
    monkeypatch.setattr("tomo.tui.SKILL_SOURCES", [str(tmp_path / "skills")])
    chat = make_chat()

    content = chat.add_skill_context("Use $planner for this")

    assert '<skill_context name="planner"' in content
    assert skill_file.as_posix() in content
    assert "Use planning." in content
    assert "User prompt:\nUse $planner for this" in content


def test_prompt_chat_dollar_skill_uses_highest_precedence_source(tmp_path, monkeypatch):
    low = tmp_path / "low" / "planner"
    high = tmp_path / "high" / "planner"
    low.mkdir(parents=True)
    high.mkdir(parents=True)
    (low / "SKILL.md").write_text("low", encoding="utf-8")
    (high / "SKILL.md").write_text("high", encoding="utf-8")
    monkeypatch.setattr("tomo.tui.SKILL_SOURCES", [str(tmp_path / "low"), str(tmp_path / "high")])
    chat = make_chat()

    content = chat.add_skill_context("Use $planner")

    assert "high" in content
    assert "low" not in content


def test_prompt_chat_sends_skill_context_without_storing_it(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "planner"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("skill body", encoding="utf-8")
    monkeypatch.setattr("tomo.tui.SKILL_SOURCES", [str(tmp_path / "skills")])
    class InvokeOnlyAgent:
        def __init__(self) -> None:
            self.invoke = Mock(return_value={"messages": [{"role": "assistant", "content": "done"}]})

    agent = InvokeOnlyAgent()
    chat = PromptChat(session=create_session(), agent=agent, app=FakeApp())

    chat.send_message("Help with $planner")

    sent_content = agent.invoke.call_args.args[0]["messages"][-1]["content"]
    assert "skill body" in sent_content
    assert chat.session.messages[0] == {"role": "user", "content": "Help with $planner"}


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
                            ],
                            "review_configs": [{"action_name": "write", "allowed_decisions": ["approve", "reject"]}],
                        }
                    )
                ]
            }
        return {"messages": [{"role": "assistant", "content": "done"}]}


def test_prompt_chat_resumes_interrupt_with_approval():
    agent = InterruptingAgent()
    chat = PromptChat(session=create_session(), agent=agent, app=FakeApp())
    result: dict[str, object] = {}

    thread = threading.Thread(target=lambda: result.setdefault("value", chat.invoke_agent_with_approvals({"messages": []})))
    thread.start()
    assert chat.pending_approval is not None

    chat.resolve_approval_text("y")
    thread.join(timeout=1)

    assert result["value"] == {"messages": [{"role": "assistant", "content": "done"}]}
    assert len(agent.calls) == 2
    assert agent.calls[1].resume == {"decisions": [{"type": "approve"}]}
