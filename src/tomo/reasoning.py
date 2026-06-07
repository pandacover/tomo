from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from .config import settings


ReasoningEffort = Literal["low", "medium", "high"]
REASONING_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high"})

PREFERENCES_FILENAME = "preferences.json"


def preferences_path() -> Path:
    return settings.data_dir / PREFERENCES_FILENAME


def load_preferences() -> dict[str, object]:
    path = preferences_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_preferences(**updates: object) -> None:
    data = load_preferences()
    data.update(updates)
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def effective_reasoning_effort() -> str:
    stored = load_preferences().get("reasoning_effort")
    if isinstance(stored, str) and stored in REASONING_EFFORTS:
        return stored
    return settings.reasoning_effort


def effective_show_reasoning_trace(*, chat_override: bool | None = None) -> bool:
    if chat_override is not None:
        return chat_override
    stored = load_preferences().get("show_reasoning_summary")
    if isinstance(stored, bool):
        return stored
    return settings.show_reasoning_summary


def parse_reasoning_command_args(argument: str | None) -> tuple[str | None, str | None]:
    """Return (action, value) for subcommands, or (status, None) when no args."""
    if argument is None:
        return ("status", None)
    parts = argument.split()
    if not parts:
        return ("status", None)
    head = parts[0]
    if head in REASONING_EFFORTS:
        return ("effort", head)
    if head == "trace" and len(parts) >= 2:
        tail = parts[1]
        if tail in {"on", "off"}:
            return ("trace", tail)
    return ("invalid", None)


def reasoning_usage_message() -> str:
    return (
        "Usage: /reasoning low|medium|high — set API reasoning effort; "
        "/reasoning trace on|off — show reasoning summary after replies; "
        "/reasoning — show current settings."
    )


def format_reasoning_status(*, effort: str, trace: bool) -> str:
    return f"Reasoning effort: {effort}. Reasoning trace: {'on' if trace else 'off'}."