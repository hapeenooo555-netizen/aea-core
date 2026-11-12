"""Regression tests for post-approval verified mission learning."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.mission_orchestration import MissionOrchestrationService  # noqa: E402


def _mission(owner_id: str) -> dict:
    result = MissionOrchestrationService(owner_id).create_mission(
        "Publish approved Pinterest content",
        metadata={"platform": "pinterest"},
    )
    assert result["success"]
    return result["mission"]


def test_public_recorder_records_verified_post_approval_outcome(monkeypatch):
    owner_id = "experience-owner"
    mission = _mission(owner_id)
    service = MissionOrchestrationService(owner_id)
    calls = []
    monkeypatch.setattr(
        service,
        "_record_verified_experience",
        lambda stored_mission, result, status: calls.append(
            (stored_mission["id"], result, status)
        ),
    )

    accepted = service.record_verified_experience(
        mission["id"],
        {
            "pin_id": "pin-123",
            "verification": {
                "verified": True,
                "source": "pinterest_api",
                "reference": "pin-123",
                "verified_at": "2026-11-13T00:00:00+00:00",
            },
        },
    )

    assert accepted is True
    assert len(calls) == 1
    assert calls[0][0] == mission["id"]
    assert calls[0][2] == "COMPLETE"


def test_public_recorder_rejects_unknown_or_foreign_mission(monkeypatch):
    owner_id = "experience-owner-a"
    foreign = _mission("experience-owner-b")
    service = MissionOrchestrationService(owner_id)
    calls = []
    monkeypatch.setattr(
        service,
        "_record_verified_experience",
        lambda *args: calls.append(args),
    )

    assert service.record_verified_experience(foreign["id"], {"verification": {}}) is False
    assert service.record_verified_experience("missing-mission", {"verification": {}}) is False
    assert calls == []


def test_public_recorder_rejects_nonterminal_execution_status(monkeypatch):
    owner_id = "experience-owner-waiting"
    mission = _mission(owner_id)
    service = MissionOrchestrationService(owner_id)
    calls = []
    monkeypatch.setattr(
        service,
        "_record_verified_experience",
        lambda *args: calls.append(args),
    )

    assert (
        service.record_verified_experience(
            mission["id"],
            {"verification": {"verified": True}},
            "WAIT_FOR_APPROVAL",
        )
        is False
    )
    assert calls == []



def test_approval_resume_records_verified_pin_after_successful_publish(monkeypatch):
    from app.services import mission_orchestration
    from app.services.approval_resume_service import ApprovalResumeService

    calls = []
    scoped_client = object()

    class FakeConnector:
        platform = "pinterest"

        def publish_content(self, worker_id, content, **kwargs):
            assert worker_id == "worker-123"
            assert kwargs["owner_id"] == "owner-123"
            return {
                "success": True,
                "status": "published",
                "pin_id": "provider-pin-456",
                "published_at": "2026-11-13T08:00:00+00:00",
            }

    class FakeOrchestration:
        def __init__(self, owner_id, client=None):
            calls.append(("init", owner_id, client))

        def record_verified_experience(self, mission_id, result, status):
            calls.append(("record", mission_id, result, status))
            return True

    monkeypatch.setattr(
        mission_orchestration,
        "MissionOrchestrationService",
        FakeOrchestration,
    )

    service = ApprovalResumeService(mission_client=scoped_client)
    outcome = service._handle_publish_content(
        approval_request_id="approval-123",
        mission_id="mission-456",
        worker_id="worker-123",
        payload={"content": {"title": "Approved Pin"}},
        connector=FakeConnector(),
        current_user_id="owner-123",
    )

    assert outcome["status"] == "resumed"
    assert outcome["pin_id"] == "provider-pin-456"
    assert calls[0] == ("init", "owner-123", scoped_client)
    assert calls[1][0:2] == ("record", "mission-456")
    assert calls[1][2]["verification"] == {
        "verified": True,
        "source": "pinterest_api",
        "reference": "provider-pin-456",
        "verified_at": "2026-11-13T08:00:00+00:00",
    }
    assert calls[1][3] == "COMPLETE"
