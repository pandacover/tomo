from __future__ import annotations

import atexit
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Literal
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
    "close",
]
DEFAULT_SCREENSHOT_PATH = "browser-screenshot.png"
DEFAULT_TIMEOUT_MS = 10_000


@dataclass
class BrowserSession:
    playwright: Any = None
    browser: Any = None
    page: Any = None


_sessions: WeakKeyDictionary[threading.Thread, BrowserSession] = WeakKeyDictionary()
_sessions_lock = threading.Lock()


def current_browser_session() -> BrowserSession:
    thread = threading.current_thread()
    with _sessions_lock:
        session = _sessions.get(thread)
        if session is None:
            session = BrowserSession()
            _sessions[thread] = session
        return session


def close_browser_session(session: BrowserSession) -> None:
    if session.browser is not None:
        try:
            session.browser.close()
        except Exception:
            pass
    if session.playwright is not None:
        try:
            session.playwright.stop()
        except Exception:
            pass
    session.playwright = None
    session.browser = None
    session.page = None


def reset_browser_session() -> None:
    close_browser_session(current_browser_session())


def reset_all_browser_sessions() -> None:
    with _sessions_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for session in sessions:
        close_browser_session(session)


atexit.register(reset_all_browser_sessions)


def ensure_page():
    session = current_browser_session()
    if session.page is not None:
        return session.page
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright is not installed. Run `uv sync` to install Tomo dependencies.") from None

    try:
        session.playwright = sync_playwright().start()
        session.browser = session.playwright.chromium.launch(headless=True)
        session.page = session.browser.new_page(viewport={"width": 1440, "height": 1000})
    except Exception as exc:  # noqa: BLE001
        reset_browser_session()
        raise RuntimeError(f"could not start Chromium. Run `uv run playwright install chromium`. Details: {exc}") from exc
    return session.page


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
) -> str:
    """Use a real headless Chromium browser for web development verification.

Actions: navigate, click, fill, type, press, scroll, screenshot, text, html,
evaluate, wait, title, url, reload, back, forward, close.
Use this for web UI tasks that require rendered pages, interaction, screenshots,
layout checks, or client-side JavaScript behavior.
"""
    try:
        if action == "close":
            reset_all_browser_sessions()
            return "Browser closed."

        page = ensure_page()
        page.set_default_timeout(max(1, timeout_ms))

        if action == "navigate":
            if not url:
                return "Error: browser navigate requires url."
            page.goto(url, wait_until="domcontentloaded")
            return page_status(page)

        if url and action in {"text", "html", "evaluate", "title"}:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1_000)

        if action == "click":
            if selector:
                page.click(selector)
            elif x is not None and y is not None:
                page.mouse.click(x, y)
            else:
                return "Error: browser click requires selector or x/y coordinates."
            return page_status(page)

        if action == "fill":
            if not selector or text is None:
                return "Error: browser fill requires selector and text."
            page.fill(selector, text)
            return page_status(page)

        if action == "type":
            if not selector or text is None:
                return "Error: browser type requires selector and text."
            page.locator(selector).type(text)
            return page_status(page)

        if action == "press":
            if not key:
                return "Error: browser press requires key."
            if selector:
                page.press(selector, key)
            else:
                page.keyboard.press(key)
            return page_status(page)

        if action == "scroll":
            page.mouse.wheel(0, scroll_y)
            return page_status(page)

        if action == "screenshot":
            if url:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(1_000)
            if page.url == "about:blank":
                return "Error: browser screenshot requires a navigated page. Call browser navigate first or pass url with the screenshot action."
            target = Path(path or DEFAULT_SCREENSHOT_PATH).expanduser()
            if not target.is_absolute():
                target = Path.cwd() / target
            target.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=target.as_posix(), full_page=full_page)
            return f"Screenshot saved: {target}\nURL: {page.url}\nTitle: {page.title()}"

        if action == "text":
            target = selector or "body"
            return truncate(page.locator(target).inner_text(timeout=timeout_ms))

        if action == "html":
            return truncate(page.content())

        if action == "evaluate":
            if not script:
                return "Error: browser evaluate requires script."
            return truncate(str(page.evaluate(script)))

        if action == "wait":
            if selector:
                page.wait_for_selector(selector, timeout=timeout_ms)
            else:
                page.wait_for_timeout(timeout_ms)
            return page_status(page)

        if action == "title":
            return page.title()

        if action == "url":
            return page.url

        if action == "reload":
            page.reload(wait_until="domcontentloaded")
            return page_status(page)

        if action == "back":
            page.go_back(wait_until="domcontentloaded")
            return page_status(page)

        if action == "forward":
            page.go_forward(wait_until="domcontentloaded")
            return page_status(page)

        return f"Error: unknown browser action {action}."
    except Exception as exc:  # noqa: BLE001
        return f"Error: browser {action} failed: {exc}"


def page_status(page: Any) -> str:
    return f"URL: {page.url}\nTitle: {page.title()}"


def truncate(text: str, limit: int = 20_000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n... output truncated ..."
