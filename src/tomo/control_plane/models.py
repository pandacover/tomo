from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ControlModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ControlHealth(ControlModel):
    ok: bool
    model: str
    project_root: str = Field(alias="projectRoot")
    authenticated: bool


class ControlOverview(ControlModel):
    memory_count: int = Field(alias="memoryCount")
    memories_updated_this_week: int = Field(alias="memoriesUpdatedThisWeek")
    tool_count: int = Field(alias="toolCount")
    skill_count: int = Field(alias="skillCount")
    gateway_count: int = Field(alias="gatewayCount")
    gateways_needing_review: int = Field(alias="gatewaysNeedingReview")
    integration_count: int = Field(alias="integrationCount")
    integrations_needing_review: int = Field(alias="integrationsNeedingReview")
    scheduled_task_count: int = Field(alias="scheduledTaskCount")
    scheduled_tasks_gated: int = Field(alias="scheduledTasksGated")
    pending_approval_count: int = Field(alias="pendingApprovalCount")


class ControlMemoryEntry(ControlModel):
    id: str
    timestamp: str
    text: str
    title: str
    status: Literal["active", "disabled"]
    freshness: Literal["new", "updated", "stale"]
    updated_label: str = Field(alias="updatedLabel")


class MemoryImportFile(BaseModel):
    filename: str
    text: str

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.lower()


class MemoryImportResult(ControlModel):
    imported: int
    entries: list[ControlMemoryEntry]


class ControlApproval(ControlModel):
    id: str
    operation: str
    target: str
    reason: str
    channel_id: str | None = Field(default=None, alias="channelId")


class ControlApprovalResolution(ControlModel):
    ok: bool
    id: str
    approved: bool


class ControlScheduledTask(ControlModel):
    id: str
    kind: Literal["reminder", "action"]
    label: str
    description: str
    schedule_label: str = Field(alias="scheduleLabel")
    status: Literal["pending", "fired", "cancelled", "error"]
    enabled: bool
    requires_approval: bool = Field(alias="requiresApproval")


class ControlIntegration(ControlModel):
    id: str
    name: str
    kind: Literal["tool", "skill", "gateway"]
    description: str
    scopes: list[str]
    enabled: bool
    review_required: bool = Field(default=False, alias="reviewRequired")
