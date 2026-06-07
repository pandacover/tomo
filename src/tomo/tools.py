from __future__ import annotations

import html
from html.parser import HTMLParser
import ipaddress
import re
import shlex
import socket
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from langchain_core.tools import tool
from rank_bm25 import BM25Okapi

from .browser_tools import browser


MAX_BASH_OUTPUT = 20_000
BASH_TIMEOUT_SECONDS = 60
WEB_TIMEOUT_SECONDS = 10
MAX_WEB_BYTES = 500_000
MAX_WEB_FETCH_OUTPUT = 12_000
WEB_SEARCH_CHUNK_SIZE = 1_500
WEB_SEARCH_MAX_CHUNKS = 8
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
WEB_USER_AGENT = "Tomo/0.1 (+https://example.invalid/tomo)"


class ApprovalRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovalRequest:
    operation: str
    target: str
    reason: str


ApprovalHandler = Callable[[ApprovalRequest], bool]
_approval_handler: ApprovalHandler | None = None


def set_approval_handler(handler: ApprovalHandler | None) -> None:
    global _approval_handler
    _approval_handler = handler


def get_tools():
    return [files_search, terminal, browser, web_search, web_fetch, append_memory, read_memory, schedule_task]


@tool("terminal")
def terminal(command: str, cwd: str = ".") -> str:
    """Run a fresh non-interactive bash command.

cwd defaults to "." which means the workspace root/current launch directory.
Shell state does not persist between calls. Use cwd for subdirectories.
Do not prefix commands with cd <workspace> &&; terminal already starts there.
Use for tests, project CLIs, git/status checks, package commands, file metadata, and exact command output.
"""
    blocked = _blocked_bash_reason(command)
    if blocked:
        return f"Error: blocked terminal command: {blocked}"

    target_cwd = _resolve_path(cwd)
    reason = _approval_reason(target_cwd)
    if reason:
        return f"Error: cwd {cwd} {reason}"
    if not target_cwd.exists():
        return f"Error: cwd {cwd} does not exist."
    if not target_cwd.is_dir():
        return f"Error: cwd {cwd} is not a directory."

    try:
        result = subprocess.run(
            command,
            cwd=target_cwd,
            shell=True,
            executable="/bin/bash",
            text=True,
            capture_output=True,
            timeout=BASH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {BASH_TIMEOUT_SECONDS}s."

    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        output += ("\n" if output else "") + result.stderr
    output = output.strip()
    if len(output) > MAX_BASH_OUTPUT:
        output = output[:MAX_BASH_OUTPUT] + "\n... output truncated ..."

    prefix = f"CWD: {target_cwd}\nExit code: {result.returncode}"
    return f"{prefix}\n{output}" if output else prefix


@tool("schedule_task")
def schedule_task(command: str, delay: str = "5 minutes") -> str:
    """Schedule a non-interactive bash command to run later using the local 'at' scheduler. delay examples: '5 minutes', '1 hour', 'now + 30 min'."""
    try:
        result = subprocess.run(
            ["at", delay],
            input=command + "\n",
            cwd=_workspace(),
            text=True,
            capture_output=True,
            timeout=30,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return f"Scheduled (exit {result.returncode}): {out.strip()}" if out.strip() else f"Scheduled (exit {result.returncode})"
    except FileNotFoundError:
        return "Error: 'at' command not found on this system."
    except Exception as exc:  # noqa: BLE001
        return f"Error scheduling task: {exc}"


@tool("files_search")
def files_search(query: str, path: str = ".", k: int = 20) -> str:
    """Search local workspace text with ripgrep and BM25-rank matching lines. Use to find code, symbols, config, tests, docs, and references before reading files."""
    if not query or not query.strip():
        return "Error: search query cannot be empty."
    query = normalize_search_query(query)
    target = _resolve_path(path)
    reason = _approval_reason(target)
    if reason:
        return f"Error: search path {path} {reason}"
    command = f"rg --line-number --no-heading --smart-case {shlex.quote(query)} {shlex.quote(str(target))}"
    try:
        result = subprocess.run(
            command,
            cwd=_workspace(),
            shell=True,
            executable="/bin/bash",
            text=True,
            capture_output=True,
            timeout=BASH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"Error: search timed out after {BASH_TIMEOUT_SECONDS}s."
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode == 1:
        return "No matches found."
    if result.returncode != 0:
        detail = output[:MAX_BASH_OUTPUT] if output else "ripgrep failed without output."
        return f"Error: rg exited with code {result.returncode}\n{detail}"
    lines = result.stdout.splitlines()
    ranked = rank_texts(query, lines, k=k)
    return "\n".join(ranked) if ranked else "No matches found."


def normalize_search_query(query: str) -> str:
    return " ".join(query.split())


@tool("web_search")
def web_search(query: str, k: int = 5, fetch_results: bool = True) -> str:
    """Search the public web for current or external information. Use for news, versions, docs, prices, schedules, laws, and facts that may have changed."""
    if not query or not query.strip():
        return "Error: web search query cannot be empty."
    k = max(1, min(k, 10))
    try:
        results = search_duckduckgo_html(query, k=k)
    except Exception as exc:  # noqa: BLE001
        return f"Error: web search failed: {exc}"
    if not results:
        return "No web results found."
    if not fetch_results:
        return format_search_results(results)

    chunks: list[str] = []
    errors: list[str] = []
    for result in results[:k]:
        fetch = fetch_url_text(result.url)
        if fetch.error:
            errors.append(f"{result.url}: {fetch.error}")
            continue
        ranked = rank_chunks(query, chunk_text(fetch.text, WEB_SEARCH_CHUNK_SIZE), k=2)
        for chunk in ranked:
            chunks.append(f"Source: {result.title}\nURL: {result.url}\nExcerpt: {chunk}")

    ranked_chunks = rank_texts(query, chunks, k=WEB_SEARCH_MAX_CHUNKS)
    if ranked_chunks:
        output = "\n\n".join(ranked_chunks)
    else:
        output = format_search_results(results)
    if errors:
        output += "\n\nFetch notes:\n" + "\n".join(errors[:5])
    return output


@tool("web_fetch")
def web_fetch(url: str, query: str | None = None) -> str:
    """Fetch and clean text from a known public HTTP(S) URL. Use when a specific page, article, documentation URL, or source needs to be read."""
    fetch = fetch_url_text(url)
    if fetch.error:
        return f"Error: {fetch.error}"
    if query and query.strip():
        chunks = rank_chunks(query, chunk_text(fetch.text, WEB_SEARCH_CHUNK_SIZE), k=WEB_SEARCH_MAX_CHUNKS)
        body = "\n\n".join(chunks) if chunks else fetch.text[:MAX_WEB_FETCH_OUTPUT]
    else:
        body = fetch.text[:MAX_WEB_FETCH_OUTPUT]
    if len(fetch.text) > len(body):
        body += "\n... output truncated ..."
    return f"URL: {fetch.url}\nTitle: {fetch.title or '(untitled)'}\n\n{body}"


def _workspace() -> Path:
    return Path.cwd().resolve()


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class WebFetchResult:
    url: str
    title: str
    text: str
    error: str | None = None


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[WebSearchResult] = []
        self._current_link: dict[str, str] | None = None
        self._current_snippet: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name: value or "" for name, value in attrs}
        class_name = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in class_name:
            self._current_link = {"title": "", "href": attrs_dict.get("href", "")}
            return
        if tag == "a" and attrs_dict.get("rel") == "nofollow" and attrs_dict.get("href") and self._current_link is None:
            self._current_link = {"title": "", "href": attrs_dict.get("href", "")}
            return
        if "result__snippet" in class_name:
            self._current_snippet = []

    def handle_data(self, data: str) -> None:
        if self._current_link is not None:
            self._current_link["title"] += data
        if self._current_snippet is not None:
            self._current_snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_link is not None:
            title = normalize_space(self._current_link["title"])
            url = normalize_duckduckgo_url(self._current_link["href"])
            if title and url:
                self.results.append(WebSearchResult(title=title, url=url, snippet=""))
            self._current_link = None
        if tag in {"a", "div"} and self._current_snippet is not None and self.results:
            snippet = normalize_space(" ".join(self._current_snippet))
            if snippet:
                previous = self.results[-1]
                self.results[-1] = WebSearchResult(title=previous.title, url=previous.url, snippet=snippet)
            self._current_snippet = None


class ReadableTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        self.parts.append(data)

    def text(self) -> str:
        return normalize_space("\n".join(self.parts))


def search_duckduckgo_html(query: str, k: int = 5) -> list[WebSearchResult]:
    response = httpx.get(
        DUCKDUCKGO_HTML_URL,
        params={"q": query},
        headers={"User-Agent": WEB_USER_AGENT},
        timeout=WEB_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    response.raise_for_status()
    parser = DuckDuckGoHTMLParser()
    parser.feed(response.text)
    unique: list[WebSearchResult] = []
    seen: set[str] = set()
    for result in parser.results:
        if result.url in seen:
            continue
        if safe_url_error(result.url):
            continue
        unique.append(result)
        seen.add(result.url)
        if len(unique) >= k:
            break
    return unique


def fetch_url_text(url: str) -> WebFetchResult:
    error = safe_url_error(url)
    if error:
        return WebFetchResult(url=url, title="", text="", error=error)
    try:
        with httpx.Client(
            timeout=WEB_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": WEB_USER_AGENT},
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                final_url = str(response.url)
                error = safe_url_error(final_url)
                if error:
                    return WebFetchResult(url=final_url, title="", text="", error=f"redirect target {error}")
                content_type = response.headers.get("content-type", "")
                if not is_textual_content_type(content_type):
                    return WebFetchResult(url=final_url, title="", text="", error=f"non-text content type: {content_type}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_WEB_BYTES:
                        remaining = max(0, MAX_WEB_BYTES - (total - len(chunk)))
                        chunks.append(chunk[:remaining])
                        break
                    chunks.append(chunk)
    except Exception as exc:  # noqa: BLE001
        return WebFetchResult(url=url, title="", text="", error=str(exc))

    raw = b"".join(chunks)
    encoding = response.encoding or "utf-8"
    text = raw.decode(encoding, errors="replace")
    title = ""
    if "html" in content_type.lower() or looks_like_html(text):
        parser = ReadableTextHTMLParser()
        parser.feed(text)
        title = normalize_space(parser.title)
        text = parser.text()
    else:
        text = normalize_space(text)
    return WebFetchResult(url=final_url, title=title, text=text)


def safe_url_error(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "URL must use http or https."
    if not parsed.hostname:
        return "URL is missing a host."
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return f"host could not be resolved: {exc}"
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if is_blocked_ip(ip):
            return f"host resolves to blocked address {ip}."
    return None


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        [
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        ]
    )


def normalize_duckduckgo_url(url: str) -> str:
    parsed = urlparse(html.unescape(url))
    if (not parsed.netloc or parsed.netloc.endswith("duckduckgo.com")) and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(uddg)
    return html.unescape(url)


def is_textual_content_type(content_type: str) -> bool:
    lowered = content_type.lower()
    return not lowered or lowered.startswith("text/") or "html" in lowered or "json" in lowered or "xml" in lowered


def looks_like_html(text: str) -> bool:
    prefix = text[:500].lower()
    return "<html" in prefix or "<!doctype html" in prefix


def normalize_space(text: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in html.unescape(text).splitlines()]
    return "\n".join(line for line in lines if line)


def chunk_text(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(text[start:end].strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def rank_chunks(query: str, chunks: list[str], k: int) -> list[str]:
    return rank_texts(query, chunks, k=k)


def format_search_results(results: list[WebSearchResult]) -> str:
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result.title}\nURL: {result.url}\nSnippet: {result.snippet or '(none)'}")
    return "\n\n".join(lines)


def _guard_path(path: str, operation: str) -> Path:
    target = _resolve_path(path)
    reason = _approval_reason(target)
    if reason:
        raise ApprovalRequired(f"Approval required: {operation} {path} {reason}")
    return target


def _approval_message(operation: str, path: str) -> str | None:
    target = _resolve_path(path)
    reason = _approval_reason(target)
    if reason and not _request_approval(operation, path, reason):
        return f"Approval denied: {operation} {path} {reason}"
    return None


def _request_approval(operation: str, target: str, reason: str) -> bool:
    if _approval_handler is None:
        return False
    try:
        return bool(_approval_handler(ApprovalRequest(operation=operation, target=target, reason=reason)))
    except Exception:
        return False


def _resolve_path(path: str) -> Path:
    raw = Path(path).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    return (_workspace() / raw).resolve()


def _approval_reason(path: Path) -> str | None:
    workspace = _workspace()
    if not _is_relative_to(path, workspace):
        return "is outside the current working directory."
    relative = path.relative_to(workspace)
    if any(part.startswith(".") for part in relative.parts):
        return "touches a dotfile or dot-directory."
    return None


def _bash_approval_reason(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return f"could not parse command safely ({exc})."

    for token in tokens:
        if not _looks_like_path(token):
            continue
        path = _resolve_path(token)
        reason = _approval_reason(path)
        if reason:
            return f"touches `{token}` which {reason}"
    return None


def _looks_like_path(token: str) -> bool:
    if token in {".", ".."}:
        return True
    if token.startswith(("/", "./", "../", "~", ".")):
        return True
    return "/" in token


def _blocked_bash_reason(command: str) -> str | None:
    normalized = " ".join(command.lower().split())
    blocked_patterns = ["rm -rf /", "git reset --hard", "mkfs", "shutdown", "reboot"]
    for pattern in blocked_patterns:
        if pattern in normalized:
            return pattern
    return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def rank_texts(query: str, texts: list[str], k: int = 8) -> list[str]:
    """Return BM25-ranked texts, newest-ish strings first on score ties."""
    if not texts:
        return []
    tokenized_corpus = [text.lower().split() for text in texts]
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(query.lower().split())
    scored = sorted(zip(scores, texts), key=lambda x: (x[0], x[1][:20]), reverse=True)
    return [text for _, text in scored[:k]]


MEMORY_FILE = Path("MEMORY.md")


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@tool("append_memory")
def append_memory(text: str) -> str:
    """Append a timestamped durable memory to MEMORY.md. Use for user preferences, project facts, decisions, recurring context, and useful workarounds."""
    if not text or not text.strip():
        return "Error: memory text cannot be empty"
    line = f"[{_now_iso()}] {text.strip()}\n"
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    return f"Appended memory: {text.strip()[:80]}"


@tool("read_memory")
def read_memory(query: str, k: int = 8) -> str:
    """Search MEMORY.md with BM25 for relevant prior context. Use before answering when user preferences, past decisions, or project facts may matter."""
    if not MEMORY_FILE.exists():
        return "No memory file yet."

    lines = MEMORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return "Memory file is empty."

    top = rank_texts(query, lines, k=k)
    return "\n".join(top) if top else "No relevant memories found."
