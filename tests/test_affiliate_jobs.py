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
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.main import app
from app.routers import approvals as approvals_router
from app.routers.affiliate_jobs import _normalize_job
from app.services.approval_gateway import ApprovalGateway
from app.services.approval_resume_service import ApprovalResumeService
from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.mission_execution_service import MissionExecutionService
from app.services.mission_orchestration import MissionOrchestrationService
from app.services.p1_7_contracts import ObjectiveParser
from app.services.connectors.pinterest_connector import PinterestConnector
from app.services.connectors.registry import ConnectorRegistry
from app.services.stores.platform_connection_store import PlatformConnectionStore
from app.services.stores.onboarding_workflow_store import OnboardingWorkflowStore
from app.services.stores.pin_publish_store import PinPublishStore


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


def test_affiliate_job_end_to_end_lifecycle_uses_durable_publish_record(supabase_disabled):
    owner = "user-a"
    mission_service = MissionOrchestrationService(owner)
    mission = mission_service.create_mission(
        "Start affiliate marketing on pinterest | Country: USA | Language: en | Niche: AI tools | Daily limit: 30 pins",
        title="Affiliate: pinterest (AI tools)",
        metadata=AFFILIATE_METADATA,
    )["mission"]
    assert mission["owner_id"] == owner

    connection_store = PlatformConnectionStore(client=None)
    connection_store.upsert(owner_id="worker-123", platform="pinterest", status="connected")
    connector = PinterestConnector(
        connection_store=connection_store,
        pin_store=PinPublishStore(client=None),
    )
    registry = ConnectorRegistry()
    registry.register(connector)

    approval_gateway = ApprovalGateway()
    approval_request_id = approval_gateway.create_request(
        mission_id=mission["id"],
        action_type="publish_content",
        risk_level="sensitive",
        payload={
            "platform": "pinterest",
            "worker_id": "worker-123",
            "content": {
                "board_name": "My Board",
                "pin_text": "AI tools",
                "link_url": "https://example.com/product",
                "opportunity_id": "opp-123",
            },
        },
        owner_id=owner,
    )["request"]["id"]
    approval_gateway.approve_request(approval_request_id, approved_by=owner)

    result = ApprovalResumeService(
        approval_gateway=approval_gateway,
        connector_registry=registry,
    ).resume(approval_request_id, current_user_id=owner)

    assert result["status"] == "resumed"
    assert result["action_type"] == "publish_content"
    operation_key = result["operation_key"]
    durable_publish = connector._pin_store.get_by_operation_key(owner, operation_key)
    assert durable_publish is not None
    assert durable_publish["status"] == "published"
    assert durable_publish["link_url"].startswith("https://example.com/product")

    mission_row = dict(mission)
    mission_row["status"] = "completed"
    mission_row["result"] = {
        "action_type": "publish_content",
        "approval_request_id": approval_request_id,
        "operation_key": operation_key,
        "pin_id": durable_publish["pin_id"],
        "link_url": "https://example.com/stale",
        "status": "published",
    }

    job = _normalize_job(mission_row, owner_id=owner, client=None)
    assert job["status"] == "published"
    assert job["lifecycle_status"] == "published"
    assert job["approval_request_id"] == approval_request_id
    assert job["operation_key"] == operation_key
    assert job["pin_id"] == durable_publish["pin_id"]
    assert job["publish_link_url"] == durable_publish["link_url"]
    assert job["duplicate_safe"] is False


def test_affiliate_job_cross_user_isolation_for_lookup_and_durable_publish(supabase_disabled):
    owner_a = "user-a"
    owner_b = "user-b"
    gateway = ApprovalGateway()
    connection_store = PlatformConnectionStore(client=None)
    connection_store.upsert(owner_id="worker-123", platform="pinterest", status="connected")
    connector = PinterestConnector(
        connection_store=connection_store,
        pin_store=PinPublishStore(client=None),
    )
    registry = ConnectorRegistry()
    registry.register(connector)

    mission_service_a = MissionOrchestrationService(owner_a)
    mission = mission_service_a.create_mission(
        "Start affiliate marketing on pinterest | Country: USA | Language: en | Niche: AI tools | Daily limit: 30 pins",
        metadata=AFFILIATE_METADATA,
    )["mission"]
    approval_request_id = gateway.create_request(
        mission_id=mission["id"],
        action_type="publish_content",
        risk_level="sensitive",
        payload={
            "platform": "pinterest",
            "worker_id": "worker-123",
            "content": {
                "board_name": "My Board",
                "pin_text": "AI tools",
                "link_url": "https://example.com/product",
            },
        },
        owner_id=owner_a,
    )["request"]["id"]
    gateway.approve_request(approval_request_id, approved_by=owner_a)

    publish_result = ApprovalResumeService(
        approval_gateway=gateway,
        connector_registry=registry,
    ).resume(approval_request_id, current_user_id=owner_a)
    durable_publish = connector._pin_store.get_by_operation_key(owner_a, publish_result["operation_key"])
    assert durable_publish is not None

    assert MissionOrchestrationService(owner_b).get_mission(mission["id"]) is None

    owner_b_resume = ApprovalResumeService(
        approval_gateway=gateway,
        connector_registry=registry,
    ).resume(approval_request_id, current_user_id=owner_b)
    assert owner_b_resume["status"] in {"approval_unauthorized", "approval_not_found"}

    mission_row = dict(mission)
    mission_row["status"] = "completed"
    mission_row["result"] = {
        "action_type": "publish_content",
        "approval_request_id": approval_request_id,
        "operation_key": publish_result["operation_key"],
        "pin_id": durable_publish["pin_id"],
        "link_url": durable_publish["link_url"],
        "status": "published",
    }

    b_job = _normalize_job(mission_row, owner_id=owner_b, client=None)
    assert b_job["status"] == "completed"
    assert b_job["lifecycle_status"] == "completed"
    assert b_job["pin_id"] is None
    assert b_job["publish_link_url"] is None


def test_affiliate_job_status_maps_to_publish_lifecycle_and_publish_metadata():
    pin_store = PinPublishStore(client=None)
    pin_store._memory_store["p1-11:approval-123"] = {
        "id": "published-123",
        "owner_id": "user-a",
        "worker_id": "worker-123",
        "platform": "pinterest",
        "operation_key": "p1-11:approval-123",
        "approval_request_id": "approval-123",
        "board_name": "My Board",
        "pin_text": "AI tools",
        "link_url": "https://example.com/product?utm_source=aea",
        "pin_id": "pin-456",
        "status": "published",
        "content": {"opportunity_id": "opp-123"},
        "created_at": "2026-09-18T00:00:00+00:00",
        "updated_at": "2026-09-18T00:00:00+00:00",
    }

    job = _normalize_job(
        {
            "id": "job-publish-1",
            "title": "Affiliate Pinterest job",
            "objective": "Start affiliate marketing on pinterest",
            "status": "completed",
            "priority": "normal",
            "urgency": "normal",
            "business_importance": 2,
            "metadata": {
                "platform": "pinterest",
                "country": "USA",
                "language": "en",
                "niche": "AI tools",
                "daily_limit": 25,
                "human_approval_required": True,
            },
            "result": {
                "action_type": "publish_content",
                "approval_request_id": "approval-123",
                "operation_key": "p1-11:approval-123",
                "pin_id": "pin-456",
                "link_url": "https://example.com/product?utm_source=aea",
                "published_at": "2026-09-18T00:00:00+00:00",
                "status": "published",
            },
            "created_at": "2026-09-17T00:00:00+00:00",
            "updated_at": "2026-09-18T00:00:00+00:00",
        },
        owner_id="user-a",
        client=None,
    )

    assert job["status"] == "published"
    assert job["lifecycle_status"] == "published"
    assert job["approval_request_id"] == "approval-123"
    assert job["operation_key"] == "p1-11:approval-123"
    assert job["pin_id"] == "pin-456"
    assert job["publish_link_url"] == "https://example.com/product?utm_source=aea"
    assert job["duplicate_safe"] is False


def test_affiliate_job_duplicate_publish_is_marked_duplicate_safe_and_sensitive_fields_not_exposed():
    pin_store = PinPublishStore(client=None)
    pin_store._memory_store["p1-11:approval-999"] = {
        "id": "published-999",
        "owner_id": "user-a",
        "worker_id": "worker-999",
        "platform": "pinterest",
        "operation_key": "p1-11:approval-999",
        "approval_request_id": "approval-999",
        "board_name": "My Board",
        "pin_text": "AI tools",
        "link_url": "https://example.com/duplicate",
        "pin_id": "pin-111",
        "status": "published",
        "content": {"opportunity_id": "opp-999"},
        "created_at": "2026-09-18T00:00:00+00:00",
        "updated_at": "2026-09-18T00:00:00+00:00",
    }

    job = _normalize_job(
        {
            "id": "job-dupe-1",
            "title": "Affiliate Pinterest job",
            "objective": "Start affiliate marketing on pinterest",
            "status": "completed",
            "priority": "normal",
            "urgency": "normal",
            "business_importance": 2,
            "metadata": {
                "platform": "pinterest",
                "country": "USA",
                "language": "en",
                "niche": "AI tools",
                "daily_limit": 25,
                "human_approval_required": True,
            },
            "result": {
                "note": "pin_already_published",
                "status": "duplicate",
                "pin_id": "pin-111",
                "approval_request_id": "approval-999",
                "operation_key": "p1-11:approval-999",
                "link_url": "https://example.com/duplicate",
            },
            "created_at": "2026-09-17T00:00:00+00:00",
            "updated_at": "2026-09-18T00:00:00+00:00",
        },
        owner_id="user-a",
        client=None,
    )

    assert job["status"] == "published"
    assert job["duplicate_safe"] is True
    assert "access_token" not in job
    assert "oauth_code" not in job
    assert "client_secret" not in job
    assert job["pin_id"] == "pin-111"


def test_affiliate_job_requires_durable_publish_record_before_marking_published():
    job = _normalize_job(
        {
            "id": "job-no-durable-publish",
            "title": "Affiliate Pinterest job",
            "objective": "Start affiliate marketing on pinterest",
            "status": "completed",
            "priority": "normal",
            "urgency": "normal",
            "business_importance": 2,
            "metadata": {"platform": "pinterest"},
            "result": {
                "action_type": "publish_content",
                "approval_request_id": "approval-777",
                "operation_key": "p1-11:approval-777",
                "pin_id": "pin-777",
                "link_url": "https://example.com/never-published",
                "status": "published",
            },
            "created_at": "2026-09-17T00:00:00+00:00",
            "updated_at": "2026-09-18T00:00:00+00:00",
        },
        owner_id="user-a",
        client=None,
    )

    assert job["status"] == "completed"
    assert job["lifecycle_status"] == "completed"
    assert job["operation_key"] == "p1-11:approval-777"


def test_affiliate_job_uses_durable_published_record_when_present():
    pin_store = PinPublishStore(client=None)
    pin_store._memory_store["p1-11:approval-1001"] = {
        "id": "published-1001",
        "owner_id": "user-a",
        "worker_id": "worker-1001",
        "platform": "pinterest",
        "operation_key": "p1-11:approval-1001",
        "approval_request_id": "approval-1001",
        "board_name": "My Board",
        "pin_text": "AI tools",
        "link_url": "https://example.com/real-publish",
        "pin_id": "pin-1001",
        "status": "published",
        "content": {"opportunity_id": "opp-1001"},
        "created_at": "2026-09-17T00:00:00+00:00",
        "updated_at": "2026-09-18T00:00:00+00:00",
    }

    job = _normalize_job(
        {
            "id": "job-durable-publish",
            "title": "Affiliate Pinterest job",
            "objective": "Start affiliate marketing on pinterest",
            "status": "completed",
            "priority": "normal",
            "urgency": "normal",
            "business_importance": 2,
            "metadata": {"platform": "pinterest"},
            "result": {
                "action_type": "publish_content",
                "approval_request_id": "approval-1001",
                "operation_key": "p1-11:approval-1001",
                "pin_id": "older-pin",
                "link_url": "https://example.com/stale",
                "status": "published",
            },
            "created_at": "2026-09-17T00:00:00+00:00",
            "updated_at": "2026-09-18T00:00:00+00:00",
        },
        owner_id="user-a",
        client=None,
    )

    assert job["status"] == "published"
    assert job["lifecycle_status"] == "published"
    assert job["pin_id"] == "pin-1001"
    assert job["publish_link_url"] == "https://example.com/real-publish"


def test_affiliate_employee_api_e2e_approval_and_publish_requires_durable_record(supabase_disabled):
    owner = "user-a"
    other = "user-b"
    current_user = {"id": owner}
    client = TestClient(app)

    def _current_user_id() -> str:
        return current_user["id"]

    shared_gateway = ApprovalGateway(client=None)
    shared_connection_store = PlatformConnectionStore(client=None)
    shared_connector = PinterestConnector(
        connection_store=shared_connection_store,
        pin_store=PinPublishStore(client=None),
    )
    shared_registry = ConnectorRegistry()
    shared_registry.register(shared_connector)
    shared_resume_service = ApprovalResumeService(
        approval_gateway=shared_gateway,
        connector_registry=shared_registry,
    )
    original_get_approval_gateway = approvals_router._get_approval_gateway
    original_get_resume_service = approvals_router._get_resume_service
    approvals_router._get_approval_gateway = lambda client=None: shared_gateway
    approvals_router._get_resume_service = lambda current_user_id_arg, client=None: shared_resume_service

    app.dependency_overrides[get_current_user_id] = _current_user_id
    app.dependency_overrides[get_user_scoped_client] = lambda: None

    try:
        create_response = client.post(
            "/affiliate",
            json={
                "platform": "pinterest",
                "country": "USA",
                "language": "en",
                "niche": "AI tools",
                "daily_limit": 30,
                "human_approval_required": True,
                "title": "Affiliate Pinterest job",
            },
        )
        assert create_response.status_code == 201
        payload = create_response.json()
        mission_id = payload["mission_id"]
        assert payload["job"]["status"] == "created"
        assert payload["job"]["platform"] == "pinterest"
        assert "oauth_code" not in str(payload)

        fetch_response = client.get(f"/affiliate/{mission_id}")
        assert fetch_response.status_code == 200
        fetched = fetch_response.json()["job"]
        assert fetched["id"] == mission_id
        assert fetched["status"] == "created"
        assert "access_token" not in str(fetched)

        employee = EmployeeVerticalSlice(
            owner,
            connection_store=shared_connection_store,
            execution_service=MissionExecutionService(client=None),
            approval_gateway=shared_gateway,
        )
        pending = employee.run(
            "Connect my Pinterest affiliate account",
            mission_id=mission_id,
            metadata={
                "vertical": "affiliate",
                "platform": "pinterest",
                "country": "USA",
                "language": "en",
                "niche": "AI tools",
                "daily_limit": 30,
                "human_approval_required": True,
            },
        )
        assert pending["status"] == "WAIT_FOR_APPROVAL"
        approval_id = pending["report"]["resume_information"]["approval_request_id"]
        assert approval_id

        approve_response = client.post(f"/approvals/{approval_id}/approve", json={})
        assert approve_response.status_code == 200
        approval_body = approve_response.json()
        assert approval_body["success"] is True
        assert "oauth_code" not in str(approval_body)
        assert approval_body["resume"]["status"] in {"awaiting_human_intervention", "resumed", "completed"}

        connection_store = PlatformConnectionStore(client=None)
        connection_store.upsert(owner_id=owner, platform="pinterest", status="connected")
        connector = PinterestConnector(
            connection_store=connection_store,
            pin_store=PinPublishStore(client=None),
        )
        registry = ConnectorRegistry()
        registry.register(connector)

        publish_gateway = ApprovalGateway(client=None)
        publish_request = publish_gateway.create_request(
            mission_id=mission_id,
            action_type="publish_content",
            risk_level="sensitive",
            payload={
                "platform": "pinterest",
                "worker_id": owner,
                "content": {
                    "board_name": "My Board",
                    "pin_text": "AI tools",
                    "link_url": "https://example.com/product",
                    "opportunity_id": "opp-123",
                },
            },
            owner_id=owner,
        )
        assert publish_request["success"] is True
        approval_id_publish = publish_request["request"]["id"]
        approve_publish = publish_gateway.approve_request(approval_id_publish, approved_by=owner)
        assert approve_publish["success"] is True

        publish_result = ApprovalResumeService(
            approval_gateway=publish_gateway,
            connector_registry=registry,
        ).resume(approval_id_publish, current_user_id=owner)
        assert publish_result["status"] in {"resumed", "completed"}
        operation_key = publish_result["operation_key"]
        durable_publish = connector._pin_store.get_by_operation_key(owner, operation_key)
        assert durable_publish is not None
        assert durable_publish["status"] == "published"
        assert durable_publish["link_url"].startswith("https://example.com/product")

        mission_row = {
            "id": mission_id,
            "objective": payload["job"]["objective"],
            "title": payload["job"]["title"],
            "status": "completed",
            "priority": "normal",
            "urgency": "normal",
            "business_importance": 1,
            "metadata": {"platform": "pinterest"},
            "result": {
                "action_type": "publish_content",
                "approval_request_id": approval_id_publish,
                "operation_key": operation_key,
                "pin_id": durable_publish["pin_id"],
                "link_url": durable_publish["link_url"],
                "status": "published",
            },
            "created_at": payload["job"]["created_at"],
            "updated_at": payload["job"]["updated_at"],
        }
        job = _normalize_job(mission_row, owner_id=owner, client=None)
        assert job["status"] == "published"
        assert job["lifecycle_status"] == "published"
        assert job["operation_key"] == operation_key
        assert job["publish_link_url"] == durable_publish["link_url"]

        current_user["id"] = other
        cross_user = client.get(f"/affiliate/{mission_id}")
        assert cross_user.status_code == 404
        assert cross_user.json()["detail"] == "Affiliate job not found"
    finally:
        approvals_router._get_approval_gateway = original_get_approval_gateway
        approvals_router._get_resume_service = original_get_resume_service
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_user_scoped_client, None)
