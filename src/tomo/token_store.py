from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import settings


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    token_endpoint: str
    redirect_uri: str
    expires_at: float
    token_type: str = "Bearer"

    @property
    def expired(self) -> bool:
        return self.expires_at <= time.time() + 120


def auth_path() -> Path:
    return settings.data_dir / "auth.json"


def load_tokens() -> TokenSet | None:
    path = auth_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return TokenSet(**data)


def save_tokens(tokens: TokenSet) -> None:
    path = auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(tokens), indent=2))
    path.chmod(0o600)


def delete_tokens() -> None:
    path = auth_path()
    if path.exists():
        path.unlink()


def ensure_logged_in() -> bool:
    """Print a friendly message and return False if no tokens; otherwise True."""
    if load_tokens() is None:
        print("Not logged in. Run `uv run tomo login` first.")
        return False
    return True
