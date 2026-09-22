"""P1-7B employee vertical slice tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import database as database_module
from app.services.approval_gateway import ApprovalGateway
from app.services.approval_resume_service import ApprovalResumeService
from app.services.connectors.pinterest_connector import PinterestConnector
from app.services.connectors.registry import ConnectorRegistry
from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.mission_execution_service import MissionExecutionService
from app.services.p1_7_contracts import sanitize_payload
from app.services.stores.onboarding_workflow_store import OnboardingWorkflowStore
from app.services.stores.platform_connection_store import PlatformConnectionStore


@pytest.fixture(autouse=True)
def _disable_supabase(monkeypatch):
    monkeypatch.setattr(database_module, "supabase_client", None, raising=False)
    monkeypatch.setattr(
        database_module, "is_supabase_configured", lambda: False, raising=False
    )


def _slice(owner: str = "user-a") -> EmployeeVerticalSlice:
    connection_store = PlatformConnectionStore(client=None)
    connector_registry = ConnectorRegistry()
    connector_registry.register(PinterestConnector(
        workflow_store=OnboardingWorkflowStore(durable_required=False),
        connection_store=connection_store,
    ))
    return EmployeeVerticalSlice(
        owner,
        connector_registry=connector_registry,
        connection_store=connection_store,
        execution_service=MissionExecutionService(client=None),
        approval_gateway=ApprovalGateway(client=None),
    )


def test_pinterest_status_is_real_read_and_reports_not_connected():
    employee = _slice()
    result = employee.run("Check my Pinterest account status", mission_id="mission-status")

    assert result["success"] is False
    assert result["status"] == "WAIT_FOR_APPROVAL"
    report = result["report"]
    assert report["results"][0]["status"] == "not_started"
    assert report["discovered_capabilities"]["tools"]
    assert report["execution_id"]
    approval_id = report["resume_information"]["approval_request_id"]
    assert approval_id
    approval = employee._approvals.get_request(approval_id)
    assert approval["action_type"] == "start_platform_onboarding"


def test_onboarding_creates_durable_approval_and_resumes_to_human_checkpoint():
    employee = _slice()
    pending = employee.run("Connect my Pinterest account", mission_id="mission-connect")

    assert pending["success"] is False
    assert pending["status"] == "WAIT_FOR_APPROVAL"
    approval_id = pending["report"]["resume_information"]["approval_request_id"]
    assert approval_id

    approved = employee._approvals.approve_request(approval_id, approved_by="user-a")
    assert approved["success"] is True
    resumed = employee.resume_approval(approval_id)
    assert resumed["status"] == "awaiting_human_intervention"
    assert resumed["result"]["platform"] == "pinterest"

    execution_id = pending["report"]["resume_information"]["execution_id"]
    execution = employee._execution.get_execution(execution_id, owner_id="user-a")
    assert execution["status"] == "WAITING_INPUT"
    assert employee._execution.get_execution(execution_id, owner_id="user-b") is None


def test_capabilities_are_owner_scoped_and_connection_status_is_not_faked():
    employee = _slice("user-a")
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    discovered = employee.discover_capabilities()
    names = {tool["tool_name"] for tool in discovered["tools"]}
    assert "pinterest.get_account_status" in names

    other = _slice("user-b")
    other_names = {tool["tool_name"] for tool in other.discover_capabilities()["tools"]}
    assert "pinterest.get_account_status" in other_names
    assert employee._connections.get("user-b", "pinterest") is None


def test_secret_sanitization_is_recursive_and_report_safe():
    value = sanitize_payload({"nested": {"access_token": "secret", "ok": True}, "password": "pw"})
    assert value == {"nested": {"ok": True}}
    result = _slice().run("Check my Pinterest status", metadata={"content_inputs": {"api_key": "secret"}})
    assert "secret" not in str(result["report"])


def test_unknown_tool_cannot_enter_the_plan():
    employee = _slice()
    validation = employee._validator.validate([{"tool_name": "arbitrary_function", "input": {}}], discovered=employee.discover_capabilities())
    assert validation["success"] is False
    assert "unknown tool" in validation["errors"][0]


def test_orchestrator_requires_an_authenticated_owner_for_objective_execution():
    from app.services.agent_orchestrator import AgentOrchestrator

    result = AgentOrchestrator().run_objective("Check Pinterest")
    assert result == {"success": False, "status": "FAIL", "error": "Authenticated owner is required"}


def test_pinterest_not_connected_dynamically_triggers_onboarding_approval():
    employee = _slice()
    result = employee.run(
        "Start affiliate marketing on pinterest | Country: US | Language: en | Niche: AI tools | Daily limit: 30 pins",
        mission_id="mission-aff-not-started",
        metadata={"platform": "pinterest", "vertical": "affiliate", "content_inputs": {"platform": "pinterest", "vertical": "affiliate"}},
    )

    assert result["success"] is False
    assert result["status"] == "WAIT_FOR_APPROVAL"
    report = result["report"]
    assert report["results"][0]["status"] == "not_started"
    assert "start_platform_onboarding" in [s["tool_name"] for s in report["plan"]]
    assert len([s for s in report["plan"] if s["tool_name"] == "start_platform_onboarding"]) == 1

    approval_id = report["resume_information"]["approval_request_id"]
    assert approval_id
    approval = employee._approvals.get_request(approval_id)
    assert approval is not None
    assert approval["action_type"] == "start_platform_onboarding"


def test_pinterest_needs_reconnect_dynamically_triggers_onboarding_approval():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="needs_reconnect", scopes=[])
    result = employee.run(
        "Start affiliate marketing on pinterest | Country: US | Language: en | Niche: AI tools | Daily limit: 30 pins",
        mission_id="mission-aff-reconnect",
        metadata={"platform": "pinterest", "vertical": "affiliate", "content_inputs": {"platform": "pinterest", "vertical": "affiliate"}},
    )

    assert result["success"] is False
    assert result["status"] == "WAIT_FOR_APPROVAL"
    report = result["report"]
    assert report["results"][0]["status"] == "needs_reconnect"
    assert "start_platform_onboarding" in [s["tool_name"] for s in report["plan"]]

    approval_id = report["resume_information"]["approval_request_id"]
    assert approval_id
    approval = employee._approvals.get_request(approval_id)
    assert approval["action_type"] == "start_platform_onboarding"


def test_pinterest_already_connected_does_not_trigger_onboarding():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    result = employee.run(
        "Nataka kuanza affiliate marketing kwenye Pinterest",
        mission_id="mission-aff-connected",
        metadata={"platform": "pinterest", "vertical": "affiliate", "content_inputs": {"platform": "pinterest", "vertical": "affiliate"}},
    )

    assert result["success"] is False
    assert result["status"] == "WAIT_FOR_HUMAN_INPUT"
    report = result["report"]
    assert report["selected_tools"] == ["pinterest.get_account_status"]
    assert report["results"][0]["status"] == "connected"
    assert report["final_status"] == "WAIT_FOR_HUMAN_INPUT"
    assert report["required_user_action"]
    assert "affiliate niche" in report["required_user_action"]
    assert report["execution_id"]
    assert not any(s["tool_name"] == "start_platform_onboarding" for s in report["plan"])
    assert not any(a["action_type"] == "start_platform_onboarding" for a in employee._approvals.list_requests())


def test_dynamic_onboarding_step_is_not_appended_repeatedly():
    employee = _slice()
    result = employee.run(
        "Start affiliate marketing on pinterest | Country: US | Language: en | Niche: AI tools | Daily limit: 30 pins",
        mission_id="mission-aff-idempotent",
        metadata={"platform": "pinterest", "vertical": "affiliate", "content_inputs": {"platform": "pinterest", "vertical": "affiliate"}},
    )

    assert result["status"] == "WAIT_FOR_APPROVAL"
    report = result["report"]
    executed = [s["tool_name"] for s in report["executed_actions"]]
    assert executed.count("start_platform_onboarding") == 1
    assert len([s for s in report["plan"] if s["tool_name"] == "start_platform_onboarding"]) == 1


def test_resume_after_approval_advances_to_human_checkpoint():
    employee = _slice()
    pending = employee.run(
        "Start affiliate marketing on pinterest | Country: US | Language: en | Niche: AI tools | Daily limit: 30 pins",
        mission_id="mission-aff-resume",
        metadata={"platform": "pinterest", "vertical": "affiliate", "content_inputs": {"platform": "pinterest", "vertical": "affiliate"}},
    )

    assert pending["status"] == "WAIT_FOR_APPROVAL"
    approval_id = pending["report"]["resume_information"]["approval_request_id"]

    approved = employee._approvals.approve_request(approval_id, approved_by="user-a")
    assert approved["success"] is True
    resumed = employee.resume_approval(approval_id)
    assert resumed["status"] == "awaiting_human_intervention"
    assert resumed["result"]["platform"] == "pinterest"

    execution_id = pending["report"]["resume_information"]["execution_id"]
    execution = employee._execution.get_execution(execution_id, owner_id="user-a")
    assert execution["status"] == "WAITING_INPUT"


def test_publish_content_contract_registered():
    employee = _slice()
    tool = employee._tools.get_tool("publish_content")
    assert tool["success"]
    tool = tool["tool"]
    assert tool["tool_name"] == "publish_content"
    assert tool["capability"] == "publish_content"
    assert tool["platform"] == "pinterest"
    assert tool["operation"] == "publish_content"
    assert tool["risk_level"] == "WRITE_EXTERNAL"
    assert tool["requires_approval"] is True
    assert tool["requires_connection"] is True
    assert tool["supports_dry_run"] is False
    assert tool["idempotency_behavior"] == "approval_and_operation_idempotent"
    assert tool["input_schema"]["required"] == ["board_name", "pin_text", "link_url"]


def test_publish_content_plan_created_when_intent_and_inputs_complete():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    result = employee.run(
        "Publish my pin",
        mission_id="mission-pub",
        metadata={"platform": "pinterest", "content_inputs": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"}},
    )
    assert result["success"] is False
    assert result["status"] == "WAIT_FOR_APPROVAL"
    plan_tools = [s["tool_name"] for s in result["report"]["plan"]]
    assert "publish_content" in plan_tools
    approval_id = result["report"]["resume_information"]["approval_request_id"]
    approval = employee._approvals.get_request(approval_id)
    assert approval["action_type"] == "publish_content"
    assert approval["owner_id"] == "user-a"
    assert approval["payload"]["operation_key"] == f"p1-7d:mission-pub:{result['report']['execution_id']}:publish_content"


def test_publish_content_not_planned_for_broad_affiliate_goal():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    result = employee.run(
        "Start affiliate marketing on pinterest | Country: US | Language: en | Niche: AI tools | Daily limit: 30 pins",
        mission_id="mission-aff-pub",
        metadata={"platform": "pinterest", "vertical": "affiliate", "content_inputs": {"platform": "pinterest", "vertical": "affiliate"}},
    )
    plan_tools = [s["tool_name"] for s in result["report"]["plan"]]
    assert "publish_content" not in plan_tools


def test_publish_content_not_planned_when_inputs_missing():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    result = employee.run(
        "Publish my pin",
        mission_id="mission-missing",
        metadata={"platform": "pinterest", "content_inputs": {"board_name": "B", "pin_text": "T"}},
    )
    plan_tools = [s["tool_name"] for s in result["report"]["plan"]]
    assert "publish_content" not in plan_tools


def test_publish_content_not_planned_when_disconnected():
    employee = _slice()
    result = employee.run(
        "Publish my pin",
        mission_id="mission-disconnected",
        metadata={"platform": "pinterest", "content_inputs": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"}},
    )
    plan_tools = [s["tool_name"] for s in result["report"]["plan"]]
    assert "publish_content" not in plan_tools


def test_employee_publish_creates_approval():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    result = employee.run(
        "Publish my pin",
        mission_id="mission-approval",
        metadata={"platform": "pinterest", "content_inputs": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"}},
    )
    assert result["status"] == "WAIT_FOR_APPROVAL"
    approval_id = result["report"]["resume_information"]["approval_request_id"]
    approval = employee._approvals.get_request(approval_id)
    assert approval["action_type"] == "publish_content"
    assert approval["owner_id"] == "user-a"
    assert approval["payload"]["operation_key"].startswith("p1-7d:")


def test_publish_content_resume():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    pending = employee.run(
        "Publish my pin",
        mission_id="mission-publish-resume",
        metadata={"platform": "pinterest", "content_inputs": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"}},
    )
    assert pending["status"] == "WAIT_FOR_APPROVAL"
    approval_id = pending["report"]["resume_information"]["approval_request_id"]
    employee._approvals.approve_request(approval_id, approved_by="user-a")
    resumed = employee.resume_approval(approval_id)
    assert resumed["status"] == "resumed"
    assert resumed["result"]["action_type"] == "publish_content"
    assert resumed["result"]["pin_id"]
    connector = employee._connectors.get("pinterest")
    durable = connector._pin_store.get_by_operation_key("user-a", resumed["result"]["operation_key"])
    assert durable is not None
    assert durable["status"] == "published"


def test_publish_content_idempotency_duplicate():
    employee = _slice()
    employee._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    pending = employee.run(
        "Publish my pin",
        mission_id="mission-idempotent",
        metadata={"platform": "pinterest", "content_inputs": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"}},
    )
    approval_id = pending["report"]["resume_information"]["approval_request_id"]
    employee._approvals.approve_request(approval_id, approved_by="user-a")
    resumed = employee.resume_approval(approval_id)
    assert resumed["status"] == "resumed"
    resume_service = ApprovalResumeService(
        approval_gateway=employee._approvals,
        connector_registry=employee._connectors,
    )
    second = resume_service.resume(approval_id, current_user_id="user-a")
    assert second["status"] == "completed"
    assert second.get("note") == "pin_already_published"


def test_publish_content_cross_user_cannot_resume():
    employee_a = _slice()
    employee_a._connections.upsert("user-a", "pinterest", status="connected", scopes=[])
    pending = employee_a.run(
        "Publish my pin",
        mission_id="mission-cross",
        metadata={"platform": "pinterest", "content_inputs": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"}},
    )
    approval_id = pending["report"]["resume_information"]["approval_request_id"]
    employee_a._approvals.approve_request(approval_id, approved_by="user-a")
    result = ApprovalResumeService(
        approval_gateway=employee_a._approvals,
        connector_registry=employee_a._connectors,
    ).resume(approval_id, current_user_id="user-b")
    assert result["status"] == "approval_unauthorized"
    assert "authenticated user" in result["error"].lower()
