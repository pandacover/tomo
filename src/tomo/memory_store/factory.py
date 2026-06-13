from __future__ import annotations

from tomo.config import settings

from .repository import MemoryRepository
from .sqlite_repository import SQLiteMemoryRepository


def get_memory_repository() -> MemoryRepository:
    return SQLiteMemoryRepository(settings.data_dir / "tomo.db")
