"""Lightweight project-local scheduler for Tomo.

Stores tasks in .tomo/scheduled_tasks.json (project-local).
Supports one-shot reminders and actions only.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable


SCHEDULED_TASKS_FILE = ".tomo/scheduled_tasks.json"
POLL_INTERVAL_SECONDS = 5.0


@dataclass
class ScheduledTask:
    id: str
    kind: str  # "reminder" | "action"
    payload: dict[str, Any]
    scheduled_at: str  # ISO8601 UTC
    status: str = "pending"  # pending | fired | cancelled | error
    result: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class Scheduler:
    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or Path(SCHEDULED_TASKS_FILE)
        self._lock = threading.RLock()
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._on_reminder: Callable[[str], None] | None = None
        self._load()

    def set_reminder_callback(self, callback: Callable[[str], None] | None) -> None:
        self._on_reminder = callback

    def _ensure_storage_dir(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        if not self.storage_path.exists():
            self._tasks = {}
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self._tasks = {}
            for item in data:
                task = ScheduledTask(**item)
                if task.status == "pending":
                    self._tasks[task.id] = task
        except Exception:
            self._tasks = {}

    def _save(self) -> None:
        self._ensure_storage_dir()
        all_tasks = list(self._tasks.values())
        # Also persist fired/cancelled for history (keep last 200)
        try:
            if self.storage_path.exists():
                existing = json.loads(self.storage_path.read_text(encoding="utf-8"))
                for item in existing:
                    if item.get("status") != "pending" and len(all_tasks) < 200:
                        all_tasks.append(ScheduledTask(**item))
        except Exception:
            pass
        # Deduplicate by id, keep newest
        seen: dict[str, ScheduledTask] = {}
        for t in sorted(all_tasks, key=lambda x: x.created_at, reverse=True):
            if t.id not in seen:
                seen[t.id] = t
        final = list(seen.values())[:200]
        self.storage_path.write_text(
            json.dumps([asdict(t) for t in final], indent=2),
            encoding="utf-8",
        )

    def schedule_reminder(self, text: str, when: str | datetime) -> str:
        """Schedule a reminder. Returns task id."""
        scheduled_at = self._parse_when(when)
        task = ScheduledTask(
            id=str(uuid.uuid4()),
            kind="reminder",
            payload={"text": text},
            scheduled_at=scheduled_at.isoformat(),
        )
        with self._lock:
            self._tasks[task.id] = task
            self._save()
        return task.id

    def schedule_action(self, action: str, payload: dict[str, Any], when: str | datetime) -> str:
        """Schedule an action (tool call, terminal, etc.). Returns task id."""
        scheduled_at = self._parse_when(when)
        task = ScheduledTask(
            id=str(uuid.uuid4()),
            kind="action",
            payload={"action": action, **payload},
            scheduled_at=scheduled_at.isoformat(),
        )
        with self._lock:
            self._tasks[task.id] = task
            self._save()
        return task.id

    def list_tasks(self, include_past: bool = False) -> list[ScheduledTask]:
        with self._lock:
            now = datetime.now(UTC)
            result = []
            for t in self._tasks.values():
                if t.status != "pending" and not include_past:
                    continue
                result.append(t)
            return sorted(result, key=lambda x: x.scheduled_at)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == "pending":
                task.status = "cancelled"
                self._save()
                return True
            return False

    def _parse_when(self, when: str | datetime) -> datetime:
        if isinstance(when, datetime):
            return when if when.tzinfo else when.replace(tzinfo=UTC)
        when = when.strip().lower()
        now = datetime.now(UTC)
        if when in {"now", "immediately"}:
            return now
        # Very small parser: "in 5 minutes", "in 2 hours", "tomorrow at 09:00"
        if when.startswith("in "):
            parts = when[3:].split()
            if len(parts) >= 2:
                try:
                    value = int(parts[0])
                    unit = parts[1].rstrip("s")
                    if unit == "minute":
                        return now + timedelta(minutes=value)
                    if unit == "hour":
                        return now + timedelta(hours=value)
                    if unit == "day":
                        return now + timedelta(days=value)
                except ValueError:
                    pass
        # Fallback: try ISO parse
        try:
            return datetime.fromisoformat(when.replace("Z", "+00:00"))
        except Exception:
            return now + timedelta(minutes=5)  # safe default

    def _poll_once(self) -> None:
        now = datetime.now(UTC)
        to_fire: list[ScheduledTask] = []
        with self._lock:
            for task in list(self._tasks.values()):
                if task.status != "pending":
                    continue
                try:
                    scheduled = datetime.fromisoformat(task.scheduled_at)
                    if scheduled <= now:
                        to_fire.append(task)
                except Exception:
                    task.status = "error"
                    task.result = "Invalid scheduled_at"
        for task in to_fire:
            self._fire(task)

    def _fire(self, task: ScheduledTask) -> None:
        with self._lock:
            if task.status != "pending":
                return
            task.status = "fired"
            try:
                if task.kind == "reminder" and self._on_reminder:
                    text = task.payload.get("text", "")
                    self._on_reminder(text)
                    task.result = "delivered"
                elif task.kind == "action":
                    # Placeholder: real execution would go through TomoGateway
                    task.result = f"action {task.payload.get('action')} would run here"
                else:
                    task.result = "unknown kind"
            except Exception as exc:
                task.status = "error"
                task.result = str(exc)
            self._save()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.wait(POLL_INTERVAL_SECONDS):
            try:
                self._poll_once()
            except Exception:
                pass  # never let the scheduler kill the app


# Global singleton for easy wiring
_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


def start_scheduler(on_reminder: Callable[[str], None] | None = None) -> Scheduler:
    sched = get_scheduler()
    if on_reminder:
        sched.set_reminder_callback(on_reminder)
    sched.start()
    return sched


def stop_scheduler() -> None:
    if _scheduler:
        _scheduler.stop()