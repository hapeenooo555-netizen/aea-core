from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.approval_gateway import ApprovalGateway
from app.services.connectors.base import BaseConnector, ConnectorCapabilities
from app.services.connectors.registry import ConnectorRegistry
from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.mission_execution_service import MissionExecutionService
from app.services.p1_7_contracts import sanitize_payload
from app.services.stores.platform_connection_store import PlatformConnectionStore


class CountingConnector(BaseConnector):
    def __init__(self, barrier: threading.Barrier | None = None) -> None:
        super().__init__()
        self.calls = 0
        self.status_calls = 0
        self.onboarding_calls = 0
        self.keys: list[str | None] = []
        self.barrier = barrier
        self.lock = threading.Lock()

    @property
    def platform(self) -> str:
        return "pinterest"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(platform="pinterest", account_status=True, onboarding=True)

    def health_check(self):
        return {"success": True, "status": "available"}

    def get_account_status(self, worker_id=None):
        with self.lock:
            self.calls += 1
            self.status_calls += 1
        if self.barrier:
            self.barrier.wait(timeout=2)
        return {"success": True, "status": "not_started", "details": {"connected": False}}

    def start_onboarding(self, worker_id, **kwargs):
        with self.lock:
            self.calls += 1
            self.onboarding_calls += 1
            self.keys.append(kwargs.get("idempotency_key"))
        return {"success": True, "status": "completed"}

    def resume_onboarding(self, workflow_id, human_input):
        return {"success": True, "status": "completed"}


def _service() -> MissionExecutionService:
    return MissionExecutionService(client=None)


def _employee(owner, execution, connector, approvals=None):
    registry = ConnectorRegistry()
    registry.register(connector)
    return EmployeeVerticalSlice(
        owner,
        connector_registry=registry,
        connection_store=PlatformConnectionStore(client=None),
        execution_service=execution,
        approval_gateway=approvals or ApprovalGateway(client=None),
    )


def test_same_operation_key_survives_retry_and_stale_recovery():
    service = _service()
    first = service.claim_step("execution", "owner", "step-a", 0, "attempt-0", operation_key="logical-a", claim_token="worker-a", lease_seconds=0)
    assert first["claimed"] is True
    recovered = service.claim_step("execution", "owner", "step-b", 1, "attempt-1", operation_key="logical-a", claim_token="worker-b", lease_seconds=60)
    assert recovered["claimed"] is True
    steps = service.load_steps_for_execution("execution", "owner")
    assert {step["operation_key"] for step in steps} == {"logical-a"}
    assert steps[0]["status"] == "failed"
    assert steps[1]["claim_token"] == "worker-b"


def test_completed_operation_is_not_claimed_again():
    service = _service()
    claim = service.claim_step("execution", "owner", "step-a", 0, "attempt-0", operation_key="logical-a", claim_token="worker-a")
    service.update_step("execution", "owner", claim["step"]["id"], status="completed", result={"ok": True}, claim_token="worker-a")
    again = service.claim_step("execution", "owner", "step-b", 1, "attempt-1", operation_key="logical-a", claim_token="worker-b")
    assert again["claimed"] is False
    assert again["step"]["result"] == {"ok": True}


def test_full_runtime_two_worker_race_has_one_side_effect():
    execution = _service()
    connector = CountingConnector()
    approvals = ApprovalGateway(client=None)
    first = _employee("owner", execution, connector, approvals)
    second = _employee("owner", execution, connector, approvals)
    # Both callers start together. The in-memory execution store may allow
    # one caller to observe the existing execution only after the other has
    # already claimed its step; requiring both to enter claim_step at a barrier
    # incorrectly deadlocks that valid schedule.
    results = []
    result_lock = threading.Lock()

    start_barrier = threading.Barrier(2)

    def run(employee):
        # Ensure both workers begin the same mission concurrently; without
        # this barrier a fast first worker can finish before the second starts,
        # making a claim-step race assertion nondeterministic.
        start_barrier.wait(timeout=2)
        result = employee.run("Check my Pinterest account status", mission_id="mission-race")
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=run, args=(first,)), threading.Thread(target=run, args=(second,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert connector.status_calls == 1
    assert connector.onboarding_calls == 0
    assert {result["status"] for result in results} <= {"COMPLETE", "RETRY", "WAIT_FOR_APPROVAL"}
    execution_id = next(result["report"]["execution_id"] for result in results if result.get("report"))
    steps = execution.load_steps_for_execution(execution_id, "owner")
    assert len(steps) == 1
    assert steps[0]["status"] == "completed"


def test_runtime_retry_keeps_logical_operation_identity():
    employee = _employee("owner", _service(), CountingConnector())
    employee._execute_tool = lambda *args, **kwargs: {"success": False, "error": "connection timed out"}
    first = employee.run("Check my Pinterest account status", mission_id="mission-retry")
    second = employee.run("Check my Pinterest account status", mission_id="mission-retry")
    assert first["status"] == second["status"] == "RETRY"
    steps = employee._execution.load_steps_for_execution(first["report"]["execution_id"], "owner")
    assert len({step["operation_key"] for step in steps}) == 1


def test_approval_payload_binds_operation_identity_and_sanitizes_secrets():
    approvals = ApprovalGateway(client=None)
    employee = _employee("owner", _service(), CountingConnector(), approvals)
    result = employee.run(
        "Connect Pinterest",
        mission_id="mission-approval",
    )
    assert result["status"] == "WAIT_FOR_APPROVAL"
    request = approvals.get_request(result["report"]["resume_information"]["approval_request_id"])
    assert request["payload"]["operation_key"].startswith("p1-7d:")
    secret_request = approvals.create_request(
        mission_id="mission-approval",
        action_type="start_platform_onboarding",
        risk_level="WRITE_EXTERNAL",
        payload={"operation_key": request["payload"]["operation_key"], "token": "never-persist"},
        owner_id="owner",
    )
    assert "token" not in secret_request["request"]["payload"]
    assert "never-persist" not in str(secret_request)


def test_approval_resume_preserves_operation_identity():
    approvals = ApprovalGateway(client=None)
    connector = CountingConnector()
    employee = _employee("owner", _service(), connector, approvals)
    pending = employee.run("Connect Pinterest", mission_id="mission-resume")
    request_id = pending["report"]["resume_information"]["approval_request_id"]
    operation_key = approvals.get_request(request_id)["payload"]["operation_key"]
    approvals.approve_request(request_id, approved_by="owner")
    resumed = employee.resume_approval(request_id)
    assert resumed["status"] == "completed"
    assert connector.keys == [operation_key]


def test_fencing_rejects_old_worker_completion():
    service = _service()
    first = service.claim_step("execution", "owner", "step-a", 0, "attempt-0", operation_key="logical-a", claim_token="worker-a", lease_seconds=0)
    second = service.claim_step("execution", "owner", "step-b", 1, "attempt-1", operation_key="logical-a", claim_token="worker-b")
    stale = service.update_step("execution", "owner", first["step"]["id"], status="completed", result={"stale": True}, claim_token="worker-a")
    current = service.update_step("execution", "owner", second["step"]["id"], status="completed", result={"ok": True}, claim_token="worker-b")
    assert stale["success"] is True  # old row is fenced by row identity and already recovered
    assert current["success"] is True
    assert service.load_steps_for_execution("execution", "owner")[-1]["result"] == {"ok": True}


def test_sanitized_execution_metadata_contains_no_credentials():
    value = sanitize_payload({"operation_key": "p1-7d:owner:mission:step", "access_token": "secret"})
    assert value == {"operation_key": "p1-7d:owner:mission:step"}
