from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers import approvals


def test_repeated_approval_returns_persisted_execution_without_replaying_connector(monkeypatch):
    owner_id = "approval-owner"
    approval_id = "approval-123"
    stored = {
        "id": approval_id,
        "owner_id": owner_id,
        "status": "approved",
        "payload": {
            "execution": {
                "status": "completed",
                "action_type": "publish_content",
                "platform": "pinterest",
                "result": {"pin_id": "pin-456"},
            }
        },
    }

    class Gateway:
        def get_request(self, _approval_id, client=None):
            return stored

        def approve_request(self, _approval_id, approved_by=None):
            assert approved_by == owner_id
            return {"success": True, "request": stored, "idempotent": True}

    resumed = {"called": False}

    def forbidden_resume(*_args, **_kwargs):
        resumed["called"] = True
        raise AssertionError("Completed connector action must not be replayed")

    monkeypatch.setattr(approvals, "_get_approval_gateway", lambda _client: Gateway())
    monkeypatch.setattr(approvals, "_resume_approval_for_owner", forbidden_resume)

    response = asyncio.run(
        approvals.approve_approval(
            approval_id,
            approvals.ApprovalApproveRequest(),
            current_user_id=owner_id,
            client=object(),
        )
    )

    assert response["success"] is True
    assert response["idempotent"] is True
    assert response["resume"]["result"]["pin_id"] == "pin-456"
    assert resumed["called"] is False
