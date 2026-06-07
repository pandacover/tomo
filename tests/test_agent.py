from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from langchain.agents.middleware import TodoListMiddleware

from tomo.agent import (
    DEFAULT_INTERRUPT_ON,
    EXCLUDED_BUILTIN_TOOLS,
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION_OVERRIDES,
    TomoTodoListMiddleware,
    SKILLS_LOGGER,
    SKILL_SOURCES,
    extract_text,
    workspace_permissions,
)
from tomo import tools
from tomo.tools import get_tools, rank_texts


def test_agent_permissions_allow_outside_workspace_after_dotfile_deny():
    rules = workspace_permissions()

    workspace = Path.cwd().resolve().as_posix()
    assert rules[0].mode == "allow"
    assert rules[0].operations == ["read", "write"]
    assert rules[1].mode == "deny"
    assert "/**/.*" in rules[1].paths
    assert "/**/.*/**" in rules[1].paths
    assert rules[2].mode == "allow"
    assert rules[2].paths == [f"{workspace}/**", workspace]
    assert rules[3].mode == "allow"
    assert rules[3].paths == ["/**"]


def test_agent_interrupts_filesystem_and_terminal_tools_for_approval():
    assert set(DEFAULT_INTERRUPT_ON) == {
        "read_file",
        "glob",
        "terminal",
        "write_file",
        "edit_file",
    }


def test_agent_keeps_safe_deepagents_edit_tools_available():
    assert EXCLUDED_BUILTIN_TOOLS == frozenset({"grep", "ls"})


def test_tomo_todo_middleware_keeps_planning_but_requires_validation():
    middleware = TomoTodoListMiddleware()

    assert isinstance(middleware, TodoListMiddleware)
    assert "write_todos" == middleware.tools[0].name
    assert "does not read, write, or edit project files" in middleware.tool_description
    assert "after suitable validation passed" in middleware.tool_description
    assert "fully finished and validated" in middleware.system_prompt


def test_system_prompt_requires_fenced_markdown_artifacts():
    assert "output that artifact literally inside a fenced code block" in SYSTEM_PROMPT
    assert "Do not emit raw markdown tables" in SYSTEM_PROMPT
    assert "wrap the entire artifact in one fenced code block" in SYSTEM_PROMPT


def test_system_prompt_documents_telegram_lifecycle_commands():
    assert "`uv run tomo telegram start`" in SYSTEM_PROMPT
    assert "`uv run tomo telegram stop`" in SYSTEM_PROMPT
    assert "`uv run tomo telegram restart`" in SYSTEM_PROMPT
    assert "`.tomo/telegram.pid`" in SYSTEM_PROMPT
    assert "`.tomo/telegram.log`" in SYSTEM_PROMPT


def test_agent_overrides_remaining_builtin_tool_descriptions():
    assert set(TOOL_DESCRIPTION_OVERRIDES) == {"read_file", "glob"}


def test_skill_sources_match_cli_precedence():
    assert SKILL_SOURCES == [
        str(Path.home() / ".deepagents" / "tomo" / "skills"),
        str(Path.home() / ".agents" / "skills"),
        str(Path.cwd() / ".deepagents" / "skills"),
        str(Path.cwd() / ".agents" / "skills"),
    ]


def test_project_tools_include_primitive_file_tools_and_search():
    names = {tool.name for tool in get_tools()}
    assert names == {
        "files_search",
        "terminal",
        "web_search",
        "web_fetch",
        "append_memory",
        "read_memory",
        "schedule_task",
    }


def test_rank_texts_prefers_relevant_results():
    results = rank_texts("memory search", ["irrelevant line", "memory uses bm25 search"], k=1)
    assert results == ["memory uses bm25 search"]


def test_files_search_treats_rg_exit_one_as_no_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    assert tools.files_search.invoke({"query": "missing"}) == "No matches found."


def test_files_search_reports_rg_exit_two_as_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="", stderr="rg: bad pattern"),
    )

    assert tools.files_search.invoke({"query": "["}) == "Error: rg exited with code 2\nrg: bad pattern"


def test_skills_logger_warnings_are_suppressed_in_tui():
    SKILLS_LOGGER.setLevel(logging.ERROR)
    assert SKILLS_LOGGER.getEffectiveLevel() == logging.ERROR


def test_extract_text_does_not_render_empty_internal_state():
    assert extract_text({}) == ""
    assert extract_text({"messages": []}) == ""
