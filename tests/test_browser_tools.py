from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

from tomo import browser_tools
from tomo.browser_tools import browser


class FakeLocator:
    def __init__(self) -> None:
        self.typed: list[str] = []

    def type(self, text: str) -> None:
        self.typed.append(text)

    def inner_text(self, timeout: int) -> str:
        return f"body text timeout={timeout}"


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.actions: list[tuple[str, object]] = []
        self.mouse = SimpleNamespace(
            click=lambda x, y: self.actions.append(("mouse.click", (x, y))),
            wheel=lambda x, y: self.actions.append(("mouse.wheel", (x, y))),
        )
        self.keyboard = SimpleNamespace(press=lambda key: self.actions.append(("keyboard.press", key)))
        self.locator_instance = FakeLocator()

    def set_default_timeout(self, timeout: int) -> None:
        self.actions.append(("timeout", timeout))

    def goto(self, url: str, wait_until: str) -> None:
        self.url = url
        self.actions.append(("goto", (url, wait_until)))

    def title(self) -> str:
        return "Fake Title"

    def click(self, selector: str) -> None:
        self.actions.append(("click", selector))

    def fill(self, selector: str, text: str) -> None:
        self.actions.append(("fill", (selector, text)))

    def press(self, selector: str, key: str) -> None:
        self.actions.append(("press", (selector, key)))

    def locator(self, selector: str) -> FakeLocator:
        self.actions.append(("locator", selector))
        return self.locator_instance

    def screenshot(self, path: str, full_page: bool) -> None:
        self.actions.append(("screenshot", (path, full_page)))

    def content(self) -> str:
        return "<html></html>"

    def evaluate(self, script: str) -> object:
        self.actions.append(("evaluate", script))
        return {"ok": True}

    def wait_for_selector(self, selector: str, timeout: int) -> None:
        self.actions.append(("wait_for_selector", (selector, timeout)))

    def wait_for_timeout(self, timeout: int) -> None:
        self.actions.append(("wait_for_timeout", timeout))

    def reload(self, wait_until: str) -> None:
        self.actions.append(("reload", wait_until))

    def go_back(self, wait_until: str) -> None:
        self.actions.append(("back", wait_until))

    def go_forward(self, wait_until: str) -> None:
        self.actions.append(("forward", wait_until))


class ThreadBoundPage(FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.owner_thread = threading.get_ident()

    def title(self) -> str:
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError("wrong thread")
        return "Thread Title"


class FakeBrowser:
    def new_page(self, viewport: dict[str, int]) -> ThreadBoundPage:
        return ThreadBoundPage()

    def close(self) -> None:
        pass


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = SimpleNamespace(launch=lambda headless: FakeBrowser())

    def stop(self) -> None:
        pass


class FakePlaywrightFactory:
    def start(self) -> FakePlaywright:
        return FakePlaywright()


def test_browser_navigate_uses_headless_page(monkeypatch):
    page = FakePage()
    monkeypatch.setattr(browser_tools, "ensure_page", lambda: page)

    result = browser.invoke({"action": "navigate", "url": "https://example.com"})

    assert result == "URL: https://example.com\nTitle: Fake Title"
    assert ("goto", ("https://example.com", "domcontentloaded")) in page.actions


def test_browser_click_fill_scroll_and_text(monkeypatch):
    page = FakePage()
    monkeypatch.setattr(browser_tools, "ensure_page", lambda: page)

    assert "Fake Title" in browser.invoke({"action": "click", "selector": "#go"})
    assert "Fake Title" in browser.invoke({"action": "fill", "selector": "#name", "text": "Tomo"})
    assert "Fake Title" in browser.invoke({"action": "scroll", "scroll_y": 300})
    assert browser.invoke({"action": "text", "selector": "main"}) == "body text timeout=10000"

    assert ("click", "#go") in page.actions
    assert ("fill", ("#name", "Tomo")) in page.actions
    assert ("mouse.wheel", (0, 300)) in page.actions


def test_browser_screenshot_writes_requested_path(tmp_path, monkeypatch):
    page = FakePage()
    monkeypatch.setattr(browser_tools, "ensure_page", lambda: page)
    target = tmp_path / "shot.png"
    page.url = "https://example.com"

    result = browser.invoke({"action": "screenshot", "path": target.as_posix(), "full_page": False})

    assert result == f"Screenshot saved: {target}\nURL: https://example.com\nTitle: Fake Title"
    assert ("screenshot", (target.as_posix(), False)) in page.actions


def test_browser_screenshot_rejects_blank_page(monkeypatch):
    page = FakePage()
    monkeypatch.setattr(browser_tools, "ensure_page", lambda: page)

    result = browser.invoke({"action": "screenshot"})

    assert result == "Error: browser screenshot requires a navigated page. Call browser navigate first or pass url with the screenshot action."
    assert not any(action == "screenshot" for action, _ in page.actions)


def test_browser_screenshot_with_url_navigates_before_capture(tmp_path, monkeypatch):
    page = FakePage()
    monkeypatch.setattr(browser_tools, "ensure_page", lambda: page)
    target = tmp_path / "shot.png"

    result = browser.invoke({"action": "screenshot", "url": "https://example.com", "path": target.as_posix()})

    assert result == f"Screenshot saved: {target}\nURL: https://example.com\nTitle: Fake Title"
    assert ("goto", ("https://example.com", "domcontentloaded")) in page.actions
    assert ("wait_for_timeout", 1000) in page.actions
    assert ("screenshot", (target.as_posix(), True)) in page.actions


def test_browser_text_with_url_navigates_before_reading(monkeypatch):
    page = FakePage()
    monkeypatch.setattr(browser_tools, "ensure_page", lambda: page)

    result = browser.invoke({"action": "text", "url": "https://example.com", "selector": "main"})

    assert result == "body text timeout=10000"
    assert ("goto", ("https://example.com", "domcontentloaded")) in page.actions
    assert ("wait_for_timeout", 1000) in page.actions
    assert ("locator", "main") in page.actions


def test_browser_sessions_are_thread_local(monkeypatch):
    import playwright.sync_api

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: FakePlaywrightFactory())
    browser_tools.reset_browser_session()

    assert browser_tools.browser.invoke({"action": "title"}) == "Thread Title"

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(lambda: browser_tools.browser.invoke({"action": "title"})).result()

    assert result == "Thread Title"


def test_browser_sessions_are_keyed_by_thread_object_not_reused_ident(monkeypatch):
    class FakeThread:
        __slots__ = ("__weakref__",)

        pass

    first_thread = FakeThread()
    second_thread = FakeThread()
    monkeypatch.setattr(threading, "current_thread", lambda: first_thread)
    first_session = browser_tools.current_browser_session()

    monkeypatch.setattr(threading, "current_thread", lambda: second_thread)
    second_session = browser_tools.current_browser_session()

    assert second_session is not first_session
