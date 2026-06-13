from __future__ import annotations

import re
from pathlib import Path

MEMORY_LINE_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")


def parse_markdown_memory(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    parsed: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = MEMORY_LINE_RE.match(line.strip())
        if not match:
            continue
        timestamp, text = match.groups()
        if text.strip():
            parsed.append((timestamp, text.strip()))
    return parsed
