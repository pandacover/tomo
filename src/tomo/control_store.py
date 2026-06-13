from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from .agent import SKILL_SOURCES
from .config import settings
from .cross_gateway_bridge import process_is_running
from .langgraph_agent import default_langgraph_tools
from .scheduler import SCHEDULED_TASKS_FILE, ScheduledTask
from .telegram_config import load_telegram_config
from .token_store import load_tokens
from .tools import MEMORY_FILE, now_iso

MEMORY_LINE_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
DESKTOP_PID_FILENAME = "desktop.pid"
TELEGRAM_PID_FILENAME = "telegram.pid"

TOOL_SCOPES: dict[str, list[str]] = {
    "browser": ["browser", "network"],
    "social_browser": ["browser", "network", "social"],
    "terminal": ["filesystem", "process"],
    "write_file": ["filesystem"],
    "edit_file": ["filesystem"],
    "read_file": ["filesystem"],
    "glob": ["filesystem"],
    "files_search": ["filesystem"],
    "web_search": ["network"],
    "web_fetch": ["network"],
    "generate_image": ["network"],
    "append_memory": ["memory"],
    "read_memory": ["memory"],
    "cross_gateway": ["gateway"],
}


def _memory_id(timestamp: str, text: str) -> str:
    digest = hashlib.sha256(f"{timestamp}:{text}".encode("utf-8")).hexdigest()
    return f"mem-{digest[:12]}"


def _parse_timestamp(raw: str) -> datetime | None:
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _freshness(timestamp: datetime, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    age = now - timestamp
    if age <= timedelta(days=1):
        return "new"
    if age <= timedelta(days=7):
        return "updated"
    return "stale"


def _updated_label(timestamp: datetime) -> str:
    local = timestamp.astimezone()
    return local.strftime("updated %H:%M")


def _title_from_text(text: str) -> str:
    words = text.strip().split()
    return " ".join(words[:6]).lower()


def parse_memory_entries(path: Path | None = None) -> list[dict[str, Any]]:
    memory_path = path or MEMORY_FILE
    if not memory_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    for line in memory_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = MEMORY_LINE_RE.match(stripped)
        if not match:
            continue
        timestamp_raw, text = match.groups()
        timestamp = _parse_timestamp(timestamp_raw)
        if timestamp is None:
            continue
        entries.append(
            {
                "id": _memory_id(timestamp_raw, text),
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "text": text,
                "title": _title_from_text(text),
                "status": "active",
                "freshness": _freshness(timestamp),
                "updatedLabel": _updated_label(timestamp),
            }
        )
    entries.sort(key=lambda item: item["timestamp"], reverse=True)
    return entries


def append_memory_entry(text: str, *, path: Path | None = None) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("memory text cannot be empty")
    memory_path = path or MEMORY_FILE
    timestamp = now_iso()
    line = f"[{timestamp}] {cleaned}\n"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    with open(memory_path, "a", encoding="utf-8") as handle:
        handle.write(line)
    parsed = _parse_timestamp(timestamp) or datetime.now(UTC)
    return {
        "id": _memory_id(timestamp, cleaned),
        "timestamp": parsed.isoformat().replace("+00:00", "Z"),
        "text": cleaned,
        "title": _title_from_text(cleaned),
        "status": "active",
        "freshness": "new",
        "updatedLabel": _updated_label(parsed),
    }


def import_memory_texts(texts: list[str]) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []
    for text in texts:
        for paragraph in re.split(r"\n\s*\n", text.strip()):
            cleaned = " ".join(paragraph.split())
            if cleaned:
                imported.append(append_memory_entry(cleaned))
    return imported


def _skill_description(skill_md: Path) -> str:
    content = skill_md.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped
    return "Skill instructions for Tomo agent runs."


def _discover_skills() -> list[dict[str, Any]]:
    discovered: dict[str, dict[str, Any]] = {}
    for source in SKILL_SOURCES:
        root = Path(source).expanduser()
        if not root.is_dir():
            continue
        for skill_md in root.glob("*/SKILL.md"):
            name = skill_md.parent.name
            if name in discovered:
                continue
            discovered[name] = {
                "id": f"int-skill-{name}",
                "name": name,
                "kind": "skill",
                "description": _skill_description(skill_md),
                "scopes": ["filesystem"],
                "enabled": True,
            }
    return sorted(discovered.values(), key=lambda item: item["name"])


def _tool_integration(tool: BaseTool) -> dict[str, Any]:
    name = tool.name
    description = (tool.description or "").strip().split("\n")[0] or f"Tomo {name} tool."
    return {
        "id": f"int-tool-{name}",
        "name": name,
        "kind": "tool",
        "description": description,
        "scopes": TOOL_SCOPES.get(name, ["agent"]),
        "enabled": True,
    }


def _read_pid(filename: str) -> int | None:
    path = settings.data_dir / filename
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _gateway_running(pid_filename: str) -> bool:
    pid = _read_pid(pid_filename)
    return bool(pid and process_is_running(pid))


def list_integrations() -> list[dict[str, Any]]:
    integrations = [_tool_integration(tool) for tool in default_langgraph_tools()]
    integrations.extend(_discover_skills())

    desktop_running = _gateway_running(DESKTOP_PID_FILENAME)
    integrations.append(
        {
            "id": "int-gateway-desktop",
            "name": "desktop",
            "kind": "gateway",
            "description": "Tray flyout chat gateway for local desktop sessions.",
            "scopes": ["approval_channel", "chat"],
            "enabled": desktop_running,
            "reviewRequired": not desktop_running,
        }
    )

    telegram_config = load_telegram_config()
    telegram_running = _gateway_running(TELEGRAM_PID_FILENAME)
    integrations.append(
        {
            "id": "int-gateway-telegram",
            "name": "telegram",
            "kind": "gateway",
            "description": "Routes chat and approvals through the configured Telegram bot.",
            "scopes": ["approval_channel", "chat"],
            "enabled": telegram_running and telegram_config is not None,
            "reviewRequired": telegram_config is None or not telegram_running,
        }
    )
    return integrations


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


def scheduled_task_to_api(task: ScheduledTask) -> dict[str, Any]:
    payload = task.payload or {}
    if task.kind == "reminder":
        label = "Reminder"
        description = str(payload.get("text", "Scheduled reminder"))
    else:
        label = str(payload.get("action", "Scheduled action"))
        description = json.dumps(payload, ensure_ascii=True)
    return {
        "id": task.id,
        "kind": task.kind,
        "label": label,
        "description": description,
        "scheduleLabel": task.scheduled_at,
        "status": task.status,
        "enabled": task.status == "pending",
        "requiresApproval": task.kind == "action",
    }


def overview_stats() -> dict[str, Any]:
    memories = parse_memory_entries()
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    updated_this_week = 0
    for entry in memories:
        timestamp = _parse_timestamp(entry["timestamp"])
        if timestamp and timestamp >= week_ago:
            updated_this_week += 1

    integrations = list_integrations()
    tasks = load_scheduled_tasks()
    pending_tasks = [task for task in tasks if task.status == "pending"]

    from .control_approval_store import get_control_approval_store

    pending_approvals = get_control_approval_store().list_pending()

    return {
        "memoryCount": len(memories),
        "memoriesUpdatedThisWeek": updated_this_week,
        "integrationCount": len(integrations),
        "integrationsNeedingReview": sum(1 for item in integrations if item.get("reviewRequired")),
        "scheduledTaskCount": len(tasks),
        "scheduledTasksGated": sum(1 for task in pending_tasks if task.kind == "action"),
        "pendingApprovalCount": len(pending_approvals),
    }


def health_payload() -> dict[str, Any]:
    tokens = load_tokens()
    return {
        "ok": True,
        "model": settings.model,
        "projectRoot": str(Path.cwd()),
        "authenticated": tokens is not None,
    }