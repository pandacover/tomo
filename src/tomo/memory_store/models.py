from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MemorySource = Literal["manual", "import", "migration", "agent"]


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    created_at: str
    text: str
    source: MemorySource
    source_ref: str | None = None
    disabled_at: str | None = None
