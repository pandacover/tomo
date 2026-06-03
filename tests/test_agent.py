from __future__ import annotations

import logging
from pathlib import Path

from butler.agent import DEFAULT_INTERRUPT_ON, SKILLS_LOGGER, SKILL_SOURCES, workspace_permissions
from butler.tools import get_tools, rank_texts


def test_agent_permissions_deny_dotfiles_before_allowing_workspace():
    rules = workspace_permissions()

    workspace = Path.cwd().resolve().as_posix()
    assert rules[0].mode == "allow"
    assert rules[0].operations == ["read", "write"]
    assert rules[1].mode == "deny"
    assert f"{workspace}/**/.*" in rules[1].paths
    assert f"{workspace}/**/.*/**" in rules[1].paths
    assert rules[2].mode == "allow"
    assert rules[2].paths == [f"{workspace}/**", workspace]
    assert rules[3].mode == "deny"
    assert rules[3].paths == ["/**"]


def test_agent_interrupts_only_sensitive_deepagents_tools():
    assert set(DEFAULT_INTERRUPT_ON) == {"write_file", "edit_file", "write", "edit", "bash"}
    assert "read_file" not in DEFAULT_INTERRUPT_ON
    assert "ls" not in DEFAULT_INTERRUPT_ON
    assert "grep" not in DEFAULT_INTERRUPT_ON
    assert "glob" not in DEFAULT_INTERRUPT_ON


def test_skill_sources_match_cli_precedence():
    assert SKILL_SOURCES == [
        str(Path.home() / ".deepagents" / "butler" / "skills"),
        str(Path.home() / ".agents" / "skills"),
        str(Path.cwd() / ".deepagents" / "skills"),
        str(Path.cwd() / ".agents" / "skills"),
    ]


def test_project_tools_include_primitive_file_tools_and_search():
    names = {tool.name for tool in get_tools()}
    assert {"read", "write", "edit", "search", "bash", "web_search", "web_fetch", "append_memory", "read_memory"} <= names


def test_rank_texts_prefers_relevant_results():
    results = rank_texts("memory search", ["irrelevant line", "memory uses bm25 search"], k=1)
    assert results == ["memory uses bm25 search"]


def test_skills_logger_warnings_are_suppressed_in_tui():
    SKILLS_LOGGER.setLevel(logging.ERROR)
    assert SKILLS_LOGGER.getEffectiveLevel() == logging.ERROR
