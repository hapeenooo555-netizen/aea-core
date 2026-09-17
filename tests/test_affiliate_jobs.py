"""P1-9 Pinterest Affiliate Job tests.

These tests verify that the existing EmployeeVerticalSlice, Pinterest
connector, and ApprovalGateway machinery correctly handles affiliate
job metadata for Pinterest onboarding — without introducing new
abstractions, DB writes, or API endpoints.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.approval_gateway import ApprovalGateway
from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.mission_execution_service import MissionExecutionService
from app.services.p1_7_contracts import ObjectiveParser
from app.services.connectors.pinterest_connector import PinterestConnector
from app.services.connectors.registry import ConnectorRegistry
from app.services.stores.platform_connection_store import PlatformConnectionStore
from app.services.stores.onboarding_workflow_store import OnboardingWorkflowStore


def _in_memory_approval_gateway() -> ApprovalGateway:
    gate = ApprovalGateway(client=None)
    gate._is_configured = lambda: False
    gate._client = None
    return gate


def _slice(owner: str = "user-a") -> EmployeeVerticalSlice:
    shared_connections = PlatformConnectionStore(client=None)
    shared_workflows = OnboardingWorkflowStore(client=None)
    connector = PinterestConnector(
        workflow_store=shared_workflows,
        connection_store=shared_connections,
    )
    registry = ConnectorRegistry()
    registry.register(connector)
    return EmployeeVerticalSlice(
        owner,
        connector_registry=registry,
        connection_store=shared_connections,
        execution_service=MissionExecutionService(client=None),
        approval_gateway=_in_memory_approval_gateway(),
    )


AFFILIATE_METADATA = {
    "vertical": "affiliate",
    "platform": "pinterest",
    "country": "USA",
    "language": "en",
    "niche": "AI tools",
    "daily_limit": 30,
    "human_approval_required": True,
}


def test_affiliate_job_metadata():
    parser = ObjectiveParser()
    objective = parser.parse(
        "Start affiliate marketing",
        metadata=dict(AFFILIATE_METADATA, content_inputs={"existing_input": "kept"}),
    )

    assert objective.content_inputs["platform"] == "pinterest"
    assert objective.content_inputs["vertical"] == "affiliate"
    assert objective.content_inputs["country"] == "USA"
    assert objective.content_inputs["language"] == "en"
    assert objective.content_inputs["niche"] == "AI tools"
    assert objective.content_inputs["daily_limit"] == 30
    assert objective.content_inputs["human_approval_required"] is True
    assert objective.content_inputs["existing_input"] == "kept"
    assert "pinterest" in objective.platform_constraints

    employee = _slice()
    result = employee.run("Start affiliate marketing", metadata=AFFILIATE_METADATA, mission_id="mission-aff-meta")
    assert result["success"] is True
    assert result["status"] == "COMPLETE"
    assert "pinterest.get_account_status" in result["report"]["selected_tools"]


def test_affiliate_pinterest_plan_selection():
    employee = _slice()
    goal = "Onboard my affiliate marketing account for a new vertical"
    result = employee.run(goal, metadata=AFFILIATE_METADATA, mission_id="mission-aff-plan")

    assert result["success"] is False
    assert result["status"] == "WAIT_FOR_APPROVAL"
    plan = result["report"]["plan"]
    tools = [step["tool_name"] for step in plan]
    assert "pinterest.get_account_status" in tools
    assert "start_platform_onboarding" in tools

    first_step = plan[0]
    assert first_step["tool_name"] == "pinterest.get_account_status"
    assert first_step["input"]["platform"] == "pinterest"
    assert first_step["action_payload"]["platform"] == "pinterest"
    assert first_step["action_payload"]["country"] == "USA"
    assert first_step["action_payload"]["vertical"] == "affiliate"


def test_affiliate_pinterest_connected_path():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])

    result = employee.run("Check my Pinterest account status", mission_id="mission-aff-connected")

    assert result["success"] is True
    assert result["status"] == "COMPLETE"
    assert "pinterest.get_account_status" in result["report"]["selected_tools"]
    assert result["report"]["results"][0]["status"] == "connected"


def test_affiliate_pinterest_not_connected_requires_approval():
    employee = _slice()
    result = employee.run("Connect my Pinterest affiliate account", metadata=AFFILIATE_METADATA, mission_id="mission-aff-approval")

    assert result["success"] is False
    assert result["status"] == "WAIT_FOR_APPROVAL"
    report = result["report"]
    assert report["resume_information"]["approval_request_id"]
    assert report["resume_information"]["execution_id"]
    assert report["final_status"] == "WAIT_FOR_APPROVAL"
    assert report["required_user_action"] == "Approve the pending action"


def test_affiliate_pinterest_approval_resume():
    employee = _slice()
    first = employee.run(
        "Connect my Pinterest affiliate account",
        metadata=AFFILIATE_METADATA,
        mission_id="mission-aff-resume",
    )
    assert first["status"] == "WAIT_FOR_APPROVAL"
    approval_id = first["report"]["resume_information"]["approval_request_id"]

    approved = employee._approvals.approve_request(approval_id, approved_by="user-a")
    assert approved["success"] is True

    resumed = employee.resume_approval(approval_id)
    assert resumed["status"] == "awaiting_human_intervention"
    assert resumed["result"]["platform"] == "pinterest"
    assert resumed["result"]["current_step"] == 1

    execution_id = first["report"]["resume_information"]["execution_id"]
    execution = employee._execution.get_execution(execution_id, owner_id="user-a")
    assert execution["status"] == "WAITING_INPUT"


def test_affiliate_pinterest_rejection():
    employee = _slice()
    first = employee.run(
        "Connect my Pinterest affiliate account",
        metadata=AFFILIATE_METADATA,
        mission_id="mission-aff-reject",
    )
    assert first["status"] == "WAIT_FOR_APPROVAL"
    approval_id = first["report"]["resume_information"]["approval_request_id"]

    rejected = employee._approvals.reject_request(approval_id, reason="Not authorized", rejected_by="user-a")
    assert rejected["success"] is True

    resumed = employee.resume_approval(approval_id)
    assert resumed["success"] is False
    assert resumed["status"] == "approval_rejected"


def test_affiliate_pinterest_owner_isolation():
    owner_a = _slice("user-a")
    owner_b = _slice("user-b")

    first = owner_a.run(
        "Connect my Pinterest affiliate account",
        metadata=AFFILIATE_METADATA,
        mission_id="mission-aff-owner",
    )
    assert first["status"] == "WAIT_FOR_APPROVAL"
    approval_id = first["report"]["resume_information"]["approval_request_id"]

    cross_resume = owner_b.resume_approval(approval_id)
    assert cross_resume["success"] is False
    assert cross_resume["status"] == "FAIL"
    assert "not found" in cross_resume["error"]

    cross_approve = owner_b._approvals.approve_request(approval_id, approved_by="user-b")
    assert cross_approve["success"] is False
