from __future__ import annotations

import sqlite3

from tomo.memory_store import SQLiteMemoryRepository


def test_sqlite_repository_appends_lists_imports_and_disables(tmp_path):
    repo = SQLiteMemoryRepository(tmp_path / ".tomo" / "tomo.db", migration_source=tmp_path / "MEMORY.md")

    created = repo.append("Remember the dedicated browser.", source="manual")
    imported = repo.import_texts(["First imported memory.", "Second imported memory."], source_ref="notes.md")

    records = repo.list()
    assert records[0].text == "Second imported memory."
    assert created in records
    assert len(imported) == 2

    assert repo.mark_disabled(created.id) is True
    disabled = next(record for record in repo.list() if record.id == created.id)
    assert disabled.disabled_at is not None


def test_sqlite_repository_migrates_memory_md_once_without_rewriting(tmp_path):
    memory_file = tmp_path / "MEMORY.md"
    original = "[2026-06-14T08:30:00Z] migrated memory\ninvalid line\n"
    memory_file.write_text(original, encoding="utf-8")
    db_path = tmp_path / ".tomo" / "tomo.db"

    first = SQLiteMemoryRepository(db_path, migration_source=memory_file)
    second = SQLiteMemoryRepository(db_path, migration_source=memory_file)

    assert [record.text for record in second.list()] == ["migrated memory"]
    assert memory_file.read_text(encoding="utf-8") == original

    with sqlite3.connect(db_path) as connection:
        migration_count = connection.execute("SELECT COUNT(*) FROM memory_migrations").fetchone()[0]
        memory_count = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert migration_count == 1
    assert memory_count == 1
