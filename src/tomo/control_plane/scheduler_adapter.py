from __future__ import annotations

import json
from pathlib import Path

from tomo.scheduler import SCHEDULED_TASKS_FILE, ScheduledTask, get_scheduler

from .models import ControlScheduledTask


def load_scheduled_tasks(*, include_all: bool = True) -> list[ScheduledTask]:
    path = Path(SCHEDULED_TASKS_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    tasks = [ScheduledTask(**item) for item in data if isinstance(item, dict)]
    if include_all:
        return sorted(tasks, key=lambda task: task.scheduled_at, reverse=True)
    return [task for task in tasks if task.status == "pending"]


def scheduled_task_model(task: ScheduledTask) -> ControlScheduledTask:
    payload = task.payload or {}
    if task.kind == "reminder":
        label = "Reminder"
        description = str(payload.get("text", "Scheduled reminder"))
    else:
        label = str(payload.get("action", "Scheduled action"))
        description = json.dumps(payload, ensure_ascii=True)
    return ControlScheduledTask(
        id=task.id,
        kind=task.kind,
        label=label,
        description=description,
        schedule_label=task.scheduled_at,
        status=task.status,
        enabled=task.status == "pending",
        requires_approval=task.kind == "action",
    )


class SchedulerAdapter:
    def list(self) -> list[ControlScheduledTask]:
        return [scheduled_task_model(task) for task in load_scheduled_tasks()]

    def cancel(self, task_id: str) -> ControlScheduledTask:
        scheduler = get_scheduler()
        if not scheduler.cancel(task_id):
            raise KeyError("Scheduled task not found.")
        match = next((task for task in load_scheduled_tasks() if task.id == task_id), None)
        if match is None:
            raise KeyError("Scheduled task not found.")
        return scheduled_task_model(match)
