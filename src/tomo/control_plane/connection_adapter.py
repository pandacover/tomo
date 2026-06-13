from __future__ import annotations

from pathlib import Path

from tomo.config import settings
from tomo.cross_gateway_bridge import process_is_running
from tomo.telegram_config import load_telegram_config

from .models import ControlConnection

DESKTOP_PID_FILENAME = "desktop.pid"
TELEGRAM_PID_FILENAME = "telegram.pid"


def read_pid(filename: str) -> int | None:
    path = settings.data_dir / filename
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def process_running(pid_filename: str) -> bool:
    pid = read_pid(pid_filename)
    return bool(pid and process_is_running(pid))


def count_tool_metadata(connector_dir: Path) -> int:
    tools_dir = connector_dir / "tools"
    if not tools_dir.is_dir():
        return 0
    return sum(1 for path in tools_dir.glob("*.json") if path.is_file())


class ConnectionAdapter:
    def list_chat_connections(self) -> list[ControlConnection]:
        desktop_running = process_running(DESKTOP_PID_FILENAME)
        telegram_config = load_telegram_config()
        telegram_running = process_running(TELEGRAM_PID_FILENAME)
        return [
            ControlConnection(
                id="chat-desktop",
                name="desktop",
                category="chat",
                description="Local desktop chat surface.",
                status="connected" if desktop_running else "available",
                enabled=desktop_running,
            ),
            ControlConnection(
                id="chat-telegram",
                name="telegram",
                category="chat",
                description="Telegram chat surface.",
                status="connected" if telegram_running else "needs_setup",
                enabled=telegram_running and telegram_config is not None,
                review_required=telegram_config is None,
            ),
        ]

    def list_app_connections(self) -> list[ControlConnection]:
        return []

    def list_social_connections(self) -> list[ControlConnection]:
        return [
            ControlConnection(
                id="social-x",
                name="x",
                category="social",
                description="Managed X social browser.",
                status="available",
                enabled=True,
            )
        ]

    def list_custom_connections(self) -> list[ControlConnection]:
        root = Path("mcps")
        if not root.is_dir():
            return []
        connections: list[ControlConnection] = []
        for connector_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            tool_count = count_tool_metadata(connector_dir)
            metadata: dict[str, str | int | bool | None] = {}
            if tool_count:
                metadata["toolCount"] = tool_count
            connections.append(
                ControlConnection(
                    id=f"custom-{connector_dir.name}",
                    name=connector_dir.name,
                    category="custom",
                    description="Local custom MCP connector.",
                    status="available",
                    enabled=True,
                    metadata=metadata,
                )
            )
        return connections

    def list(self) -> list[ControlConnection]:
        return [
            *self.list_chat_connections(),
            *self.list_app_connections(),
            *self.list_social_connections(),
            *self.list_custom_connections(),
        ]
