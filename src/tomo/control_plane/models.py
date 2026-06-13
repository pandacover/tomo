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
    session_count: int = Field(alias="sessionCount")
    connection_count: int = Field(alias="connectionCount")
    connections_needing_review: int = Field(alias="connectionsNeedingReview")
    scheduled_task_count: int = Field(alias="scheduledTaskCount")


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


class ControlConnection(ControlModel):
    id: str
    name: str
    category: Literal["chat", "app", "social", "custom"]
    description: str
    status: Literal["connected", "available", "needs_setup", "disabled", "unknown"]
    enabled: bool
    review_required: bool = Field(default=False, alias="reviewRequired")
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)


class ControlSession(ControlModel):
    id: str
    name: str
    created_date: str = Field(alias="createdDate")
    updated_date: str = Field(alias="updatedDate")
    message_count: int = Field(alias="messageCount")
