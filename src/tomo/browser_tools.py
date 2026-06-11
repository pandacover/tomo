from __future__ import annotations

import atexit
import os
import platform
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from weakref import WeakKeyDictionary

from langchain_core.tools import tool


BrowserAction = Literal[
    "navigate",
    "click",
    "fill",
    "type",
    "press",
    "scroll",
    "screenshot",
    "text",
    "html",
    "evaluate",
    "wait",
    "title",
    "url",
    "reload",
    "back",
    "forward",
    "snapshot",
    "batch",
    "close",
]
DEFAULT_SCREENSHOT_PATH = "browser-screenshot.png"
DEFAULT_TIMEOUT_MS = 10_000
DEFAULT_VIEWPORT = (1440, 1000)
REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_HINT = (
    "Install agent-browser: run `npm install` in the Tomo repo, then "
    "`npx agent-browser install`. Or: `npm install -g agent-browser && agent-browser install`."
)


@dataclass(frozen=True)
class AgentBrowserResult:
    returncode: int
    stdout: str
    stderr: str


_sessions: WeakKeyDictionary[threading.Thread, str] = WeakKeyDictionary()
_initialized_sessions: set[str] = set()
_sessions_lock = threading.Lock()


def current_session_name() -> str:
    thread = threading.current_thread()
    with _sessions_lock:
        session = _sessions.get(thread)
        if session is None:
            session = f"tomo-{threading.get_ident()}"
            _sessions[thread] = session
        return session


def reset_browser_session() -> None:
    session: str | None = None
    with _sessions_lock:
        thread = threading.current_thread()
        session = _sessions.get(thread)
        if session is not None:
            _initialized_sessions.discard(session)
    if session is not None:
        run_agent_browser("close", check=False)


def reset_all_browser_sessions() -> None:
    with _sessions_lock:
        _sessions.clear()
        _initialized_sessions.clear()
    run_agent_browser("close", "--all", check=False)


atexit.register(reset_all_browser_sessions)


def resolve_agent_browser_command() -> list[str]:
    override = os.environ.get("AGENT_BROWSER_BIN", "").strip()
    if override:
        return shlex.split(override, posix=platform.system() != "Windows")

    local_names = ("agent-browser.cmd", "agent-browser") if platform.system() == "Windows" else ("agent-browser",)
    for name in local_names:
        candidate = REPO_ROOT / "node_modules" / ".bin" / name
        if candidate.exists():
            return [candidate.as_posix()]

    if found := shutil.which("agent-browser"):
        return [found]

    npx = shutil.which("npx")
    if npx:
        return [npx, "agent-browser"]

    return ["agent-browser"]


def run_agent_browser(*args: str, timeout_s: float | None = None, json_output: bool = False, check: bool = True) -> AgentBrowserResult:
    command = [*resolve_agent_browser_command(), "--session", current_session_name()]
    if json_output:
        command.append("--json")
    command.extend(args)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(INSTALL_HINT) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"agent-browser timed out after {timeout_s}s") from exc

    result = AgentBrowserResult(
        returncode=completed.returncode,
        stdout=(completed.stdout or "").strip(),
        stderr=(completed.stderr or "").strip(),
    )
    if check and result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit code {result.returncode}"
        raise RuntimeError(detail)
    return result


def ensure_browser_ready(timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
    session = current_session_name()
    with _sessions_lock:
        if session in _initialized_sessions:
            return
        _initialized_sessions.add(session)

    width, height = DEFAULT_VIEWPORT
    run_agent_browser("set", "viewport", str(width), str(height), timeout_s=max(5, timeout_ms / 1000))


def navigate_to(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
    ensure_browser_ready(timeout_ms)
    run_agent_browser("open", url, timeout_s=max(10, timeout_ms / 1000))
    run_agent_browser("wait", "--load", "domcontentloaded", timeout_s=max(10, timeout_ms / 1000))


def maybe_navigate_for_url(url: str | None, action: str, timeout_ms: int) -> str | None:
    if not url:
        return None
    navigate_to(url, timeout_ms)
    settle = run_agent_browser("wait", "1000", timeout_s=max(5, timeout_ms / 1000), check=False)
    if settle.returncode != 0:
        return format_error(action, settle)
    return None


def current_page_url(timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    result = run_agent_browser("get", "url", timeout_s=max(5, timeout_ms / 1000))
    return result.stdout.strip()


def page_status(timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    url = current_page_url(timeout_ms)
    title = run_agent_browser("get", "title", timeout_s=max(5, timeout_ms / 1000)).stdout.strip()
    return f"URL: {url}\nTitle: {title}"


def format_error(action: str, result: AgentBrowserResult) -> str:
    detail = result.stderr or result.stdout or f"exit code {result.returncode}"
    if "ENOENT" in detail or "not found" in detail.lower():
        detail = f"{detail}\n{INSTALL_HINT}"
    return f"Error: browser {action} failed: {detail}"


def screenshot_target(path: str | None) -> Path:
    target = Path(path or DEFAULT_SCREENSHOT_PATH).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def truncate(text: str, limit: int = 20_000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n... output truncated ..."


@tool("browser")
def browser(
    action: BrowserAction,
    url: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    key: str | None = None,
    script: str | None = None,
    path: str | None = None,
    x: int | None = None,
    y: int | None = None,
    scroll_y: int = 700,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    full_page: bool = True,
    commands: list[str] | None = None,
) -> str:
    """Use agent-browser (headless Chromium) for rendered web UI work.

Actions: navigate, snapshot, click, fill, type, press, scroll, screenshot, text,
html, evaluate, wait, title, url, reload, back, forward, batch, close.
Prefer snapshot to discover @eN refs, then click/fill those refs. Re-snapshot
after navigation or DOM changes. Use batch for multi-step flows in one call.
"""
    timeout_s = max(5, timeout_ms / 1000)
    try:
        if action == "close":
            reset_all_browser_sessions()
            return "Browser closed."

        if action == "batch":
            if not commands:
                return "Error: browser batch requires commands."
            ensure_browser_ready(timeout_ms)
            args = ["batch", "--bail", *commands]
            result = run_agent_browser(*args, timeout_s=max(timeout_s, 30), check=False)
            if result.returncode != 0:
                return format_error("batch", result)
            output = result.stdout or "Batch completed."
            return truncate(output)

        if action == "navigate":
            if not url:
                return "Error: browser navigate requires url."
            navigate_to(url, timeout_ms)
            return page_status(timeout_ms)

        prefetch_error = maybe_navigate_for_url(url, action, timeout_ms)
        if prefetch_error:
            return prefetch_error

        ensure_browser_ready(timeout_ms)

        if action == "snapshot":
            args = ["snapshot", "-i", "-c"]
            if selector:
                args.extend(["-s", selector])
            result = run_agent_browser(*args, timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("snapshot", result)
            return truncate(result.stdout)

        if action == "click":
            if selector:
                result = run_agent_browser("click", selector, timeout_s=timeout_s, check=False)
            elif x is not None and y is not None:
                run_agent_browser("mouse", "move", str(x), str(y), timeout_s=timeout_s)
                run_agent_browser("mouse", "down", timeout_s=timeout_s)
                result = run_agent_browser("mouse", "up", timeout_s=timeout_s, check=False)
            else:
                return "Error: browser click requires selector or x/y coordinates."
            if result.returncode != 0:
                return format_error("click", result)
            return page_status(timeout_ms)

        if action == "fill":
            if not selector or text is None:
                return "Error: browser fill requires selector and text."
            result = run_agent_browser("fill", selector, text, timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("fill", result)
            return page_status(timeout_ms)

        if action == "type":
            if not selector or text is None:
                return "Error: browser type requires selector and text."
            result = run_agent_browser("type", selector, text, timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("type", result)
            return page_status(timeout_ms)

        if action == "press":
            if not key:
                return "Error: browser press requires key."
            if selector:
                run_agent_browser("focus", selector, timeout_s=timeout_s)
            result = run_agent_browser("press", key, timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("press", result)
            return page_status(timeout_ms)

        if action == "scroll":
            result = run_agent_browser("scroll", "down", str(scroll_y), timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("scroll", result)
            return page_status(timeout_ms)

        if action == "screenshot":
            if current_page_url(timeout_ms) == "about:blank":
                return (
                    "Error: browser screenshot requires a navigated page. "
                    "Call browser navigate first or pass url with the screenshot action."
                )
            target = screenshot_target(path)
            args = ["screenshot"]
            if full_page:
                args.append("--full")
            args.append(target.as_posix())
            result = run_agent_browser(*args, timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("screenshot", result)
            return f"Screenshot saved: {target}\n{page_status(timeout_ms)}"

        if action == "text":
            target = selector or "body"
            result = run_agent_browser("get", "text", target, timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("text", result)
            return truncate(result.stdout)

        if action == "html":
            if selector:
                result = run_agent_browser("get", "html", selector, timeout_s=timeout_s, check=False)
            else:
                result = run_agent_browser("eval", "document.documentElement.outerHTML", timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("html", result)
            return truncate(result.stdout)

        if action == "evaluate":
            if not script:
                return "Error: browser evaluate requires script."
            result = run_agent_browser("eval", script, timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("evaluate", result)
            return truncate(result.stdout)

        if action == "wait":
            if selector:
                result = run_agent_browser("wait", selector, timeout_s=timeout_s, check=False)
            else:
                result = run_agent_browser("wait", str(max(1, timeout_ms)), timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("wait", result)
            return page_status(timeout_ms)

        if action == "title":
            result = run_agent_browser("get", "title", timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("title", result)
            return result.stdout

        if action == "url":
            return current_page_url(timeout_ms)

        if action == "reload":
            result = run_agent_browser("reload", timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("reload", result)
            run_agent_browser("wait", "--load", "domcontentloaded", timeout_s=timeout_s, check=False)
            return page_status(timeout_ms)

        if action == "back":
            result = run_agent_browser("back", timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("back", result)
            run_agent_browser("wait", "--load", "domcontentloaded", timeout_s=timeout_s, check=False)
            return page_status(timeout_ms)

        if action == "forward":
            result = run_agent_browser("forward", timeout_s=timeout_s, check=False)
            if result.returncode != 0:
                return format_error("forward", result)
            run_agent_browser("wait", "--load", "domcontentloaded", timeout_s=timeout_s, check=False)
            return page_status(timeout_ms)

        return f"Error: unknown browser action {action}."
    except RuntimeError as exc:
        return f"Error: browser {action} failed: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error: browser {action} failed: {exc}"