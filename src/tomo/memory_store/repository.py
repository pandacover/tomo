from __future__ import annotations

from typing import Protocol

from .models import MemoryRecord


class MemoryRepository(Protocol):
    def list(self) -> list[MemoryRecord]: ...

    def append(self, text: str, *, source: str = "manual", source_ref: str | None = None) -> MemoryRecord: ...

    def import_texts(self, texts: list[str], *, source_ref: str | None = None) -> list[MemoryRecord]: ...

    def mark_disabled(self, memory_id: str) -> bool: ...
