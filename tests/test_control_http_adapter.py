from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tomo.config import settings
from tomo.control_plane.http_adapter import create_app
from tomo.control_plane.memory_adapter import MemoryAdapter
from tomo.control_plane.plane import AgentControlPlane
from tomo.scheduler import SCHEDULED_TASKS_FILE, ScheduledTask


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".tomo")
    monkeypatch.setattr(settings, "control_api_key", None)
    plane = AgentControlPlane(memory=MemoryAdapter(tmp_path / "MEMORY.md"))
    return TestClient(create_app(plane))


def test_http_routes_preserve_v1_contract(client):
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/overview").status_code == 200
    assert client.get("/v1/memories").json() == {"entries": []}
    assert "connections" in client.get("/v1/connections").json()
    assert client.get("/v1/sessions").json() == {"sessions": []}
    assert client.get("/v1/integrations").json() == client.get("/v1/connections").json()
    assert client.get("/v1/scheduled-tasks").json() == {"tasks": []}
    assert client.get("/v1/approvals").status_code == 410


def test_http_import_memories(client, tmp_path):
    files = {"files": ("notes.md", "First imported memory.\n\nSecond imported memory.", "text/markdown")}

    response = client.post("/v1/memories/import", files=files)

    assert response.status_code == 200
    assert response.json()["imported"] == 2
    assert len(client.get("/v1/memories").json()["entries"]) == 2


def test_http_import_memories_rejects_pdf(client):
    files = {"files": ("notes.pdf", "not parsed", "application/pdf")}

    response = client.post("/v1/memories/import", files=files)

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported file type: .pdf"


def test_http_scheduled_task_patch_is_gone(client, tmp_path, monkeypatch):
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

    reenable = client.patch("/v1/scheduled-tasks/task-123", json={"enabled": True})
    assert reenable.status_code == 410
    assert reenable.json()["detail"] == "Scheduled tasks are read-only in the dashboard."


def test_http_approval_resolve_errors_for_missing_id(client):
    response = client.post("/v1/approvals/missing", json={"approved": True})

    assert response.status_code == 410
    assert response.json()["detail"] == "Approvals are handled in the active Tomo gateway, not the dashboard."


def test_api_key_required_when_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".tomo")
    monkeypatch.setattr(settings, "control_api_key", "secret-key")
    client = TestClient(create_app())

    denied = client.get("/v1/memories")
    assert denied.status_code == 401

    allowed = client.get("/v1/memories", headers={"Authorization": "Bearer secret-key"})
    assert allowed.status_code == 200


def test_compat_control_api_app_imports(tmp_path, monkeypatch):
    from tomo.control_api import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".tomo")
    monkeypatch.setattr(settings, "control_api_key", None)

    response = TestClient(app).get("/v1/health")
    assert response.status_code == 200
