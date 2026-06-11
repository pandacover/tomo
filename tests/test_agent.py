from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tomo.agent import SYSTEM_PROMPT, SKILL_SOURCES, extract_text
from tomo import tools
from tomo.tools import get_tools, rank_texts


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


def test_system_prompt_documents_desktop_lifecycle_commands():
    assert "`uv run tomo desktop start`" in SYSTEM_PROMPT
    assert "`uv run tomo desktop stop`" in SYSTEM_PROMPT
    assert "`uv run tomo desktop restart`" in SYSTEM_PROMPT
    assert "`.tomo/desktop.pid`" in SYSTEM_PROMPT
    assert "`.tomo/desktop.log`" in SYSTEM_PROMPT


def test_skill_sources_match_cli_precedence():
    assert SKILL_SOURCES == [
        str(Path.home() / ".agents" / "skills"),
        str(Path.cwd() / ".agents" / "skills"),
        str(Path.cwd() / "skills"),
    ]


def test_browser_tool_skill_exists_in_local_skill_folder():
    skill = Path("skills/browser-tool/SKILL.md")

    assert skill.exists()
    content = skill.read_text(encoding="utf-8")
    assert "name: browser-tool" in content
    assert "Use Tomo's browser tool reliably" in content
    assert "Do not screenshot `about:blank`" in content


def test_project_tools_include_primitive_file_tools_and_search():
    names = {tool.name for tool in get_tools()}
    assert names == {
        "files_search",
        "terminal",
        "browser",
        "web_search",
        "web_fetch",
        "generate_image",
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


def test_files_search_normalizes_multiline_queries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(command, *args, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    assert tools.files_search.invoke({"query": "index.html\npackage.json"}) == "No matches found."
    assert "\n" not in str(seen["command"])


def test_files_search_invokes_rg_without_shell(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(command, *args, **kwargs):
        seen["command"] = command
        seen["shell"] = kwargs.get("shell")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    assert tools.files_search.invoke({"query": "hello world"}) == "No matches found."
    assert seen["command"] == ["rg", "--line-number", "--no-heading", "--smart-case", "hello world", str(tmp_path)]
    assert seen["shell"] is None


def test_extract_text_does_not_render_empty_internal_state():
    assert extract_text({}) == ""
    assert extract_text({"messages": []}) == ""


def test_system_prompt_mentions_browser_for_web_dev_tasks():
    assert "- browser: use a real headless Chromium browser" in SYSTEM_PROMPT
    assert "For web UI/dev tasks, use browser" in SYSTEM_PROMPT


def test_terminal_runs_from_workspace_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = tools.terminal.invoke({"command": "pwd"})

    assert f"CWD: {tmp_path}" in result
    assert str(tmp_path) in result
    assert "Exit code: 0" in result


def test_terminal_uses_powershell_on_windows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools.platform, "system", lambda: "Windows")
    seen: dict[str, object] = {}

    def fake_run(command, *args, **kwargs):
        seen["command"] = command
        seen["shell"] = kwargs.get("shell")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    result = tools.terminal.invoke({"command": "Get-ChildItem"})

    assert seen["command"] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "Get-ChildItem",
    ]
    assert seen["shell"] is False
    assert "Exit code: 0\nok" in result


def test_terminal_uses_bash_on_posix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools.platform, "system", lambda: "Linux")
    seen: dict[str, object] = {}

    def fake_run(command, *args, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    assert "Exit code: 0\nok" in tools.terminal.invoke({"command": "pwd"})
    assert seen["command"] == ["/bin/bash", "-lc", "pwd"]


def test_terminal_accepts_relative_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subdir = tmp_path / "src"
    subdir.mkdir()

    result = tools.terminal.invoke({"command": "pwd", "cwd": "src"})

    assert f"CWD: {subdir}" in result
    assert str(subdir) in result
    assert "Exit code: 0" in result


def test_terminal_rejects_missing_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = tools.terminal.invoke({"command": "pwd", "cwd": "missing"})

    assert result == "Error: cwd missing does not exist."


def test_terminal_rejects_file_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    file_path = tmp_path / "not-dir.txt"
    file_path.write_text("x")

    result = tools.terminal.invoke({"command": "pwd", "cwd": "not-dir.txt"})

    assert result == "Error: cwd not-dir.txt is not a directory."
