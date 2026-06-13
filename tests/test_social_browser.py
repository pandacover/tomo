from __future__ import annotations

from pathlib import Path

import pytest

from tomo import social_browser
from tomo.browser_tools import AgentBrowserResult, build_agent_browser_command_for_session
from tomo.social_browser import X_ALLOWED_DOMAINS, X_SESSION, XSocialAdapter, is_allowed_x_url, normalize_x_url


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses: dict[tuple[str, ...], AgentBrowserResult] = {}
        self.default = AgentBrowserResult(0, "OK", "")

    def __call__(self, session: str, *args: str, **kwargs) -> AgentBrowserResult:
        self.calls.append({"session": session, "args": args, "kwargs": kwargs})
        return self.responses.get(args, self.default)


def test_build_social_agent_browser_command_uses_session_and_allowed_domains_without_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(social_browser, "SOCIAL_ROOT", tmp_path / ".tomo" / "social-browser")
    profile = social_browser.x_config().profile_dir
    monkeypatch.setattr("tomo.browser_tools.resolve_agent_browser_command", lambda: ["agent-browser"])

    command = build_agent_browser_command_for_session(
        X_SESSION,
        "open",
        "https://x.com/i/flow/login",
        profile=profile,
        headed=False,
        allowed_domains=X_ALLOWED_DOMAINS,
    )

    assert command == [
        "agent-browser",
        "--session",
        "tomo-social-x",
        "--allowed-domains",
        ",".join(X_ALLOWED_DOMAINS),
        "open",
        "https://x.com/i/flow/login",
    ]


def test_normalize_x_url_converts_twitter_status_url():
    assert normalize_x_url("https://twitter.com/user/status/123") == "https://x.com/user/status/123"


@pytest.mark.parametrize("url", ["http://x.com/user", "https://evil.com/user"])
def test_rejects_disallowed_x_urls(url):
    assert not is_allowed_x_url(url)
    with pytest.raises(ValueError):
        normalize_x_url(url)


def test_login_start_opens_real_chrome_incognito_debug_login(tmp_path, monkeypatch):
    chrome_calls: list[int] = []
    monkeypatch.setattr(social_browser, "SOCIAL_ROOT", tmp_path / ".tomo" / "social-browser")
    monkeypatch.setattr(
        social_browser,
        "start_chrome_incognito_login",
        lambda port=9222: chrome_calls.append(port) or AgentBrowserResult(0, "started", ""),
    )

    result = XSocialAdapter().login_start()

    assert "Opened X login in a real Chrome incognito window" in result
    assert not (tmp_path / ".tomo" / "social-browser" / "profiles").exists()
    assert chrome_calls == [9222]


def test_login_check_reports_not_logged_in(tmp_path, monkeypatch):
    runner = RecordingRunner()
    runner.responses = {
        ("get", "text", "body"): AgentBrowserResult(0, "Sign in to X\nCreate your account", ""),
    }
    monkeypatch.setattr(social_browser, "SOCIAL_ROOT", tmp_path / ".tomo" / "social-browser")
    monkeypatch.setattr(social_browser, "run_agent_browser_for_session", runner)

    result = XSocialAdapter().login_check()

    assert "Login: not logged in" in result


def test_login_check_attaches_to_debug_chrome_without_profile(tmp_path, monkeypatch):
    runner = RecordingRunner()
    runner.responses = {
        ("get", "text", "body"): AgentBrowserResult(0, "Home\nMessages\nNotifications", ""),
    }
    monkeypatch.setattr(social_browser, "SOCIAL_ROOT", tmp_path / ".tomo" / "social-browser")
    monkeypatch.setattr(social_browser, "run_agent_browser_for_session", runner)

    result = XSocialAdapter().login_check()

    assert "Login: logged in" in result
    assert runner.calls[0]["args"] == ("get", "text", "body")
    assert runner.calls[0]["kwargs"]["auto_connect"] is False
    assert runner.calls[0]["kwargs"]["cdp_port"] == 9222
    assert runner.calls[0]["kwargs"]["profile"] is None
    assert ("open", "https://x.com/home") not in [call["args"] for call in runner.calls]


def test_connect_chrome_attaches_to_debug_chrome_without_profile(tmp_path, monkeypatch):
    runner = RecordingRunner()
    runner.responses = {
        ("get", "text", "body"): AgentBrowserResult(0, "Home\nMessages\nNotifications", ""),
    }
    monkeypatch.setattr(social_browser, "SOCIAL_ROOT", tmp_path / ".tomo" / "social-browser")
    monkeypatch.setattr(social_browser, "run_agent_browser_for_session", runner)

    result = XSocialAdapter().connect_chrome()

    assert "Login: logged in" in result
    assert runner.calls[0]["args"] == ("get", "text", "body")
    assert runner.calls[0]["kwargs"]["auto_connect"] is False
    assert runner.calls[0]["kwargs"]["cdp_port"] == 9222
    assert runner.calls[0]["kwargs"]["profile"] is None


def test_social_x_commands_do_not_combine_auto_connect_and_cdp(tmp_path, monkeypatch):
    runner = RecordingRunner()
    monkeypatch.setattr(social_browser, "SOCIAL_ROOT", tmp_path / ".tomo" / "social-browser")
    monkeypatch.setattr(social_browser, "run_agent_browser_for_session", runner)

    XSocialAdapter().open("https://x.com/home")

    first_call = runner.calls[0]
    assert first_call["kwargs"]["auto_connect"] is False
    assert first_call["kwargs"]["cdp_port"] == 9222


def test_draft_post_refuses_empty_text():
    assert XSocialAdapter().draft_post("  ") == "Error: social_browser draft_post requires text."


def test_draft_reply_requires_status_url():
    result = XSocialAdapter().draft_reply("https://x.com/user", "hello")

    assert result == "Error: social_browser draft_reply requires an X status URL."


def test_publish_post_uses_narrow_click_command(tmp_path, monkeypatch):
    runner = RecordingRunner()
    monkeypatch.setattr(social_browser, "SOCIAL_ROOT", tmp_path / ".tomo" / "social-browser")
    monkeypatch.setattr(social_browser, "run_agent_browser_for_session", runner)

    result = XSocialAdapter().publish_post()

    assert result == "Approved X post action was submitted."
    assert runner.calls == [
        {
            "session": "tomo-social-x",
            "args": ("click", '[data-testid="tweetButton"]'),
            "kwargs": {
                "profile": None,
                "headed": False,
                "auto_connect": False,
                "cdp_port": 9222,
                "allowed_domains": X_ALLOWED_DOMAINS,
                "timeout_s": 45.0,
                "check": False,
            },
        }
    ]


def test_logout_deletes_only_x_profile_and_state(tmp_path, monkeypatch):
    runner = RecordingRunner()
    root = tmp_path / ".tomo" / "social-browser"
    x_profile = root / "profiles" / "x"
    other_profile = root / "profiles" / "linkedin"
    x_state = root / "state" / "x.json"
    x_profile.mkdir(parents=True)
    other_profile.mkdir(parents=True)
    x_state.parent.mkdir(parents=True)
    (x_profile / "cookie").write_text("secret", encoding="utf-8")
    (other_profile / "cookie").write_text("keep", encoding="utf-8")
    x_state.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(social_browser, "SOCIAL_ROOT", root)
    monkeypatch.setattr(social_browser, "run_agent_browser_for_session", runner)

    result = XSocialAdapter().logout()

    assert "Closed Tomo's X browser session" in result
    assert not x_profile.exists()
    assert not x_state.exists()
    assert other_profile.exists()
