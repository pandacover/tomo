from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tomo.config import settings
from tomo.control_api import create_app
from tomo.scheduler import SCHEDULED_TASKS_FILE, ScheduledTask
from tomo.session_store import create_session, save_session


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".tomo")
    monkeypatch.setattr(settings, "control_api_key", None)
    return TestClient(create_app())


def test_health_endpoint(client, tmp_path):
    response = client.get("/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["authenticated"] is False
    assert payload["projectRoot"] == str(tmp_path)


def test_memories_list_and_append(client, tmp_path):
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "[2026-06-13T10:00:00Z] Prefer the react host boundary.\n",
        encoding="utf-8",
    )

    listed = client.get("/v1/memories")
    assert listed.status_code == 200
    entries = listed.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["title"] == "prefer the react host boundary."

    created = client.post("/v1/memories", json={"text": "Use a dedicated tomo browser first."})
    assert created.status_code == 200
    assert created.json()["entry"]["freshness"] == "new"
    assert len(client.get("/v1/memories").json()["entries"]) == 2


def test_overview_counts_memories(client, tmp_path):
    memory_file = tmp_path / "MEMORY.md"
    recent = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    old = (datetime.now(UTC) - timedelta(days=10)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    memory_file.write_text(
        f"[{recent}] recent memory\n[{old}] old memory\n",
        encoding="utf-8",
    )

    response = client.get("/v1/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["memoryCount"] == 2
    assert payload["memoriesUpdatedThisWeek"] == 1


def test_connections_include_user_facing_surfaces(client):
    response = client.get("/v1/connections")
    assert response.status_code == 200
    connections = response.json()["connections"]
    categories = {item["category"] for item in connections}
    names = {item["name"] for item in connections}
    assert "chat" in categories
    assert "social" in categories
    assert "browser" not in names
    assert "desktop" in names
    assert "telegram" in names
    assert client.get("/v1/integrations").json() == response.json()


def test_sessions_list_saved_sessions(client):
    session = create_session("Project A")
    session.messages.append({"role": "user", "content": "hello"})
    save_session(session)

    response = client.get("/v1/sessions")

    assert response.status_code == 200
    assert response.json()["sessions"][0]["name"] == "Project A"
    assert response.json()["sessions"][0]["messageCount"] == 1


def test_scheduled_tasks_are_read_only(client, tmp_path, monkeypatch):
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

    listed = client.get("/v1/scheduled-tasks")
    assert listed.status_code == 200
    assert listed.json()["tasks"][0]["label"] == "Reminder"

    patched = client.patch("/v1/scheduled-tasks/task-123", json={"enabled": False})
    assert patched.status_code == 410
    assert patched.json()["detail"] == "Scheduled tasks are read-only in the dashboard."


def test_approvals_are_runtime_only(client):
    listed = client.get("/v1/approvals")
    assert listed.status_code == 410

    resolved = client.post("/v1/approvals/approval-123", json={"approved": True})
    assert resolved.status_code == 410
    assert resolved.json()["detail"] == "Approvals are handled in the active Tomo gateway, not the dashboard."


def test_api_key_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "control_api_key", "secret-key")
    client = TestClient(create_app())

    denied = client.get("/v1/memories")
    assert denied.status_code == 401

    allowed = client.get("/v1/memories", headers={"Authorization": "Bearer secret-key"})
    assert allowed.status_code == 200


def test_import_memories_from_text_file(client, tmp_path):
    files = {"files": ("notes.md", "First imported memory.\n\nSecond imported memory.", "text/markdown")}
    response = client.post("/v1/memories/import", files=files)
    assert response.status_code == 200
    payload = response.json()
    assert payload["imported"] == 2
    assert len(client.get("/v1/memories").json()["entries"]) == 2
