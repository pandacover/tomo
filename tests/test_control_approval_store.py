from __future__ import annotations

import threading
import time

from tomo.control_approval_store import ControlApprovalStore
from tomo.tools import ApprovalRequest


def test_store_create_list_and_resolve(tmp_path):
    store = ControlApprovalStore(storage_path=tmp_path / "control_approvals.json")
    approval_id = store.create(
        "desktop:local",
        ApprovalRequest(operation="write_file", target="MEMORY.md", reason="Append memory"),
    )

    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0].id == approval_id

    assert store.resolve(approval_id, True) is True
    assert store.get_resolution(approval_id) is True
    assert store.list_pending() == []


def test_wait_for_resolution_unblocks_from_another_thread(tmp_path):
    store = ControlApprovalStore(storage_path=tmp_path / "control_approvals.json")
    approval_id = store.create(
        "desktop:local",
        ApprovalRequest(operation="terminal", target="echo hi", reason="Test"),
    )
    result: list[bool] = []

    def waiter() -> None:
        resolved = store.wait_for_resolution(approval_id, timeout_seconds=2.0)
        result.append(bool(resolved))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.2)
    store.resolve(approval_id, False)
    thread.join(timeout=3)
    assert result == [False]