from __future__ import annotations

from types import SimpleNamespace

import httpx

from butler import tools


DUCK_HTML = """
<html><body>
  <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Alpha Result</a>
  <a class="result__snippet">Alpha snippet about search ranking.</a>
  <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.org%2Fb">Beta Result</a>
  <a class="result__snippet">Beta snippet about fetching pages.</a>
</body></html>
"""


def test_duckduckgo_html_parser_extracts_result_links(monkeypatch):
    monkeypatch.setattr(tools, "safe_url_error", lambda url: None)
    monkeypatch.setattr(tools.httpx, "get", lambda *args, **kwargs: SimpleNamespace(text=DUCK_HTML, raise_for_status=lambda: None))

    results = tools.search_duckduckgo_html("search ranking", k=2)

    assert [result.title for result in results] == ["Alpha Result", "Beta Result"]
    assert [result.url for result in results] == ["https://example.com/a", "https://example.org/b"]
    assert results[0].snippet == "Alpha snippet about search ranking."


def test_readable_html_parser_removes_scripts_and_extracts_title():
    parser = tools.ReadableTextHTMLParser()
    parser.feed("<html><title>Doc</title><script>bad()</script><h1>Heading</h1><p>Useful text</p></html>")

    assert parser.title == "Doc"
    assert "Useful text" in parser.text()
    assert "bad()" not in parser.text()


def test_safe_url_error_blocks_private_hosts(monkeypatch):
    monkeypatch.setattr(
        tools.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 80))],
    )

    error = tools.safe_url_error("http://localhost/private")

    assert error is not None
    assert "blocked address" in error


class FakeStreamResponse:
    def __init__(self, url: str, text: str, content_type: str = "text/html") -> None:
        self.url = httpx.URL(url)
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"
        self._body = text.encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        yield self._body


class FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def stream(self, method: str, url: str):
        return FakeStreamResponse(url, "<title>Fetch Title</title><p>alpha beta useful content</p>")


def test_web_fetch_returns_cleaned_page_text(monkeypatch):
    monkeypatch.setattr(tools, "safe_url_error", lambda url: None)
    monkeypatch.setattr(tools.httpx, "Client", FakeClient)

    output = tools.web_fetch.invoke({"url": "https://example.com/page"})

    assert "URL: https://example.com/page" in output
    assert "Title: Fetch Title" in output
    assert "alpha beta useful content" in output


def test_web_search_fetches_and_ranks_result_content(monkeypatch):
    monkeypatch.setattr(
        tools,
        "search_duckduckgo_html",
        lambda query, k: [
            tools.WebSearchResult(title="One", url="https://example.com/one", snippet="one"),
            tools.WebSearchResult(title="Two", url="https://example.com/two", snippet="two"),
        ],
    )
    monkeypatch.setattr(
        tools,
        "fetch_url_text",
        lambda url: tools.WebFetchResult(url=url, title="", text=f"irrelevant\nranked target content from {url}"),
    )

    output = tools.web_search.invoke({"query": "ranked target", "k": 2})

    assert "Source: One" in output
    assert "Source: Two" in output
    assert "ranked target content" in output
