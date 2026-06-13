from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tomo.config import settings
from tomo.token_store import load_tokens

from .connection_adapter import ConnectionAdapter
from .memory_adapter import MemoryAdapter, parse_timestamp
from .models import (
    ControlConnection,
    ControlHealth,
    ControlMemoryEntry,
    ControlOverview,
    ControlScheduledTask,
    ControlSession,
    MemoryImportFile,
    MemoryImportResult,
)
from .scheduler_adapter import SchedulerAdapter
from .session_adapter import SessionAdapter


class AgentControlPlane:
    def __init__(
        self,
        *,
        memory: MemoryAdapter | None = None,
        connections: ConnectionAdapter | None = None,
        scheduler: SchedulerAdapter | None = None,
        sessions: SessionAdapter | None = None,
    ) -> None:
        self.memory = memory or MemoryAdapter()
        self.connections = connections or ConnectionAdapter()
        self.scheduler = scheduler or SchedulerAdapter()
        self.sessions = sessions or SessionAdapter()

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

        connections = self.list_connections()
        tasks = self.list_scheduled_tasks()
        sessions = self.list_sessions()
        return ControlOverview(
            memory_count=len(memories),
            memories_updated_this_week=updated_this_week,
            session_count=len(sessions),
            connection_count=len(connections),
            connections_needing_review=sum(1 for item in connections if item.review_required),
            scheduled_task_count=len(tasks),
        )

    def list_memories(self) -> list[ControlMemoryEntry]:
        return self.memory.list()

    def append_memory(self, text: str) -> ControlMemoryEntry:
        return self.memory.append(text)

    def import_memories(self, files: list[MemoryImportFile]) -> MemoryImportResult:
        entries = self.memory.import_files(files)
        return MemoryImportResult(imported=len(entries), entries=entries)

    def list_connections(self) -> list[ControlConnection]:
        return self.connections.list()

    def list_integrations(self) -> list[ControlConnection]:
        return self.list_connections()

    def list_scheduled_tasks(self) -> list[ControlScheduledTask]:
        return self.scheduler.list()

    def list_sessions(self) -> list[ControlSession]:
        return self.sessions.list()
