from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.main import app
from app.services.mission_orchestration import MissionOrchestrationService


class FakeEmployee:
    def __init__(self, result=None, delay: threading.Event | None = None) -> None:
        self.result = result or {"success": True, "status": "COMPLETE", "report": {"completed": True}}
        self.delay = delay
        self.calls = 0
        self.lock = threading.Lock()

    def run(self, objective, *, mission_id=None, metadata=None):
        with self.lock:
            self.calls += 1
        if self.delay:
            self.delay.wait(timeout=2)
        return dict(self.result)


def service(owner: str, employee: FakeEmployee | None = None) -> MissionOrchestrationService:
    return MissionOrchestrationService(owner, employee=employee)


def test_objective_creates_affiliate_mission_and_duplicate_submission_is_idempotent():
    orchestration = service("p18-owner-a")
    first = orchestration.create_mission("Help me start affiliate marketing on Pinterest", idempotency_key="objective-1")
    second = orchestration.create_mission("Help me start affiliate marketing on Pinterest", idempotency_key="objective-1")
    assert first["success"] and first["mission"]["domain"] == "affiliate_jobs"
    assert first["mission"]["objective"] == "Help me start affiliate marketing on Pinterest"
    assert second["idempotent"] is True
    assert second["mission"]["id"] == first["mission"]["id"]


def test_explicit_state_transitions_reject_invalid_edges():
    orchestration = service("p18-owner-b")
    mission = orchestration.create_mission("Prepare Pinterest profile")["mission"]
    assert orchestration.transition(mission["id"], "completed")["success"] is False
    assert orchestration.pause(mission["id"])["success"] is True
    assert orchestration.resume(mission["id"])["success"] is True
    assert orchestration.cancel(mission["id"])["success"] is True
    assert orchestration.resume(mission["id"])["success"] is False


def test_mission_payload_omits_unsupported_error_field_and_keeps_error_in_result():
    orchestration = MissionOrchestrationService("owner-1")

    created = orchestration.create_mission("Create a campaign")
    assert created["success"] is True
    mission = created["mission"]
    stored = orchestration._memory_missions[mission["id"]]

    assert "error" not in mission
    assert "error" not in stored

    assert orchestration.transition(mission["id"], "active")["success"] is True
    failed = orchestration.transition(
        mission["id"],
        "failed",
        result={"success": False, "status": "FAIL"},
        error="Mission failed",
    )
    assert failed["success"] is True

    persisted = orchestration.get_mission(mission["id"])
    assert "error" not in persisted
    assert persisted["result"]["error"] == "Mission failed"


def test_priority_reason_is_deterministic_and_explains_selection():
    orchestration = service("p18-owner-c")
    low = orchestration.create_mission("Low content task", priority="low")["mission"]
    urgent = orchestration.create_mission("Urgent affiliate task", priority="urgent", urgency="critical", business_importance=5)["mission"]
    selected = orchestration.select_next()
    assert selected["mission"]["id"] == urgent["id"]
    assert selected["reason"]["priority_score"] > orchestration.priority_reason(low)["priority_score"]
    assert selected["reason"]["blocking_reason"] is None


def test_dependencies_and_schedule_block_until_ready():
    orchestration = service("p18-owner-d")
    dependency = orchestration.create_mission("Connect Pinterest account")["mission"]
    dependent = orchestration.create_mission("Prepare affiliate profile", dependencies=[dependency["id"]])["mission"]
    assert orchestration.readiness(dependent)["reason"] == "dependencies"
    orchestration.transition(dependency["id"], "active")
    orchestration.transition(dependency["id"], "completed", result={"verified": True})
    assert orchestration.readiness(dependent)["ready"] is True
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    scheduled = orchestration.create_mission("Prepare tomorrow's content", scheduled_at=future)["mission"]
    assert orchestration.readiness(scheduled)["reason"] == "scheduled_for_later"
    assert orchestration.select_next()["mission"]["id"] == dependent["id"]


def test_scheduler_restart_keeps_durable_ready_state_in_process_store():
    owner = "p18-owner-e"
    first = service(owner)
    mission = first.create_mission("Find next affiliate task", scheduled_at=None)["mission"]
    restarted = service(owner)
    assert restarted.get_mission(mission["id"])["status"] == "pending"
    assert restarted.select_next()["mission"]["id"] == mission["id"]


def test_run_delegates_to_existing_employee_and_maps_waiting_states():
    waiting = FakeEmployee({"success": False, "status": "WAIT_FOR_APPROVAL", "report": {"approval": True}})
    orchestration = service("p18-owner-f", waiting)
    mission = orchestration.create_mission("Connect Pinterest account")["mission"]
    result = orchestration.run_mission(mission["id"])
    assert result["execution"]["status"] == "WAIT_FOR_APPROVAL"
    assert result["mission"]["status"] == "waiting_approval"


def test_two_workers_only_one_enters_employee_runtime():
    owner = "p18-owner-g"
    employee = FakeEmployee()
    first = service(owner, employee)
    second = service(owner, employee)
    mission = first.create_mission("Prepare Pinterest content")["mission"]
    results = []
    barrier = threading.Barrier(2)

    original_claim = first._claim_mission
    def synchronized_claim(record, token):
        barrier.wait(timeout=2)
        return original_claim(record, token)
    first._claim_mission = synchronized_claim
    second._claim_mission = synchronized_claim

    def run(orchestration):
        results.append(orchestration.run_mission(mission["id"]))

    threads = [threading.Thread(target=run, args=(first,)), threading.Thread(target=run, args=(second,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert employee.calls == 1
    assert sum(result.get("success") is True for result in results) == 1
    assert any(result.get("status") in {"RETRY", "COMPLETED"} for result in results)


def test_report_events_are_operational_and_secret_free():
    orchestration = service("p18-owner-h")
    mission = orchestration.create_mission("Set up affiliate workflow", metadata={"api_key": "secret"})["mission"]
    orchestration.pause(mission["id"])
    report = orchestration.report()
    assert report["waiting"]
    assert any(event["event_type"] == "mission_created" for event in report["events"])
    assert "secret" not in str(report)


def test_cross_user_mission_and_event_isolation():
    owner = service("p18-owner-i")
    other = service("p18-owner-j")
    mission = owner.create_mission("Private affiliate preference")["mission"]
    assert other.get_mission(mission["id"]) is None
    assert other.list_missions() == []
    assert other.events(mission_id=mission["id"]) == []


def test_authenticated_employee_api_ignores_forged_owner_and_supports_report():
    async def fake_user_id():
        return "api-owner"

    async def fake_client():
        return None

    app.dependency_overrides[get_current_user_id] = fake_user_id
    app.dependency_overrides[get_user_scoped_client] = fake_client
    try:
        client = TestClient(app)
        created = client.post("/employee/objective", json={"objective": "Start Pinterest affiliate workflow", "idempotency_key": "api-1", "owner_id": "forged"})
        assert created.status_code == 422
        created = client.post("/employee/objective", json={"objective": "Start Pinterest affiliate workflow", "idempotency_key": "api-1"})
        assert created.status_code == 201
        listed = client.get("/employee/missions")
        assert listed.status_code == 200
        assert listed.json()["missions"][0]["owner_id"] == "api-owner"
        report = client.get("/employee/report")
        assert report.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_user_scoped_client, None)
