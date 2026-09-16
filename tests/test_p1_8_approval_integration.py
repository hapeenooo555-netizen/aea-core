from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.approvals import _update_p1_8_mission_state
from app.services.mission_orchestration import MissionOrchestrationService


def _waiting_approval_mission(owner_id: str) -> dict:
    orchestration = MissionOrchestrationService(owner_id)
    mission = orchestration.create_mission("Complete the affiliate workflow")["mission"]
    orchestration.transition(mission["id"], "active")
    orchestration.transition(mission["id"], "waiting_approval")
    return orchestration.get_mission(mission["id"])


def test_completed_approval_updates_p1_8_mission_to_completed():
    mission = _waiting_approval_mission("approval-owner-complete")

    _update_p1_8_mission_state(
        {"mission_id": mission["id"]},
        {"status": "completed"},
        "approval-owner-complete",
        None,
    )

    assert MissionOrchestrationService("approval-owner-complete").get_mission(mission["id"])["status"] == "completed"


def test_awaiting_human_approval_updates_p1_8_mission_to_waiting_human():
    mission = _waiting_approval_mission("approval-owner-human")

    _update_p1_8_mission_state(
        {"mission_id": mission["id"]},
        {"status": "awaiting_human_intervention"},
        "approval-owner-human",
        None,
    )

    assert MissionOrchestrationService("approval-owner-human").get_mission(mission["id"])["status"] == "waiting_human"


def test_rejected_approval_updates_p1_8_mission_to_failed():
    mission = _waiting_approval_mission("approval-owner-rejected")

    _update_p1_8_mission_state(
        {"mission_id": mission["id"]},
        {"status": "approval_rejected"},
        "approval-owner-rejected",
        None,
    )

    assert MissionOrchestrationService("approval-owner-rejected").get_mission(mission["id"])["status"] == "failed"


def test_missing_or_non_p1_8_mission_id_does_not_update():
    owner_id = "approval-owner-unsafe"
    mission = _waiting_approval_mission(owner_id)
    MissionOrchestrationService._memory_missions["legacy-mission"] = {
        "id": "legacy-mission",
        "owner_id": owner_id,
        "status": "waiting_approval",
    }

    _update_p1_8_mission_state(None, {"status": "completed"}, owner_id, None)
    _update_p1_8_mission_state(
        {"mission_id": "legacy-mission"},
        {"status": "completed"},
        owner_id,
        None,
    )

    assert MissionOrchestrationService(owner_id).get_mission(mission["id"])["status"] == "waiting_approval"
    assert MissionOrchestrationService(owner_id).get_mission("legacy-mission")["status"] == "waiting_approval"


def test_legacy_mission_with_objective_but_no_orchestration_key_is_not_updated():
    """A mission that has ``objective`` set but lacks the orchestration-specific
    ``orchestration_idempotency_key`` key must not be treated as P1-8."""
    owner_id = "approval-owner-legacy-obj"
    MissionOrchestrationService._memory_missions["legacy-with-objective"] = {
        "id": "legacy-with-objective",
        "owner_id": owner_id,
        "status": "waiting_approval",
        "objective": "looks like p1-8",
    }

    _update_p1_8_mission_state(
        {"mission_id": "legacy-with-objective"},
        {"status": "completed"},
        owner_id,
        None,
    )

    assert (
        MissionOrchestrationService(owner_id).get_mission("legacy-with-objective")["status"]
        == "waiting_approval"
    )
