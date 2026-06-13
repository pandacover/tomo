from __future__ import annotations

import pytest
from pydantic import ValidationError

from tomo.control_plane.models import ControlHealth, ControlScheduledTask


def test_control_models_dump_camel_case_aliases():
    health = ControlHealth(
        ok=True,
        model="grok",
        project_root="C:/repo",
        authenticated=False,
    )

    assert health.model_dump(by_alias=True) == {
        "ok": True,
        "model": "grok",
        "projectRoot": "C:/repo",
        "authenticated": False,
    }


def test_control_models_accept_alias_input():
    task = ControlScheduledTask(
        id="task-1",
        kind="reminder",
        label="Reminder",
        description="check",
        scheduleLabel="2026-06-14T08:30:00+00:00",
        status="pending",
        enabled=True,
        requiresApproval=False,
    )

    assert task.schedule_label == "2026-06-14T08:30:00+00:00"
    assert task.requires_approval is False


def test_control_models_reject_invalid_enums():
    with pytest.raises(ValidationError):
        ControlScheduledTask(
            id="task-1",
            kind="cron",
            label="Task",
            description="bad",
            schedule_label="2026-06-14T08:30:00+00:00",
            status="pending",
            enabled=True,
            requires_approval=False,
        )
