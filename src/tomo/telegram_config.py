from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import settings


@dataclass
class TelegramConfig:
    bot_token: str
    allowed_chat_ids: list[int]


def telegram_config_path() -> Path:
    return settings.data_dir / "telegram.json"


def load_telegram_config() -> TelegramConfig | None:
    path = telegram_config_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return TelegramConfig(
        bot_token=str(data["bot_token"]),
        allowed_chat_ids=[int(item) for item in data.get("allowed_chat_ids", [])],
    )


def save_telegram_config(config: TelegramConfig) -> None:
    path = telegram_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2))
    path.chmod(0o600)


def delete_telegram_config() -> None:
    path = telegram_config_path()
    if path.exists():
        path.unlink()


def resolved_telegram_config() -> TelegramConfig | None:
    saved = load_telegram_config()
    bot_token = settings.telegram_bot_token or (saved.bot_token if saved else None)
    allowed_chat_ids = (
        parse_allowed_chat_ids(settings.telegram_allowed_chat_ids)
        if settings.telegram_allowed_chat_ids is not None
        else (saved.allowed_chat_ids if saved else [])
    )
    if not bot_token:
        return None
    return TelegramConfig(bot_token=bot_token, allowed_chat_ids=allowed_chat_ids)


def parse_allowed_chat_ids(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(item.strip()) for item in value.split(",") if item.strip()]
