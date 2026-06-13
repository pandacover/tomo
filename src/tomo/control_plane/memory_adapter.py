from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tomo.tools import MEMORY_FILE, now_iso

from .models import ControlMemoryEntry, MemoryImportFile

MEMORY_LINE_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
SUPPORTED_IMPORT_SUFFIXES = {".txt", ".md", ".markdown", ".json"}


def memory_id(timestamp: str, text: str) -> str:
    digest = hashlib.sha256(f"{timestamp}:{text}".encode("utf-8")).hexdigest()
    return f"mem-{digest[:12]}"


def parse_timestamp(raw: str) -> datetime | None:
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def freshness(timestamp: datetime, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    age = now - timestamp
    if age <= timedelta(days=1):
        return "new"
    if age <= timedelta(days=7):
        return "updated"
    return "stale"


def updated_label(timestamp: datetime) -> str:
    return timestamp.astimezone().strftime("updated %H:%M")


def title_from_text(text: str) -> str:
    return " ".join(text.strip().split()[:6]).lower()


def memory_entry(timestamp_raw: str, text: str) -> ControlMemoryEntry | None:
    timestamp = parse_timestamp(timestamp_raw)
    if timestamp is None:
        return None
    return ControlMemoryEntry(
        id=memory_id(timestamp_raw, text),
        timestamp=timestamp.isoformat().replace("+00:00", "Z"),
        text=text,
        title=title_from_text(text),
        status="active",
        freshness=freshness(timestamp),
        updated_label=updated_label(timestamp),
    )


class MemoryAdapter:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or MEMORY_FILE

    def list(self) -> list[ControlMemoryEntry]:
        if not self.path.exists():
            return []
        entries: list[ControlMemoryEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = MEMORY_LINE_RE.match(stripped)
            if not match:
                continue
            timestamp_raw, text = match.groups()
            entry = memory_entry(timestamp_raw, text)
            if entry is not None:
                entries.append(entry)
        return sorted(entries, key=lambda item: item.timestamp, reverse=True)

    def append(self, text: str) -> ControlMemoryEntry:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("memory text cannot be empty")
        timestamp = now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {cleaned}\n")
        entry = memory_entry(timestamp, cleaned)
        if entry is None:
            raise ValueError("memory timestamp could not be parsed")
        return entry

    def import_files(self, files: list[MemoryImportFile]) -> list[ControlMemoryEntry]:
        imported: list[ControlMemoryEntry] = []
        for file in files:
            if file.suffix not in SUPPORTED_IMPORT_SUFFIXES:
                raise ValueError(f"Unsupported file type: {file.suffix or '(none)'}")
            for paragraph in re.split(r"\n\s*\n", file.text.strip()):
                cleaned = " ".join(paragraph.split())
                if cleaned:
                    imported.append(self.append(cleaned))
        return imported
