from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.agent_orchestrator import AgentOrchestrator


class MissionStub:
    def get_worker_missions(self, worker_id: str, limit: int = 50):
        return [
            {"id": "mission-ok", "status": "pending"},
            {"id": "mission-fail", "status": "pending"},
            {"id": "mission-done", "status": "completed"},
        ]


class RuntimeStub:
    def execute_mission(self, mission_id: str):
        return {"success": mission_id == "mission-ok", "mission_id": mission_id}


def make_orchestrator():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._mission_engine = MissionStub()
    orchestrator._worker_runtime = RuntimeStub()
    return orchestrator


def test_worker_batch_reports_partial_when_any_mission_fails():
    result = make_orchestrator().run_worker("worker-1")

    assert result["success"] is False
    assert result["status"] == "partial"
    assert result["pending_count"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert len(result["results"]) == 2


def test_worker_batch_reports_completed_when_all_pending_missions_succeed():
    orchestrator = make_orchestrator()
    orchestrator._mission_engine.get_worker_missions = lambda worker_id, limit=50: [
        {"id": "mission-ok", "status": "pending"}
    ]

    result = orchestrator.run_worker("worker-1")

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["succeeded"] == 1
    assert result["failed"] == 0


def test_worker_batch_with_no_pending_missions_is_completed():
    orchestrator = make_orchestrator()
    orchestrator._mission_engine.get_worker_missions = lambda worker_id, limit=50: [
        {"id": "mission-done", "status": "completed"}
    ]

    result = orchestrator.run_worker("worker-1")

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["pending_count"] == 0
    assert result["succeeded"] == 0
    assert result["failed"] == 0
