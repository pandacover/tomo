from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool

from tomo.agent import SKILL_SOURCES
from tomo.config import settings
from tomo.cross_gateway_bridge import process_is_running
from tomo.langgraph_agent import default_langgraph_tools
from tomo.telegram_config import load_telegram_config

from .models import ControlIntegration

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


def skill_description(skill_md: Path) -> str:
    content = skill_md.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped
    return "Skill instructions for Tomo agent runs."


def discover_skills() -> list[ControlIntegration]:
    discovered: dict[str, ControlIntegration] = {}
    for source in SKILL_SOURCES:
        root = Path(source).expanduser()
        if not root.is_dir():
            continue
        for skill_md in root.glob("*/SKILL.md"):
            name = skill_md.parent.name
            if name in discovered:
                continue
            discovered[name] = ControlIntegration(
                id=f"int-skill-{name}",
                name=name,
                kind="skill",
                description=skill_description(skill_md),
                scopes=["filesystem"],
                enabled=True,
            )
    return sorted(discovered.values(), key=lambda item: item.name)


def tool_integration(tool: BaseTool) -> ControlIntegration:
    name = tool.name
    description = (tool.description or "").strip().split("\n")[0] or f"Tomo {name} tool."
    return ControlIntegration(
        id=f"int-tool-{name}",
        name=name,
        kind="tool",
        description=description,
        scopes=TOOL_SCOPES.get(name, ["agent"]),
        enabled=True,
    )


def read_pid(filename: str) -> int | None:
    path = settings.data_dir / filename
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def gateway_running(pid_filename: str) -> bool:
    pid = read_pid(pid_filename)
    return bool(pid and process_is_running(pid))


class IntegrationAdapter:
    def list_tools(self) -> list[ControlIntegration]:
        return [tool_integration(tool) for tool in default_langgraph_tools()]

    def list_skills(self) -> list[ControlIntegration]:
        return discover_skills()

    def list_gateways(self) -> list[ControlIntegration]:
        desktop_running = gateway_running(DESKTOP_PID_FILENAME)
        telegram_config = load_telegram_config()
        telegram_running = gateway_running(TELEGRAM_PID_FILENAME)
        return [
            ControlIntegration(
                id="int-gateway-desktop",
                name="desktop",
                kind="gateway",
                description="Tray flyout chat gateway for local desktop sessions.",
                scopes=["approval_channel", "chat"],
                enabled=desktop_running,
                review_required=not desktop_running,
            ),
            ControlIntegration(
                id="int-gateway-telegram",
                name="telegram",
                kind="gateway",
                description="Routes chat and approvals through the configured Telegram bot.",
                scopes=["approval_channel", "chat"],
                enabled=telegram_running and telegram_config is not None,
                review_required=telegram_config is None or not telegram_running,
            ),
        ]

    def list(self) -> list[ControlIntegration]:
        return [*self.list_tools(), *self.list_skills(), *self.list_gateways()]
