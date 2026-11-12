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
