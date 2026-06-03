from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


SESSIONS_DIR = Path("sessions")


@dataclass
class SessionMetadata:
    name: str
    created_date: str
    updated_date: str
    id: str


@dataclass
class ChatSession:
    metadata: SessionMetadata
    messages: list[dict[str, str]] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create_session(name: str | None = None) -> ChatSession:
    session_id = str(uuid4())
    timestamp = now_iso()
    return ChatSession(
        metadata=SessionMetadata(
            name=name or "New Chat",
            created_date=timestamp,
            updated_date=timestamp,
            id=session_id,
        ),
        messages=[],
    )


def session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def save_session(session: ChatSession) -> None:
    session.metadata.updated_date = now_iso()
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "metadata": asdict(session.metadata),
        "messages": [_message_to_json(message) for message in session.messages],
    }
    session.messages = data["messages"]
    session_path(session.metadata.id).write_text(json.dumps(data, indent=2))


def load_session(session_id: str) -> ChatSession:
    return _parse_session(json.loads(session_path(session_id).read_text()))


def list_sessions() -> list[ChatSession]:
    if not SESSIONS_DIR.exists():
        return []
    sessions: list[ChatSession] = []
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            sessions.append(_parse_session(json.loads(path.read_text())))
        except (OSError, TypeError, ValueError, KeyError):
            continue
    return sorted(sessions, key=lambda session: session.metadata.updated_date, reverse=True)


def _parse_session(data: dict[str, object]) -> ChatSession:
    metadata = data["metadata"]
    messages = data.get("messages", [])
    if not isinstance(metadata, dict):
        raise ValueError("Session metadata must be an object")
    if not isinstance(messages, list):
        raise ValueError("Session messages must be a list")
    return ChatSession(
        metadata=SessionMetadata(
            name=str(metadata["name"]),
            created_date=str(metadata["created_date"]),
            updated_date=str(metadata["updated_date"]),
            id=str(metadata["id"]),
        ),
        messages=[_parse_message(message) for message in messages],
    )


def _parse_message(message: object) -> dict[str, str]:
    if not isinstance(message, dict):
        raise ValueError("Session message must be an object")
    role = str(message["role"])
    content = str(message["content"])
    return {"role": role, "content": content}


def _message_to_json(message: object) -> dict[str, str]:
    if isinstance(message, dict):
        return {"role": str(message["role"]), "content": str(message["content"])}

    message_type = getattr(message, "type", None)
    role = "assistant" if message_type == "ai" else "user" if message_type == "human" else str(message_type or "assistant")
    content = getattr(message, "content", "")
    if isinstance(content, list):
        content = "".join(str(part) for part in content)
    return {"role": role, "content": str(content)}
