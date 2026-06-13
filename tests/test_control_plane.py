from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tomo.config import settings
from tomo.control_approval_store import ControlApprovalStore
from tomo.control_plane.approval_adapter import ApprovalAdapter
from tomo.control_plane.memory_adapter import MemoryAdapter
from tomo.control_plane.models import MemoryImportFile
from tomo.control_plane.plane import AgentControlPlane
from tomo.scheduler import SCHEDULED_TASKS_FILE, ScheduledTask
from tomo.tools import ApprovalRequest


@pytest.fixture
def plane(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".tomo")
    store = ControlApprovalStore(storage_path=tmp_path / ".tomo" / "control_approvals.json")
    return AgentControlPlane(
        memory=MemoryAdapter(tmp_path / "MEMORY.md"),
        approvals=ApprovalAdapter(store),
    )


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


def test_integrations_include_tools_skills_and_gateways(plane):
    integrations = plane.list_integrations()
    kinds = {item.kind for item in integrations}
    names = {item.name for item in integrations}

    assert "tool" in kinds
    assert "skill" in kinds
    assert "gateway" in kinds
    assert "browser" in names
    assert "desktop" in names
    assert "telegram" in names


def test_scheduled_tasks_list_and_cancel(plane, tmp_path, monkeypatch):
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

    cancelled = plane.cancel_scheduled_task("task-123")
    assert cancelled.status == "cancelled"


def test_approvals_list_and_resolve(plane):
    approval_id = plane.approvals.store.create(
        "desktop:local",
        ApprovalRequest(operation="terminal", target="uv run pytest", reason="Run tests"),
    )

    pending = plane.list_pending_approvals()
    assert pending[0].id == approval_id

    resolution = plane.resolve_approval(approval_id, True)
    assert resolution.ok is True
    assert resolution.approved is True
    assert plane.list_pending_approvals() == []


def test_overview_counts_control_data(plane, tmp_path):
    recent = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    (tmp_path / "MEMORY.md").write_text(f"[{recent}] recent memory\n", encoding="utf-8")
    plane.approvals.store.create(
        "desktop:local",
        ApprovalRequest(operation="terminal", target="uv run pytest", reason="Run tests"),
    )

    overview = plane.overview()

    assert overview.memory_count == 1
    assert overview.memories_updated_this_week == 1
    assert overview.pending_approval_count == 1
    assert overview.integration_count == overview.tool_count + overview.skill_count + overview.gateway_count
