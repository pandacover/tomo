from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tomo.config import settings

from .memory_adapter import SUPPORTED_IMPORT_SUFFIXES
from .models import MemoryImportFile
from .plane import AgentControlPlane

CONTROL_API_VERSION = "v1"


class AppendMemoryBody(BaseModel):
    text: str


class ResolveApprovalBody(BaseModel):
    approved: bool


class PatchScheduledTaskBody(BaseModel):
    enabled: bool | None = None
    status: str | None = None


def parse_cors_origins(raw: str | None) -> list[str]:
    if not raw:
        return ["http://localhost:3000"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    expected = settings.control_api_key
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid API key.")


def dump_model(model: object) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True)
    raise TypeError(f"Cannot serialize {type(model).__name__}")


def create_app(control_plane: AgentControlPlane | None = None) -> FastAPI:
    plane = control_plane or AgentControlPlane()
    app = FastAPI(title="Tomo Agent Control", version=CONTROL_API_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_cors_origins(settings.control_cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(f"/{CONTROL_API_VERSION}/health")
    def health() -> dict[str, Any]:
        return dump_model(plane.health())

    @app.get(f"/{CONTROL_API_VERSION}/overview", dependencies=[Depends(require_api_key)])
    def overview() -> dict[str, Any]:
        return dump_model(plane.overview())

    @app.get(f"/{CONTROL_API_VERSION}/memories", dependencies=[Depends(require_api_key)])
    def memories() -> dict[str, Any]:
        return {"entries": [dump_model(entry) for entry in plane.list_memories()]}

    @app.post(f"/{CONTROL_API_VERSION}/memories", dependencies=[Depends(require_api_key)])
    def create_memory(body: AppendMemoryBody) -> dict[str, Any]:
        try:
            entry = plane.append_memory(body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": dump_model(entry)}

    @app.post(f"/{CONTROL_API_VERSION}/memories/import", dependencies=[Depends(require_api_key)])
    async def import_memories(files: list[UploadFile] = File(...)) -> dict[str, Any]:
        imports: list[MemoryImportFile] = []
        for upload in files:
            filename = upload.filename or ""
            suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if suffix not in SUPPORTED_IMPORT_SUFFIXES:
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or '(none)'}")
            raw = await upload.read()
            imports.append(MemoryImportFile(filename=filename, text=raw.decode("utf-8", errors="replace")))
        result = plane.import_memories(imports)
        return dump_model(result)

    @app.get(f"/{CONTROL_API_VERSION}/integrations", dependencies=[Depends(require_api_key)])
    def integrations() -> dict[str, Any]:
        return {"integrations": [dump_model(integration) for integration in plane.list_integrations()]}

    @app.get(f"/{CONTROL_API_VERSION}/scheduled-tasks", dependencies=[Depends(require_api_key)])
    def scheduled_tasks() -> dict[str, Any]:
        return {"tasks": [dump_model(task) for task in plane.list_scheduled_tasks()]}

    @app.patch(
        f"/{CONTROL_API_VERSION}/scheduled-tasks/{{task_id}}",
        dependencies=[Depends(require_api_key)],
    )
    def patch_scheduled_task(task_id: str, body: PatchScheduledTaskBody) -> dict[str, Any]:
        wants_cancel = body.enabled is False or body.status == "cancelled"
        wants_enable = body.enabled is True
        if wants_enable:
            raise HTTPException(
                status_code=400,
                detail="Re-enabling scheduled tasks from the dashboard is not supported yet.",
            )
        if not wants_cancel:
            raise HTTPException(
                status_code=400,
                detail="Only scheduled task cancellation is supported from the dashboard.",
            )
        try:
            task = plane.cancel_scheduled_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Scheduled task not found.") from exc
        return {"task": dump_model(task)}

    @app.get(f"/{CONTROL_API_VERSION}/approvals", dependencies=[Depends(require_api_key)])
    def approvals() -> dict[str, Any]:
        return {"approvals": [dump_model(approval) for approval in plane.list_pending_approvals()]}

    @app.post(
        f"/{CONTROL_API_VERSION}/approvals/{{approval_id}}",
        dependencies=[Depends(require_api_key)],
    )
    def resolve_approval(approval_id: str, body: ResolveApprovalBody) -> dict[str, Any]:
        try:
            resolution = plane.resolve_approval(approval_id, body.approved)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Pending approval not found.") from exc
        return dump_model(resolution)

    return app


app = create_app()
