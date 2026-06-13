from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus, urlparse, urlunparse

from langchain_core.tools import tool

from .browser_tools import (
    AgentBrowserResult,
    format_error,
    operation_timeout_s,
    resolve_agent_browser_command,
    run_agent_browser_command,
    run_agent_browser_for_session,
    truncate,
)


SocialPlatform = Literal["x"]
SocialAction = Literal[
    "status",
    "login_start",
    "connect_chrome",
    "login_check",
    "open",
    "read_page",
    "search",
    "draft_post",
    "draft_reply",
    "publish_post",
    "publish_reply",
    "close",
    "logout",
]

SOCIAL_ROOT = Path(".tomo") / "social-browser"
X_SESSION = "tomo-social-x"
DASHBOARD_PORT = 4848
CHROME_DEBUG_PORT = 9222
X_ALLOWED_DOMAINS = (
    "x.com",
    "twitter.com",
    "api.x.com",
    "abs.twimg.com",
    "pbs.twimg.com",
    "video.twimg.com",
)
X_LOGIN_URL = "https://x.com/i/flow/login"
X_HOME_URL = "https://x.com/home"
X_COMPOSE_URL = "https://x.com/compose/post"
X_TEXT_EDITOR_SELECTOR = '[data-testid="tweetTextarea_0"]'
X_POST_BUTTON_SELECTOR = '[data-testid="tweetButton"]'
X_REPLY_BUTTON_SELECTOR = '[data-testid="reply"]'
X_REPLY_SEND_SELECTOR = '[data-testid="tweetButtonInline"]'


@dataclass(frozen=True)
class SocialBrowserConfig:
    platform: Literal["x"]
    session: str
    profile_dir: Path | None
    allowed_domains: tuple[str, ...]
    dashboard_port: int = DASHBOARD_PORT
    chrome_debug_port: int = CHROME_DEBUG_PORT
    auto_connect: bool = False


def x_config() -> SocialBrowserConfig:
    return SocialBrowserConfig(
        platform="x",
        session=X_SESSION,
        profile_dir=None,
        allowed_domains=X_ALLOWED_DOMAINS,
    )


def normalize_x_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https":
        raise ValueError("X URLs must use https.")
    if parsed.username or parsed.password:
        raise ValueError("X URLs must not include credentials.")
    host = (parsed.hostname or "").lower()
    if host not in {"x.com", "twitter.com"}:
        raise ValueError("Only x.com and twitter.com URLs are supported.")
    if parsed.fragment and "script" in parsed.fragment.lower():
        raise ValueError("X URL fragment is not allowed.")
    netloc = "x.com"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    normalized = parsed._replace(scheme="https", netloc=netloc, fragment="")
    return urlunparse(normalized)


def is_allowed_x_url(url: str) -> bool:
    try:
        normalize_x_url(url)
    except ValueError:
        return False
    return True


def is_status_url(url: str) -> bool:
    path_parts = [part for part in urlparse(normalize_x_url(url)).path.split("/") if part]
    return len(path_parts) >= 3 and path_parts[-2] == "status" and path_parts[-1].isdigit()


class XSocialAdapter:
    def __init__(self, config: SocialBrowserConfig | None = None, timeout_ms: int = 30_000) -> None:
        self.config = config or x_config()
        self.timeout_ms = timeout_ms

    def status(self) -> str:
        current_url = self._run("get", "url", check=False)
        url_text = current_url.stdout.strip() if current_url.returncode == 0 else "(not active)"
        login = self._detect_login()
        profile = str(self.config.profile_dir) if self.config.profile_dir is not None else "(ephemeral memory-only session)"
        return (
            f"Platform: x\n"
            f"Profile: {profile}\n"
            f"Session: {self.config.session}\n"
            f"Current URL: {url_text or '(unknown)'}\n"
            f"Login: {login}"
        )

    def login_start(self) -> str:
        result = start_chrome_incognito_login(self.config.chrome_debug_port)
        if result.returncode != 0:
            return format_error("social chrome_login_start", result)
        return (
            "Opened X login in a real Chrome incognito window with local debugging enabled.\n"
            'Finish login there, then tell me "check x login". '
            "Tomo will attach to that running Chrome session without saving a cookie file."
        )

    def connect_chrome(self) -> str:
        login = self._detect_login(include_excerpt=True)
        if login.startswith("unknown"):
            return (
                f"{login}\n\n"
                "Start Chrome with remote debugging, log into X there, then retry.\n"
                "PowerShell example:\n"
                '& "$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe" '
                '--remote-debugging-port=9222 --user-data-dir="$env:TEMP\\tomo-x-chrome"'
            )
        return login

    def login_check(self) -> str:
        return self._detect_login(include_excerpt=True)

    def open(self, url: str) -> str:
        normalized = normalize_x_url(url)
        error = self._open_x(normalized)
        if error:
            return error
        return self._page_status()

    def read_page(self, url: str) -> str:
        normalized = normalize_x_url(url)
        error = self._open_x(normalized)
        if error:
            return error
        result = self._run("get", "text", "body", check=False)
        if result.returncode != 0:
            return format_error("social read_page", result)
        return truncate(result.stdout)

    def search(self, query: str) -> str:
        query = query.strip()
        if not query:
            return "Error: social_browser search requires query."
        url = f"https://x.com/search?q={quote_plus(query)}&src=typed_query"
        error = self._open_x(url)
        if error:
            return error
        result = self._run("get", "text", "body", check=False)
        if result.returncode != 0:
            return format_error("social search", result)
        return truncate(result.stdout)

    def draft_post(self, text: str) -> str:
        text = text.strip()
        if not text:
            return "Error: social_browser draft_post requires text."
        error = self._open_x(X_COMPOSE_URL)
        if error:
            return error
        filled = self._run("fill", X_TEXT_EDITOR_SELECTOR, text, check=False)
        if filled.returncode != 0:
            return self._selector_failure("draft_post", filled)
        return "Draft post filled in X compose. It has not been published."

    def draft_reply(self, reply_to_url: str, text: str) -> str:
        if not reply_to_url:
            return "Error: social_browser draft_reply requires reply_to_url."
        normalized = normalize_x_url(reply_to_url)
        if not is_status_url(normalized):
            return "Error: social_browser draft_reply requires an X status URL."
        text = text.strip()
        if not text:
            return "Error: social_browser draft_reply requires text."
        error = self._open_x(normalized)
        if error:
            return error
        clicked = self._run("click", X_REPLY_BUTTON_SELECTOR, check=False)
        if clicked.returncode != 0:
            return self._selector_failure("draft_reply click", clicked)
        self._run("wait", "1000", check=False)
        filled = self._run("fill", X_TEXT_EDITOR_SELECTOR, text, check=False)
        if filled.returncode != 0:
            return self._selector_failure("draft_reply fill", filled)
        return "Draft reply filled in X. It has not been published."

    def publish_post(self) -> str:
        result = self._run("click", X_POST_BUTTON_SELECTOR, check=False)
        if result.returncode != 0:
            return self._selector_failure("publish_post", result)
        return "Approved X post action was submitted."

    def publish_reply(self) -> str:
        result = self._run("click", X_REPLY_SEND_SELECTOR, check=False)
        if result.returncode != 0:
            return self._selector_failure("publish_reply", result)
        return "Approved X reply action was submitted."

    def close(self) -> str:
        result = self._run("close", check=False)
        if result.returncode != 0:
            return format_error("social close", result)
        return "X social browser session closed."

    def logout(self) -> str:
        self.close()
        state_path = SOCIAL_ROOT / "state" / "x.json"
        profile_dir = SOCIAL_ROOT / "profiles" / "x"
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        if self.config.profile_dir is not None and self.config.profile_dir.exists():
            shutil.rmtree(self.config.profile_dir)
        state_path.unlink(missing_ok=True)
        return "Closed Tomo's X browser session and deleted any leftover managed X profile/state."

    def _open_x(self, url: str) -> str | None:
        result = self._run("open", normalize_x_url(url), check=False)
        if result.returncode != 0:
            return format_error("social open", result)
        self._run("wait", "--load", "domcontentloaded", check=False)
        return None

    def _detect_login(self, *, include_excerpt: bool = False, auto_connect: bool = False) -> str:
        text_result = self._run("get", "text", "body", auto_connect=auto_connect, check=False)
        if text_result.returncode != 0:
            return "unknown - social browser is not active or X is not loaded"
        text = text_result.stdout.strip()
        lowered = text.lower()
        logged_out_markers = ("sign in to x", "log in to x", "sign up", "create your account")
        logged_in_markers = ("what is happening?!", "what's happening?", "home", "messages", "notifications")
        if any(marker in lowered for marker in logged_out_markers):
            status = "not logged in"
        elif any(marker in lowered for marker in logged_in_markers):
            status = "logged in"
        else:
            status = "unknown - X may be showing verification or an unexpected page"
        if not include_excerpt:
            return status
        return f"Login: {status}\n\nVisible text excerpt:\n{truncate(text, 2_000)}"

    def _page_status(self) -> str:
        url = self._run("get", "url", check=False)
        title = self._run("get", "title", check=False)
        url_text = url.stdout.strip() if url.returncode == 0 else "(unknown)"
        title_text = title.stdout.strip() if title.returncode == 0 else "(unknown)"
        return f"URL: {url_text}\nTitle: {title_text}"

    def _selector_failure(self, action: str, result: AgentBrowserResult) -> str:
        status = self._page_status()
        text = self._run("get", "text", "body", check=False)
        excerpt = truncate(text.stdout.strip(), 2_000) if text.returncode == 0 else "(unable to read page text)"
        return f"{format_error(f'social {action}', result)}\n{status}\nVisible text excerpt:\n{excerpt}"

    def _run(
        self,
        *args: str,
        headed: bool = False,
        auto_connect: bool = False,
        timeout_ms: int | None = None,
        check: bool = True,
    ) -> AgentBrowserResult:
        if self.config.profile_dir is not None:
            self.config.profile_dir.parent.mkdir(parents=True, exist_ok=True)
        effective_timeout_ms = timeout_ms if timeout_ms is not None else self.timeout_ms
        use_cdp = self.config.profile_dir is None and self.config.chrome_debug_port is not None
        return run_agent_browser_for_session(
            self.config.session,
            *args,
            profile=self.config.profile_dir,
            headed=headed,
            auto_connect=False if use_cdp else auto_connect or self.config.auto_connect,
            cdp_port=self.config.chrome_debug_port if use_cdp else None,
            allowed_domains=self.config.allowed_domains,
            timeout_s=operation_timeout_s(effective_timeout_ms),
            check=check,
        )


def start_dashboard(port: int = DASHBOARD_PORT) -> AgentBrowserResult:
    command = [*resolve_agent_browser_command(), "dashboard", "start", "--port", str(port)]
    return run_agent_browser_command(command, timeout_s=15, check=False)


def start_chrome_incognito_login(port: int = CHROME_DEBUG_PORT) -> AgentBrowserResult:
    chrome = find_chrome_executable()
    if chrome is None:
        return AgentBrowserResult(
            1,
            "",
            "Chrome executable not found. Install Chrome or start it manually with --remote-debugging-port=9222.",
        )
    user_data_dir = Path(os.environ.get("TEMP", ".")).expanduser() / "tomo-x-chrome"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(chrome),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--incognito",
        X_LOGIN_URL,
    ]
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        return AgentBrowserResult(1, "", str(exc))
    return AgentBrowserResult(0, f"Started Chrome on debugging port {port}.", "")


def find_chrome_executable() -> Path | None:
    candidates = [
        os.environ.get("CHROME_PATH"),
        os.environ.get("PROGRAMFILES", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("PROGRAMFILES(X86)", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path
    return None


@tool("social_browser")
def social_browser(
    platform: SocialPlatform,
    action: SocialAction,
    url: str | None = None,
    query: str | None = None,
    text: str | None = None,
    reply_to_url: str | None = None,
    timeout_ms: int = 30_000,
) -> str:
    """Use Tomo's managed logged-in social browser. First version supports X only.

Use this for authenticated X access. Never ask for account passwords. Draft before
publish; publish/logout/login actions require explicit approval in Tomo.
"""
    if platform != "x":
        return "Error: social_browser currently supports only platform='x'."
    adapter = XSocialAdapter(timeout_ms=timeout_ms)
    try:
        match action:
            case "status":
                return adapter.status()
            case "login_start":
                return adapter.login_start()
            case "connect_chrome":
                return adapter.connect_chrome()
            case "login_check":
                return adapter.login_check()
            case "open":
                if not url:
                    return "Error: social_browser open requires url."
                return adapter.open(url)
            case "read_page":
                if not url:
                    return "Error: social_browser read_page requires url."
                return adapter.read_page(url)
            case "search":
                if query is None:
                    return "Error: social_browser search requires query."
                return adapter.search(query)
            case "draft_post":
                if text is None:
                    return "Error: social_browser draft_post requires text."
                return adapter.draft_post(text)
            case "draft_reply":
                target = reply_to_url or url
                if not target or text is None:
                    return "Error: social_browser draft_reply requires reply_to_url and text."
                return adapter.draft_reply(target, text)
            case "publish_post":
                return adapter.publish_post()
            case "publish_reply":
                return adapter.publish_reply()
            case "close":
                return adapter.close()
            case "logout":
                return adapter.logout()
            case _:
                return f"Error: unknown social_browser action {action}."
    except ValueError as exc:
        return f"Error: {exc}"
    except RuntimeError as exc:
        return f"Error: social_browser {action} failed: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error: social_browser {action} failed: {exc}"
