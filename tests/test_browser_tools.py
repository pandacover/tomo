from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from tomo import browser_tools
from tomo.browser_tools import AgentBrowserResult, browser


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.responses: dict[str, AgentBrowserResult] = {}
        self.default = AgentBrowserResult(returncode=0, stdout="OK", stderr="")

    def __call__(self, *args: str, **kwargs) -> AgentBrowserResult:
        self.calls.append((args, kwargs))
        key = " ".join(args)
        return self.responses.get(key, self.default)


def configure_runner(monkeypatch) -> RecordingRunner:
    runner = RecordingRunner()
    monkeypatch.setattr(browser_tools, "run_agent_browser", runner)
    return runner


def test_browser_navigate_opens_page_and_waits(monkeypatch):
    runner = configure_runner(monkeypatch)
    runner.responses = {
        "set viewport 1440 1000": AgentBrowserResult(0, "", ""),
        "open https://example.com": AgentBrowserResult(0, "", ""),
        "wait --load domcontentloaded": AgentBrowserResult(0, "", ""),
        "get url": AgentBrowserResult(0, "https://example.com", ""),
        "get title": AgentBrowserResult(0, "Example Domain", ""),
    }

    result = browser.invoke({"action": "navigate", "url": "https://example.com"})

    assert result == "URL: https://example.com\nTitle: Example Domain"
    assert ("open", "https://example.com") in [call[0] for call in runner.calls]


def test_browser_snapshot_returns_interactive_tree(monkeypatch):
    runner = configure_runner(monkeypatch)
    runner.responses = {
        "set viewport 1440 1000": AgentBrowserResult(0, "", ""),
        "snapshot -i -c": AgentBrowserResult(0, "@e1 [button] \"Go\"", ""),
    }

    result = browser.invoke({"action": "snapshot"})

    assert "@e1" in result
    assert ("snapshot", "-i", "-c") in [call[0] for call in runner.calls]


def test_browser_click_fill_scroll_and_text(monkeypatch):
    runner = configure_runner(monkeypatch)
    runner.default = AgentBrowserResult(0, "OK", "")
    runner.responses = {
        "set viewport 1440 1000": AgentBrowserResult(0, "", ""),
        "get url": AgentBrowserResult(0, "https://example.com", ""),
        "get title": AgentBrowserResult(0, "Fake Title", ""),
        "get text main": AgentBrowserResult(0, "body text timeout=10000", ""),
    }

    assert "Fake Title" in browser.invoke({"action": "click", "selector": "#go"})
    assert "Fake Title" in browser.invoke({"action": "fill", "selector": "#name", "text": "Tomo"})
    assert "Fake Title" in browser.invoke({"action": "scroll", "scroll_y": 300})
    assert browser.invoke({"action": "text", "selector": "main"}) == "body text timeout=10000"

    assert ("click", "#go") in [call[0] for call in runner.calls]
    assert ("fill", "#name", "Tomo") in [call[0] for call in runner.calls]
    assert ("scroll", "down", "300") in [call[0] for call in runner.calls]


def test_browser_screenshot_writes_requested_path(tmp_path, monkeypatch):
    runner = configure_runner(monkeypatch)
    target = tmp_path / "shot.png"
    runner.responses = {
        "set viewport 1440 1000": AgentBrowserResult(0, "", ""),
        "get url": AgentBrowserResult(0, "https://example.com", ""),
        "get title": AgentBrowserResult(0, "Fake Title", ""),
        f"screenshot {target.as_posix()}": AgentBrowserResult(0, "", ""),
    }

    result = browser.invoke({"action": "screenshot", "path": target.as_posix(), "full_page": False})

    assert result == f"Screenshot saved: {target}\nURL: https://example.com\nTitle: Fake Title"
    assert any(call[0][0] == "screenshot" for call in runner.calls)


def test_browser_screenshot_rejects_blank_page(monkeypatch):
    runner = configure_runner(monkeypatch)
    runner.responses = {
        "get url": AgentBrowserResult(0, "about:blank", ""),
    }

    result = browser.invoke({"action": "screenshot"})

    assert result == (
        "Error: browser screenshot requires a navigated page. "
        "Call browser navigate first or pass url with the screenshot action."
    )
    assert not any(call[0][0] == "screenshot" for call in runner.calls)


def test_browser_screenshot_with_url_navigates_before_capture(tmp_path, monkeypatch):
    runner = configure_runner(monkeypatch)
    target = tmp_path / "shot.png"
    runner.responses = {
        "set viewport 1440 1000": AgentBrowserResult(0, "", ""),
        "open https://example.com": AgentBrowserResult(0, "", ""),
        "wait --load domcontentloaded": AgentBrowserResult(0, "", ""),
        "wait 1000": AgentBrowserResult(0, "", ""),
        "get url": AgentBrowserResult(0, "https://example.com", ""),
        "get title": AgentBrowserResult(0, "Fake Title", ""),
        f"screenshot --full {target.as_posix()}": AgentBrowserResult(0, "", ""),
    }

    result = browser.invoke({"action": "screenshot", "url": "https://example.com", "path": target.as_posix()})

    assert result == f"Screenshot saved: {target}\nURL: https://example.com\nTitle: Fake Title"
    assert ("open", "https://example.com") in [call[0] for call in runner.calls]
    assert ("wait", "1000") in [call[0] for call in runner.calls]


def test_browser_text_with_url_navigates_before_reading(monkeypatch):
    runner = configure_runner(monkeypatch)
    runner.responses = {
        "set viewport 1440 1000": AgentBrowserResult(0, "", ""),
        "open https://example.com": AgentBrowserResult(0, "", ""),
        "wait --load domcontentloaded": AgentBrowserResult(0, "", ""),
        "wait 1000": AgentBrowserResult(0, "", ""),
        "get text main": AgentBrowserResult(0, "body text timeout=10000", ""),
    }

    result = browser.invoke({"action": "text", "url": "https://example.com", "selector": "main"})

    assert result == "body text timeout=10000"
    assert ("open", "https://example.com") in [call[0] for call in runner.calls]
    assert ("get", "text", "main") in [call[0] for call in runner.calls]


def test_browser_batch_runs_multiple_commands(monkeypatch):
    runner = configure_runner(monkeypatch)
    runner.responses = {
        "set viewport 1440 1000": AgentBrowserResult(0, "", ""),
        "batch --bail open https://example.com wait --load domcontentloaded snapshot -i": AgentBrowserResult(
            0,
            "snapshot complete",
            "",
        ),
    }

    result = browser.invoke(
        {
            "action": "batch",
            "commands": ["open https://example.com", "wait --load domcontentloaded", "snapshot -i"],
        }
    )

    assert result == "snapshot complete"
    assert (
        "batch",
        "--bail",
        "open https://example.com",
        "wait --load domcontentloaded",
        "snapshot -i",
    ) in [call[0] for call in runner.calls]


def test_browser_sessions_are_thread_local(monkeypatch):
    seen_sessions: list[str] = []

    def fake_run(*args: str, **kwargs):
        seen_sessions.append(browser_tools.current_session_name())
        if args[:2] == ("get", "title"):
            return AgentBrowserResult(0, "Thread Title", "")
        return AgentBrowserResult(0, "", "")

    monkeypatch.setattr(browser_tools, "run_agent_browser", fake_run)
    browser_tools.reset_all_browser_sessions()

    assert browser.invoke({"action": "title"}) == "Thread Title"

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(lambda: browser_tools.browser.invoke({"action": "title"})).result()

    assert result == "Thread Title"
    assert len({session for session in seen_sessions}) == 2


def test_browser_sessions_are_keyed_by_thread_object_not_reused_ident(monkeypatch):
    class FakeThread:
        __slots__ = ("__weakref__",)

    first_thread = FakeThread()
    second_thread = FakeThread()
    monkeypatch.setattr(threading, "current_thread", lambda: first_thread)
    first_session = browser_tools.current_session_name()

    monkeypatch.setattr(threading, "current_thread", lambda: second_thread)
    second_session = browser_tools.current_session_name()

    assert second_session is not first_session


def test_resolve_agent_browser_prefers_repo_local_binary(monkeypatch, tmp_path):
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    local_bin = bin_dir / ("agent-browser.cmd" if __import__("platform").system() == "Windows" else "agent-browser")
    local_bin.write_text("", encoding="utf-8")
    monkeypatch.setattr(browser_tools, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("AGENT_BROWSER_BIN", raising=False)

    command = browser_tools.resolve_agent_browser_command()

    assert command == [local_bin.as_posix()]