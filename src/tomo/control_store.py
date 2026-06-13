from __future__ import annotations

from pathlib import Path
from typing import Any

from .control_plane.connection_adapter import ConnectionAdapter
from .control_plane.memory_adapter import MemoryAdapter
from .control_plane.models import MemoryImportFile
from .control_plane.plane import AgentControlPlane
from .control_plane.scheduler_adapter import load_scheduled_tasks, scheduled_task_model


def _dump(model: object) -> dict[str, Any]:
    return model.model_dump(by_alias=True) if hasattr(model, "model_dump") else dict(model)  # type: ignore[arg-type]


def parse_memory_entries(path: Path | None = None) -> list[dict[str, Any]]:
    return [_dump(entry) for entry in MemoryAdapter(path).list()]


def append_memory_entry(text: str, *, path: Path | None = None) -> dict[str, Any]:
    return _dump(MemoryAdapter(path).append(text))


def import_memory_texts(texts: list[str]) -> list[dict[str, Any]]:
    adapter = MemoryAdapter()
    files = [MemoryImportFile(filename=f"import-{index}.txt", text=text) for index, text in enumerate(texts)]
    return [_dump(entry) for entry in adapter.import_files(files)]


def list_integrations() -> list[dict[str, Any]]:
    return [_dump(connection) for connection in ConnectionAdapter().list()]


def scheduled_task_to_api(task: object) -> dict[str, Any]:
    return _dump(scheduled_task_model(task))  # type: ignore[arg-type]


def overview_stats() -> dict[str, Any]:
    return _dump(AgentControlPlane().overview())


def health_payload() -> dict[str, Any]:
    return _dump(AgentControlPlane().health())
