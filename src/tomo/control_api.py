from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .control_approval_store import get_control_approval_store
from .control_store import (
    append_memory_entry,
    health_payload,
    import_memory_texts,
    list_integrations,
    load_scheduled_tasks,
    overview_stats,
    parse_memory_entries,
    scheduled_task_to_api,
)
from .cross_gateway_bridge import process_is_running
from .scheduler import get_scheduler

CONTROL_API_VERSION = "v1"
CONTROL_API_PID_FILENAME = "control_api.pid"
CONTROL_API_LOG_FILENAME = "control_api.log"
IMPORT_SUFFIXES = {".txt", ".md", ".markdown", ".json"}


class AppendMemoryBody(BaseModel):
    text: str


class ResolveApprovalBody(BaseModel):
    approved: bool


class PatchScheduledTaskBody(BaseModel):
    enabled: bool | None = None
    status: str | None = None


def _parse_cors_origins(raw: str | None) -> list[str]:
    if not raw:
        return ["http://localhost:3000"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _require_api_key(authorization: str | None = Header(default=None)) -> None:
    expected = settings.control_api_key
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid API key.")


def create_app() -> FastAPI:
    app = FastAPI(title="Tomo Control API", version=CONTROL_API_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(settings.control_cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(f"/{CONTROL_API_VERSION}/health")
    def health() -> dict[str, Any]:
        return health_payload()

    @app.get(f"/{CONTROL_API_VERSION}/overview", dependencies=[Depends(_require_api_key)])
    def overview() -> dict[str, Any]:
        return overview_stats()

    @app.get(f"/{CONTROL_API_VERSION}/memories", dependencies=[Depends(_require_api_key)])
    def memories() -> dict[str, Any]:
        return {"entries": parse_memory_entries()}

    @app.post(f"/{CONTROL_API_VERSION}/memories", dependencies=[Depends(_require_api_key)])
    def create_memory(body: AppendMemoryBody) -> dict[str, Any]:
        try:
            entry = append_memory_entry(body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": entry}

    @app.post(f"/{CONTROL_API_VERSION}/memories/import", dependencies=[Depends(_require_api_key)])
    async def import_memories(files: list[UploadFile] = File(...)) -> dict[str, Any]:
        texts: list[str] = []
        for upload in files:
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in IMPORT_SUFFIXES:
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or '(none)'}")
            raw = await upload.read()
            texts.append(raw.decode("utf-8", errors="replace"))
        entries = import_memory_texts(texts)
        return {"imported": len(entries), "entries": entries}

    @app.get(f"/{CONTROL_API_VERSION}/integrations", dependencies=[Depends(_require_api_key)])
    def integrations() -> dict[str, Any]:
        return {"integrations": list_integrations()}

    @app.get(f"/{CONTROL_API_VERSION}/scheduled-tasks", dependencies=[Depends(_require_api_key)])
    def scheduled_tasks() -> dict[str, Any]:
        tasks = load_scheduled_tasks()
        return {"tasks": [scheduled_task_to_api(task) for task in tasks]}

    @app.patch(
        f"/{CONTROL_API_VERSION}/scheduled-tasks/{{task_id}}",
        dependencies=[Depends(_require_api_key)],
    )
    def patch_scheduled_task(task_id: str, body: PatchScheduledTaskBody) -> dict[str, Any]:
        scheduler = get_scheduler()
        if body.enabled is False or body.status == "cancelled":
            if not scheduler.cancel(task_id):
                raise HTTPException(status_code=404, detail="Scheduled task not found.")
        tasks = load_scheduled_tasks()
        match = next((task for task in tasks if task.id == task_id), None)
        if match is None:
            raise HTTPException(status_code=404, detail="Scheduled task not found.")
        return {"task": scheduled_task_to_api(match)}

    @app.get(f"/{CONTROL_API_VERSION}/approvals", dependencies=[Depends(_require_api_key)])
    def approvals() -> dict[str, Any]:
        store = get_control_approval_store()
        return {"approvals": [record.to_api() for record in store.list_pending()]}

    @app.post(
        f"/{CONTROL_API_VERSION}/approvals/{{approval_id}}",
        dependencies=[Depends(_require_api_key)],
    )
    def resolve_approval(approval_id: str, body: ResolveApprovalBody) -> dict[str, Any]:
        store = get_control_approval_store()
        if not store.resolve(approval_id, body.approved):
            raise HTTPException(status_code=404, detail="Pending approval not found.")
        return {"ok": True, "id": approval_id, "approved": body.approved}

    return app


app = create_app()


def pid_path() -> Path:
    return settings.data_dir / CONTROL_API_PID_FILENAME


def log_path() -> Path:
    return settings.data_dir / CONTROL_API_LOG_FILENAME


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def remove_pid(path: Path) -> None:
    if path.exists():
        path.unlink()


def stop_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], check=False, capture_output=True)
        return
    os.kill(pid, signal.SIGTERM)


def run_control_api(*, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        "tomo.control_api:app",
        host=host or settings.control_api_host,
        port=port or settings.control_api_port,
        log_level="info",
    )


def start_control_api(*, host: str | None = None, port: int | None = None) -> None:
    path = pid_path()
    existing = read_pid(path)
    if existing and process_is_running(existing):
        print(f"Tomo control API is already running with PID {existing}.")
        return

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path(), "a", encoding="utf-8")
    host_value = host or settings.control_api_host
    port_value = port or settings.control_api_port
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "tomo.control_api:app",
        "--host",
        host_value,
        "--port",
        str(port_value),
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=Path.cwd(),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    write_pid(path, process.pid)
    print(f"Tomo control API started with PID {process.pid}.")
    print(f"Listening on http://{host_value}:{port_value}")


def stop_control_api() -> None:
    path = pid_path()
    pid = read_pid(path)
    if pid is None:
        print("Tomo control API is not running.")
        return
    if not process_is_running(pid):
        remove_pid(path)
        print("Tomo control API was not running; removed stale PID file.")
        return
    stop_process(pid)
    time.sleep(0.5)
    remove_pid(path)
    print("Tomo control API stopped.")


def restart_control_api(*, host: str | None = None, port: int | None = None) -> None:
    stop_control_api()
    start_control_api(host=host, port=port)