from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tomo.config import settings

from .markdown_migration import parse_markdown_memory
from .models import MemoryRecord, MemorySource

VALID_SOURCES = {"manual", "import", "migration", "agent"}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def memory_id(created_at: str, text: str) -> str:
    digest = hashlib.sha256(f"{created_at}:{text}".encode("utf-8")).hexdigest()
    return f"mem-{digest[:12]}"


class SQLiteMemoryRepository:
    def __init__(self, db_path: Path | None = None, *, migration_source: Path | None = None) -> None:
        self.db_path = db_path or settings.data_dir / "tomo.db"
        self.migration_source = migration_source or Path("MEMORY.md")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self._migrate_markdown_once()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                  id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL,
                  text TEXT NOT NULL,
                  source TEXT NOT NULL,
                  source_ref TEXT,
                  disabled_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_migrations (
                  id TEXT PRIMARY KEY,
                  source_path TEXT NOT NULL,
                  source_mtime_ns INTEGER NOT NULL,
                  migrated_at TEXT NOT NULL
                )
                """
            )

    def _migrate_markdown_once(self) -> None:
        source = self.migration_source
        if not source.exists():
            return
        resolved = str(source.resolve())
        stat = source.stat()
        marker = hashlib.sha256(f"{resolved}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()
        with self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM memory_migrations WHERE id = ?", (marker,)).fetchone()
            if exists:
                return
            for created_at, text in parse_markdown_memory(source):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO memories (id, created_at, text, source, source_ref, disabled_at)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (memory_id(created_at, text), created_at, text, "migration", resolved),
                )
            connection.execute(
                """
                INSERT INTO memory_migrations (id, source_path, source_mtime_ns, migrated_at)
                VALUES (?, ?, ?, ?)
                """,
                (marker, resolved, stat.st_mtime_ns, now_iso()),
            )

    def list(self) -> list[MemoryRecord]:
        self._migrate_markdown_once()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, text, source, source_ref, disabled_at
                FROM memories
                ORDER BY created_at DESC, rowid DESC
                """
            ).fetchall()
        return [
            MemoryRecord(
                id=row["id"],
                created_at=row["created_at"],
                text=row["text"],
                source=row["source"],
                source_ref=row["source_ref"],
                disabled_at=row["disabled_at"],
            )
            for row in rows
        ]

    def append(self, text: str, *, source: str = "manual", source_ref: str | None = None) -> MemoryRecord:
        self._migrate_markdown_once()
        cleaned = " ".join(text.split())
        if not cleaned:
            raise ValueError("memory text cannot be empty")
        if source not in VALID_SOURCES:
            raise ValueError(f"Unsupported memory source: {source}")
        created_at = now_iso()
        record = MemoryRecord(
            id=memory_id(created_at, cleaned),
            created_at=created_at,
            text=cleaned,
            source=source,  # type: ignore[arg-type]
            source_ref=source_ref,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memories (id, created_at, text, source, source_ref, disabled_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (record.id, record.created_at, record.text, record.source, record.source_ref),
            )
        return record

    def import_texts(self, texts: list[str], *, source_ref: str | None = None) -> list[MemoryRecord]:
        return [self.append(text, source="import", source_ref=source_ref) for text in texts if text.strip()]

    def mark_disabled(self, memory_id: str) -> bool:
        self._migrate_markdown_once()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE memories SET disabled_at = ? WHERE id = ? AND disabled_at IS NULL",
                (now_iso(), memory_id),
            )
            return cursor.rowcount > 0
