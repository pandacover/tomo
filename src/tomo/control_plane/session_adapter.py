from __future__ import annotations

from tomo.session_store import list_sessions

from .models import ControlSession


class SessionAdapter:
    def list(self) -> list[ControlSession]:
        return [
            ControlSession(
                id=session.metadata.id,
                name=session.metadata.name,
                created_date=session.metadata.created_date,
                updated_date=session.metadata.updated_date,
                message_count=len(session.messages),
            )
            for session in list_sessions()
        ]
