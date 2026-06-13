from __future__ import annotations

from tomo.control_approval_store import ControlApprovalStore, get_control_approval_store

from .models import ControlApproval, ControlApprovalResolution


class ApprovalAdapter:
    def __init__(self, store: ControlApprovalStore | None = None) -> None:
        self.store = store or get_control_approval_store()

    def list_pending(self) -> list[ControlApproval]:
        return [
            ControlApproval(
                id=record.id,
                operation=record.operation,
                target=record.target,
                reason=record.reason,
                channel_id=record.channel_id,
            )
            for record in self.store.list_pending()
        ]

    def resolve(self, approval_id: str, approved: bool) -> ControlApprovalResolution:
        if not self.store.resolve(approval_id, approved):
            raise KeyError("Pending approval not found.")
        return ControlApprovalResolution(ok=True, id=approval_id, approved=approved)
