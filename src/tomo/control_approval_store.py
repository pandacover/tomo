from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .tools import ApprovalRequest

CONTROL_APPROVALS_FILE = "control_approvals.json"
POLL_INTERVAL_SECONDS = 0.1


@dataclass
class PendingApprovalRecord:
    id: str
    operation: str
    target: str
    reason: str
    channel_id: str

    def to_api(self) -> dict[str, str]:
        return {
            "id": self.id,
            "operation": self.operation,
            "target": self.target,
            "reason": self.reason,
            "channelId": self.channel_id,
        }


class ControlApprovalStore:
    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or settings.data_dir / CONTROL_APPROVALS_FILE
        self._lock = threading.RLock()

    def _ensure_dir(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.storage_path.exists():
            return {"pending": [], "resolutions": {}}
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except Exception:
            return {"pending": [], "resolutions": {}}
        if not isinstance(data, dict):
            return {"pending": [], "resolutions": {}}
        data.setdefault("pending", [])
        data.setdefault("resolutions", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self._ensure_dir()
        self.storage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def create(self, channel_id: str, request: ApprovalRequest, *, approval_id: str | None = None) -> str:
        record_id = approval_id or str(uuid.uuid4())
        record = PendingApprovalRecord(
            id=record_id,
            operation=request.operation,
            target=request.target,
            reason=request.reason,
            channel_id=channel_id,
        )
        with self._lock:
            data = self._load()
            pending = [item for item in data["pending"] if item.get("id") != record_id]
            pending.append(asdict(record))
            data["pending"] = pending
            data["resolutions"].pop(record_id, None)
            self._save(data)
        return record_id

    def list_pending(self) -> list[PendingApprovalRecord]:
        with self._lock:
            data = self._load()
            return [PendingApprovalRecord(**item) for item in data["pending"]]

    def get_resolution(self, approval_id: str) -> bool | None:
        with self._lock:
            resolution = self._load()["resolutions"].get(approval_id)
        if not isinstance(resolution, dict):
            return None
        return bool(resolution.get("approved"))

    def resolve(self, approval_id: str, approved: bool) -> bool:
        with self._lock:
            data = self._load()
            pending = [item for item in data["pending"] if item.get("id") == approval_id]
            if not pending:
                return False
            data["pending"] = [item for item in data["pending"] if item.get("id") != approval_id]
            data["resolutions"][approval_id] = {"approved": bool(approved)}
            self._save(data)
        return True

    def wait_for_resolution(self, approval_id: str, *, timeout_seconds: float = 3600.0) -> bool | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            resolution = self.get_resolution(approval_id)
            if resolution is not None:
                return resolution
            time.sleep(POLL_INTERVAL_SECONDS)
        return None

    def clear_resolution(self, approval_id: str) -> None:
        with self._lock:
            data = self._load()
            data["resolutions"].pop(approval_id, None)
            self._save(data)


_store: ControlApprovalStore | None = None


def get_control_approval_store() -> ControlApprovalStore:
    global _store
    if _store is None:
        _store = ControlApprovalStore()
    return _store