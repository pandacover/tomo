from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tomo.config import settings
from tomo.token_store import load_tokens

from .approval_adapter import ApprovalAdapter
from .integration_adapter import IntegrationAdapter
from .memory_adapter import MemoryAdapter, parse_timestamp
from .models import (
    ControlApproval,
    ControlApprovalResolution,
    ControlHealth,
    ControlIntegration,
    ControlMemoryEntry,
    ControlOverview,
    ControlScheduledTask,
    MemoryImportFile,
    MemoryImportResult,
)
from .scheduler_adapter import SchedulerAdapter


class AgentControlPlane:
    def __init__(
        self,
        *,
        memory: MemoryAdapter | None = None,
        approvals: ApprovalAdapter | None = None,
        integrations: IntegrationAdapter | None = None,
        scheduler: SchedulerAdapter | None = None,
    ) -> None:
        self.memory = memory or MemoryAdapter()
        self.approvals = approvals or ApprovalAdapter()
        self.integrations = integrations or IntegrationAdapter()
        self.scheduler = scheduler or SchedulerAdapter()

    def health(self) -> ControlHealth:
        return ControlHealth(
            ok=True,
            model=settings.model,
            project_root=str(Path.cwd()),
            authenticated=load_tokens() is not None,
        )

    def overview(self) -> ControlOverview:
        memories = self.list_memories()
        now = datetime.now(UTC)
        week_ago = now - timedelta(days=7)
        updated_this_week = 0
        for entry in memories:
            timestamp = parse_timestamp(entry.timestamp)
            if timestamp and timestamp >= week_ago:
                updated_this_week += 1

        integrations = self.list_integrations()
        tasks = self.list_scheduled_tasks()
        pending_tasks = [task for task in tasks if task.status == "pending"]
        pending_approvals = self.list_pending_approvals()
        tools = [item for item in integrations if item.kind == "tool"]
        skills = [item for item in integrations if item.kind == "skill"]
        gateways = [item for item in integrations if item.kind == "gateway"]
        integrations_needing_review = sum(1 for item in integrations if item.review_required)
        return ControlOverview(
            memory_count=len(memories),
            memories_updated_this_week=updated_this_week,
            tool_count=len(tools),
            skill_count=len(skills),
            gateway_count=len(gateways),
            gateways_needing_review=sum(1 for item in gateways if item.review_required),
            integration_count=len(integrations),
            integrations_needing_review=integrations_needing_review,
            scheduled_task_count=len(tasks),
            scheduled_tasks_gated=sum(1 for task in pending_tasks if task.kind == "action"),
            pending_approval_count=len(pending_approvals),
        )

    def list_memories(self) -> list[ControlMemoryEntry]:
        return self.memory.list()

    def append_memory(self, text: str) -> ControlMemoryEntry:
        return self.memory.append(text)

    def import_memories(self, files: list[MemoryImportFile]) -> MemoryImportResult:
        entries = self.memory.import_files(files)
        return MemoryImportResult(imported=len(entries), entries=entries)

    def list_integrations(self) -> list[ControlIntegration]:
        return self.integrations.list()

    def list_scheduled_tasks(self) -> list[ControlScheduledTask]:
        return self.scheduler.list()

    def cancel_scheduled_task(self, task_id: str) -> ControlScheduledTask:
        return self.scheduler.cancel(task_id)

    def list_pending_approvals(self) -> list[ControlApproval]:
        return self.approvals.list_pending()

    def resolve_approval(self, approval_id: str, approved: bool) -> ControlApprovalResolution:
        return self.approvals.resolve(approval_id, approved)
