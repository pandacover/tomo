from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TOMO_")

    model: str = "grok-4.3"
    data_dir: Path = Path(".tomo")
    telegram_bot_token: str | None = None
    telegram_allowed_chat_ids: str | None = None
    show_reasoning_summary: bool = False
    reasoning_effort: Literal["low", "medium", "high"] = "medium"


settings = Settings()
