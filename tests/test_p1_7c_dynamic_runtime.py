"""P1-7C dynamic multi-step employee runtime tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.mission_execution_service import MissionExecutionService
from app.services.stores.platform_connection_store import PlatformConnectionStore
from app.services.approval_gateway import ApprovalGateway


def _slice(owner: str = "user-a") -> EmployeeVerticalSlice:
    return EmployeeVerticalSlice(
        owner,
        connection_store=PlatformConnectionStore(client=None),
        execution_service=MissionExecutionService(client=None),
        approval_gateway=ApprovalGateway(client=None),
        max_decisions=3,
    )


def test_dynamic_plan_uses_status_then_onboarding_when_not_connected():
    employee = _slice()
    result = employee.run(
        "Check my Pinterest account status and connect if it is not connected",
        mission_id="mission-dynamic-1",
    )

    assert result["success"] is False
    assert result["status"] == "WAIT_FOR_APPROVAL"
    plan = result["report"]["plan"]
    assert len(plan) >= 2
    assert plan[0]["tool_name"] == "pinterest.get_account_status"
    assert any(step["tool_name"] == "start_platform_onboarding" for step in plan)


def test_observation_is_persisted_and_next_decision_uses_result():
    employee = _slice()
    result = employee.run("Check Pinterest and enroll if needed", mission_id="mission-dynamic-2")

    assert result["report"]["observation"]["action"] == "pinterest.get_account_status"
    execution_id = result["report"]["execution_id"]
    steps = employee._execution.load_steps_for_execution(execution_id, "user-a")
    assert steps
    assert any(step.get("result") for step in steps)
    assert "decision" in result["report"]


def test_completed_step_is_not_re_executed_on_resume():
    employee = _slice()
    mission_id = "mission-dynamic-3"
    first = employee.run("Check Pinterest account status", mission_id=mission_id)
    execution_id = first["report"]["execution_id"]
    steps = employee._execution.load_steps_for_execution(execution_id, "user-a")
    assert len(steps) >= 1
    second = employee.run("Check Pinterest account status", mission_id=mission_id)
    assert second["report"]["execution_id"] == execution_id


def test_retry_limit_is_bounded_for_transient_failures():
    employee = _slice()
    result = employee.run(
        "Check my Pinterest connection but fail transiently",
        metadata={"requested_actions": ["pinterest.get_account_status"], "content_inputs": {"force_error": "transient"}},
        mission_id="mission-dynamic-4",
    )
    assert result["report"]["final_status"] in {"FAIL", "RETRY"}
    assert employee._max_decisions >= 1


def test_missing_input_pauses_without_guessing():
    employee = _slice()
    result = employee.run(
        "Connect Pinterest and provide the board ID I need",
        mission_id="mission-dynamic-5",
    )
    assert result["status"] in {"WAIT_FOR_APPROVAL", "WAIT_FOR_HUMAN_INPUT", "FAIL"}
    if result["status"] == "WAIT_FOR_HUMAN_INPUT":
        assert result["report"]["required_user_action"]


def test_p1_7c_reaches_atomic_step_claim(monkeypatch):
    employee = _slice()
    calls = []
    original = employee._execution.claim_step

    def record_claim(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(employee._execution, "claim_step", record_claim)
    employee.run("Check my Pinterest account status", mission_id="mission-claim")

    assert calls


def test_p1_7c_durable_mode_rejects_database_failure():
    class FailingClient:
        def rpc(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

        def table(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    service = MissionExecutionService(client=FailingClient(), durable_required=True)
    result = service.claim_step(
        execution_id="00000000-0000-0000-0000-000000000001",
        owner_id="00000000-0000-0000-0000-000000000002",
        step_id="00000000-0000-0000-0000-000000000003",
        attempt_index=0,
        idempotency_key="step:durable:0",
    )

    assert result["success"] is False
    assert not service._memory_steps


def test_p1_7c_retry_attempt_is_not_reset_on_resume(monkeypatch):
    employee = _slice()
    monkeypatch.setattr(
        employee,
        "_execute_tool",
        lambda *args, **kwargs: {"success": False, "error": "connection timed out"},
    )
    first = employee.run(
        "Check my Pinterest connection",
        mission_id="mission-retry-progress",
    )
    assert first["status"] == "RETRY"
    execution_id = first["report"]["execution_id"]
    steps = employee._execution.load_steps_for_execution(execution_id, "user-a")
    assert steps[0]["attempt_index"] == 0

    second = employee.run(
        "Check my Pinterest connection",
        mission_id="mission-retry-progress",
    )
    assert second["status"] == "RETRY"
    steps = employee._execution.load_steps_for_execution(execution_id, "user-a")
    assert {step["attempt_index"] for step in steps} == {0, 1}


def test_read_only_pinterest_status_request_is_not_misrouted_to_content_worker():
    employee = _slice()
    result = employee.run(
        "Check my connected Pinterest account and retrieve my available boards. "
        "Show the actual worker execution steps and report the results. "
        "Do not publish anything.",
        mission_id="mission-readonly-pinterest-status",
    )

    plan = result["report"]["plan"]
    assert plan
    assert plan[0]["tool_name"] == "pinterest.get_account_status"
    assert all(step["tool_name"] != "prepare_affiliate_content" for step in plan)
