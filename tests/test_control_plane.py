from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tomo.config import settings
from tomo.control_plane.memory_adapter import MemoryAdapter
from tomo.control_plane.models import MemoryImportFile
from tomo.control_plane.plane import AgentControlPlane
from tomo.scheduler import SCHEDULED_TASKS_FILE, ScheduledTask
from tomo.session_store import create_session, save_session


@pytest.fixture
def plane(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".tomo")
    return AgentControlPlane(memory=MemoryAdapter(tmp_path / "MEMORY.md"))


def test_health_returns_runtime_state(plane, tmp_path):
    payload = plane.health()

    assert payload.ok is True
    assert payload.project_root == str(tmp_path)
    assert payload.authenticated is False


def test_memories_list_append_and_import(plane, tmp_path):
    memory_file = tmp_path / "MEMORY.md"
    old = (datetime.now(UTC) - timedelta(days=10)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    memory_file.write_text(f"[{old}] old memory\n", encoding="utf-8")

    assert plane.list_memories()[0].title == "old memory"
    created = plane.append_memory("Use a dedicated tomo browser first.")
    assert created.freshness == "new"

    result = plane.import_memories(
        [
            MemoryImportFile(filename="notes.md", text="First imported memory.\n\nSecond imported memory."),
        ]
    )
    assert result.imported == 2
    assert len(plane.list_memories()) == 4


def test_append_memory_rejects_empty_text(plane):
    with pytest.raises(ValueError, match="memory text cannot be empty"):
        plane.append_memory("  ")


def test_import_memories_rejects_unsupported_file_type(plane):
    with pytest.raises(ValueError, match="Unsupported file type: .pdf"):
        plane.import_memories([MemoryImportFile(filename="notes.pdf", text="not parsed")])


def test_connections_include_user_facing_surfaces_not_internal_tools_or_skills(plane, tmp_path):
    tools_dir = tmp_path / "mcps" / "react-grab-mcp" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "get_element_context.json").write_text("{}", encoding="utf-8")

    connections = plane.list_connections()
    categories = {item.category for item in connections}
    names = {item.name for item in connections}

    assert {"chat", "social", "custom"}.issubset(categories)
    assert "browser" not in names
    assert "skill-installer" not in names
    assert "desktop" in names
    assert "telegram" in names
    assert next(item for item in connections if item.name == "react-grab-mcp").metadata == {"toolCount": 1}


def test_scheduled_tasks_list_is_read_only(plane, tmp_path, monkeypatch):
    import tomo.scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "_scheduler", None)
    tasks_path = tmp_path / SCHEDULED_TASKS_FILE
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    task = ScheduledTask(
        id="task-123",
        kind="reminder",
        payload={"text": "check memory"},
        scheduled_at="2026-06-14T08:30:00+00:00",
        status="pending",
    )
    tasks_path.write_text(json.dumps([task.__dict__]), encoding="utf-8")

    listed = plane.list_scheduled_tasks()
    assert listed[0].label == "Reminder"


def test_sessions_list_saved_sessions(plane):
    session = create_session("Project A")
    session.messages.append({"role": "user", "content": "hello"})
    save_session(session)

    listed = plane.list_sessions()

    assert listed[0].name == "Project A"
    assert listed[0].message_count == 1


def test_overview_counts_control_data(plane, tmp_path):
    recent = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    (tmp_path / "MEMORY.md").write_text(f"[{recent}] recent memory\n", encoding="utf-8")

    overview = plane.overview()

    assert overview.memory_count == 1
    assert overview.memories_updated_this_week == 1
    assert overview.session_count == len(plane.list_sessions())
    assert overview.connection_count == len(plane.list_connections())
