from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Protocol

from langgraph.types import Command
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.widgets import Label, TextArea

from .agent import SKILL_SOURCES, extract_text, make_agent
from .config import settings
from .reasoning import (
    effective_reasoning_effort,
    effective_show_reasoning_trace,
    format_reasoning_status,
    parse_reasoning_command_args,
    reasoning_usage_message,
    save_preferences,
)
from .gateway import ToolEventEmitter, approval_request_from_actions, extract_agent_trace, extract_interrupts, invoke_agent_streaming
from .session_store import ChatSession, create_session, list_sessions, load_session, save_session
from .slash_commands import SlashCommandCompleter, command_argument, slash_prefix, status_hint, unrecognized_message
from .token_store import ensure_logged_in, load_tokens
from .tools import ApprovalRequest


SKILL_REF_RE = re.compile(r"(?<!\S)\$([A-Za-z0-9_-]+)")


class RunnableApp(Protocol):
    def run(self) -> object: ...
    def exit(self) -> None: ...
    def invalidate(self) -> None: ...


@dataclass
class PendingApproval:
    request: ApprovalRequest
    event: threading.Event = field(default_factory=threading.Event)
    result: bool = False


@dataclass
class PromptChat:
    session: ChatSession
    agent: object
    app: RunnableApp | None = None
    running: bool = True
    busy: bool = False
    pending_approval: PendingApproval | None = None
    awaiting_session_selection: bool = False
    session_choices: list[ChatSession] = field(default_factory=list)
    streaming_speaker: str | None = None
    debug_tool: bool = False
    reasoning_effort: str = field(default_factory=effective_reasoning_effort)
    show_reasoning_trace: bool | None = None

    def __post_init__(self) -> None:
        self.header = Label(self.header_text())
        self.output = TextArea(text="", read_only=True, scrollbar=True, focusable=True, wrap_lines=True)
        self.input = TextArea(
            prompt="You › ",
            multiline=False,
            height=1,
            focus_on_click=True,
            accept_handler=self.handle_input,
            completer=SlashCommandCompleter("chat"),
            complete_while_typing=True,
        )
        self.status = Label(status_hint("chat"))
        self._render_transcript()
        if self.app is None:
            self.app = self._build_app()

    def _build_app(self) -> Application[None]:
        kb = KeyBindings()

        @kb.add("c-c")
        def _quit(_: object) -> None:
            self.stop()

        @kb.add("pageup")
        def _page_up(_: object) -> None:
            self.scroll_chat(-10)

        @kb.add("pagedown")
        def _page_down(_: object) -> None:
            self.scroll_chat(10)


        root = HSplit(
            [
                self.header,
                Window(height=1, char="─"),
                self.output,
                Window(height=1, char="─"),
                self.status,
                self.input,
            ]
        )
        output = DummyOutput() if not sys.stdout.isatty() else None
        return Application(layout=Layout(root, focused_element=self.input), key_bindings=kb, full_screen=True, mouse_support=True, output=output)

    def run(self) -> None:
        assert self.app is not None
        self.app.run()

    def stop(self) -> None:
        self.running = False
        if self.app is not None:
            self.app.exit()

    def header_text(self) -> str:
        return f"Tomo · project chat · {settings.model} · {self.session.metadata.name} · {self.session.metadata.id[:8]}"

    def handle_input(self, buffer: object) -> bool:
        text = getattr(buffer, "text", "").strip()
        setattr(buffer, "text", "")
        if not text:
            return True
        if self.pending_approval is not None:
            self.resolve_approval_text(text)
            return True
        if self.awaiting_session_selection:
            self.select_session(text)
            return True
        if self.busy:
            self.append_line("System", "Busy. Wait for Tomo to finish or answer the approval prompt.")
            return True
        if self.handle_command(text):
            return True
        self.start_message_worker(text)
        return True

    def handle_command(self, text: str) -> bool:
        if slash_prefix(text) is None:
            return False
        if text == "/exit":
            self.stop()
            return True
        if text == "/clear":
            self.session.messages.clear()
            save_session(self.session)
            self._render_transcript()
            self.append_line("System", "Session cleared.")
            return True
        if text == "/session":
            self.show_sessions()
            return True
        if slash_prefix(text) == "/debug-tool":
            self.handle_debug_tool_command(text)
            return True
        if slash_prefix(text) == "/reasoning":
            self.handle_reasoning_command(text)
            return True
        self.append_line("System", unrecognized_message(text, "chat"))
        return True

    def handle_debug_tool_command(self, text: str) -> None:
        argument = command_argument(text)
        if argument == "enable":
            self.debug_tool = True
            self.append_line("System", "Tool debug output enabled.")
            return
        if argument == "disable":
            self.debug_tool = False
            self.append_line("System", "Tool debug output disabled.")
            return
        self.append_line("System", "Usage: /debug-tool enable or /debug-tool disable.")

    def handle_reasoning_command(self, text: str) -> None:
        action, value = parse_reasoning_command_args(command_argument(text))
        if action == "status":
            trace = effective_show_reasoning_trace(chat_override=self.show_reasoning_trace)
            self.append_line("System", format_reasoning_status(effort=self.reasoning_effort, trace=trace))
            return
        if action == "effort" and value is not None:
            self.reasoning_effort = value
            save_preferences(reasoning_effort=value)
            self.agent = make_agent(reasoning_effort=value)
            self.header.text = self.header_text()
            self.append_line("System", f"Reasoning effort set to {value}. Applies to subsequent messages.")
            return
        if action == "trace" and value is not None:
            enabled = value == "on"
            self.show_reasoning_trace = enabled
            save_preferences(show_reasoning_summary=enabled)
            self.append_line("System", f"Reasoning trace {'enabled' if enabled else 'disabled'}.")
            return
        self.append_line("System", reasoning_usage_message())

    def show_sessions(self) -> None:
        sessions = list_sessions()
        if not sessions:
            self.append_line("System", "No saved sessions.")
            return
        self.awaiting_session_selection = True
        self.session_choices = sessions
        self.append_line("System", "Saved sessions:")
        for index, session in enumerate(sessions, start=1):
            self.append_line("System", f"{index}. {session.metadata.name} · {session.metadata.updated_date} · {session.metadata.id[:8]}")
        self.status.text = "Type a session number to load it, or press Enter to cancel"
        self.append_line("System", "Type a session number to load it, or press Enter to cancel.")

    def select_session(self, text: str) -> None:
        choice = text.strip()
        if not choice:
            self.awaiting_session_selection = False
            self.session_choices = []
            self.status.text = status_hint("chat")
            self.append_line("System", "Session selection cancelled.")
            return
        try:
            index = int(choice)
        except ValueError:
            self.append_line("System", "Type a session number, or press Enter to cancel.")
            return
        if index < 1 or index > len(self.session_choices):
            self.append_line("System", f"Choose a number from 1 to {len(self.session_choices)}, or press Enter to cancel.")
            return

        selected = self.session_choices[index - 1]
        self.session = load_session(selected.metadata.id)
        self.header.text = self.header_text()
        self.awaiting_session_selection = False
        self.session_choices = []
        self.status.text = status_hint("chat")
        self._render_transcript()
        self.append_line("System", f"Loaded session: {self.session.metadata.name} · {self.session.metadata.id[:8]}")

    def start_message_worker(self, text: str) -> None:
        self.busy = True
        self.status.text = "Thinking..."
        self.append_line("You", text)
        thread = threading.Thread(target=self.send_message, args=(text,), daemon=True)
        thread.start()

    def send_message(self, text: str) -> None:
        self.session.messages.append({"role": "user", "content": text})
        save_session(self.session)
        try:
            messages = [dict(message) for message in self.session.messages]
            messages[-1]["content"] = self.add_skill_context(text)
            streamed_reply_parts: list[str] = []
            result = self.invoke_agent_with_approvals(
                {"messages": messages},
                on_text_delta=lambda delta: (streamed_reply_parts.append(delta), self.append_text_delta("Tomo", delta)),
            )
            reply = extract_text(result)
            trace = extract_agent_trace(result)
        except Exception as exc:  # noqa: BLE001
            self.append_line("Error", str(exc))
        else:
            self.session.messages.append({"role": "assistant", "content": reply})
            save_session(self.session)
            if effective_show_reasoning_trace(chat_override=self.show_reasoning_trace) and trace.reasoning_summary:
                self.append_line("Trace", f"Reasoning summary: {trace.reasoning_summary}")
            if streamed_reply_parts:
                self.streaming_speaker = None
            else:
                self.append_line("Tomo", reply)
        finally:
            self.busy = False
            self.status.text = status_hint("chat")
            self.invalidate()

    def add_skill_context(self, text: str) -> str:
        skills = self.resolve_skill_references(text)
        if not skills:
            return text
        blocks = [
            f'<skill_context name="{name}" path="{path}">\n{content}\n</skill_context>'
            for name, path, content in skills
        ]
        return "\n\n".join([*blocks, f"User prompt:\n{text}"])

    @staticmethod
    def resolve_skill_references(text: str) -> list[tuple[str, str, str]]:
        names = list(dict.fromkeys(SKILL_REF_RE.findall(text)))
        resolved: list[tuple[str, str, str]] = []
        for name in names:
            skill_file = PromptChat.find_skill_file(name)
            if skill_file is None:
                continue
            resolved.append((name, skill_file.as_posix(), skill_file.read_text(encoding="utf-8")))
        return resolved

    @staticmethod
    def find_skill_file(name: str) -> Path | None:
        for source in reversed(SKILL_SOURCES):
            skill_file = Path(source).expanduser() / name / "SKILL.md"
            if skill_file.is_file():
                return skill_file
        return None

    def invoke_agent_with_approvals(self, payload: object, on_text_delta=None) -> object:
        config = {"configurable": {"thread_id": self.session.metadata.id}}
        emitter = ToolEventEmitter(lambda event: self.append_line("Tool", event.render(full_input=self.debug_tool)))
        result = invoke_agent_streaming(self.agent, payload, config, emitter, on_text_delta=on_text_delta)

        while interrupts := self.extract_interrupts(result):
            interrupt_value = interrupts[0].value
            action_requests = interrupt_value.get("action_requests", [])
            if not action_requests:
                break

            request = self.approval_request_from_actions(action_requests)
            approved = self._approval_handler(request)

            decisions = [
                {"type": "approve"} if approved else {"type": "reject", "message": "User denied the tool call."}
                for _ in action_requests
            ]

            result = invoke_agent_streaming(self.agent, Command(resume={"decisions": decisions}), config, emitter, on_text_delta=on_text_delta)

        return result

    @staticmethod
    def extract_interrupts(result: object) -> list[object]:
        return extract_interrupts(result)

    @staticmethod
    def approval_request_from_actions(action_requests: list[object]) -> ApprovalRequest:
        return approval_request_from_actions(action_requests)

    def _approval_handler(self, request: ApprovalRequest) -> bool:
        pending = PendingApproval(request=request)
        self.pending_approval = pending
        self.status.text = "Approval required: type y/yes to approve, n/no/Enter to deny"
        self.append_line("Approval", f"{request.operation} {request.target}\nReason: {request.reason}")
        self.invalidate()
        pending.event.wait()
        self.pending_approval = None
        self.status.text = "Thinking..."
        self.invalidate()
        return pending.result

    def resolve_approval_text(self, text: str) -> None:
        pending = self.pending_approval
        if pending is None:
            return
        answer = text.strip().lower()
        if answer in {"y", "yes"}:
            pending.result = True
            self.append_line("Approval", "Approved.")
            pending.event.set()
            return
        if answer in {"", "n", "no"}:
            pending.result = False
            self.append_line("Approval", "Denied.")
            pending.event.set()
            return
        self.append_line("Approval", "Type y or n.")

    def append_line(self, speaker: str, text: str) -> None:
        self.streaming_speaker = None
        prefix = f"{speaker}: " if speaker else ""
        current = self.output.text
        separator = "\n\n" if current else ""
        self.output.text = f"{current}{separator}{prefix}{text}"
        self.scroll_chat_to_bottom()
        self.invalidate()

    def append_text_delta(self, speaker: str, text: str) -> None:
        if self.streaming_speaker != speaker:
            prefix = f"{speaker}: " if speaker else ""
            current = self.output.text
            separator = "\n\n" if current else ""
            self.output.text = f"{current}{separator}{prefix}{text}"
            self.streaming_speaker = speaker
        else:
            self.output.text += text
        self.scroll_chat_to_bottom()
        self.invalidate()

    def scroll_chat(self, lines: int) -> None:
        window = self.output.window
        window.vertical_scroll = max(0, window.vertical_scroll + lines)
        self.invalidate()

    def scroll_chat_to_top(self) -> None:
        self.output.window.vertical_scroll = 0
        self.invalidate()

    def scroll_chat_to_bottom(self) -> None:
        line_count = max(0, len(self.output.text.splitlines()) - 1)
        self.output.window.vertical_scroll = line_count
        self.invalidate()

    def invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    def _render_transcript(self) -> None:
        if not self.session.messages:
            self.output.text = "Tomo: Ready. Tools enabled: read_file, glob, files_search, terminal."
            self.scroll_chat_to_bottom()
            return
        rendered: list[str] = []
        for message in self.session.messages:
            role = message.get("role", "assistant")
            title = "You" if role == "user" else "Tomo"
            rendered.append(f"{title}: {message.get('content', '')}")
        self.output.text = "\n\n".join(rendered)
        self.scroll_chat_to_bottom()


def run_chat() -> None:
    if not ensure_logged_in():
        return
    session = create_session()
    save_session(session)
    effort = effective_reasoning_effort()
    chat = PromptChat(
        session=session,
        agent=make_agent(reasoning_effort=effort),
        reasoning_effort=effort,
    )
    chat.run()
