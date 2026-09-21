"""Tests for P1-1: Approval → Resume pipeline.

Covers the deterministic, idempotent resume of connector onboarding
operations after an approval request has been approved.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.approval_gateway import ApprovalGateway  # noqa: E402
from app.services.approval_resume_service import ApprovalResumeService  # noqa: E402
from app.services.connectors.pinterest_connector import PinterestConnector  # noqa: E402
from app.services.connectors.registry import ConnectorRegistry  # noqa: E402
from app.services.human_intervention import HumanInterventionManager  # noqa: E402
from app.services.stores.onboarding_workflow_store import OnboardingWorkflowStore  # noqa: E402
from app.services.stores.platform_connection_store import PlatformConnectionStore  # noqa: E402
from app.services.stores.pin_publish_store import PinPublishStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def stores(supabase_disabled):
    """Construct fresh stores for every test (in-memory mode)."""
    return {
        "workflow": OnboardingWorkflowStore(client=None),
        "connection": PlatformConnectionStore(client=None),
    }


@pytest.fixture
def approval_gateway(supabase_disabled) -> ApprovalGateway:
    return ApprovalGateway()


@pytest.fixture
def human_intervention_manager(supabase_disabled) -> HumanInterventionManager:
    return HumanInterventionManager()


@pytest.fixture
def resume_service(stores, approval_gateway, human_intervention_manager):
    registry = ConnectorRegistry()
    registry.register(
        PinterestConnector(
            workflow_store=stores["workflow"],
            connection_store=stores["connection"],
            pin_store=PinPublishStore(),
        )
    )
    return ApprovalResumeService(
        approval_gateway=approval_gateway,
        connector_registry=registry,
        human_intervention_manager=human_intervention_manager,
    )


def _make_approval_request(
    approval_gateway: ApprovalGateway,
    *,
    action_type: str = "start_platform_onboarding",
    payload: dict | None = None,
    ttl_hours: int = 24,
) -> str:
    """Create an approval request and return its id."""
    payload = payload or {"platform": "pinterest", "worker_id": "worker-1"}
    result = approval_gateway.create_request(
        mission_id="mission-1",
        action_type=action_type,
        risk_level="sensitive",
        payload=payload,
        ttl_hours=ttl_hours,
    )
    assert result.get("success"), result
    return result["request"]["id"]


# ---------------------------------------------------------------------------
# 1-3: creation, approval, basic resume
# ---------------------------------------------------------------------------
def test_approval_can_be_created_for_connector_action(approval_gateway):
    request_id = _make_approval_request(approval_gateway)
    request = approval_gateway.get_request(request_id)
    assert request is not None
    assert request["action_type"] == "start_platform_onboarding"
    assert request["status"] == "pending"


def test_approval_can_be_approved(approval_gateway):
    request_id = _make_approval_request(approval_gateway)
    result = approval_gateway.approve_request(request_id, approved_by="human@example.com")
    assert result["success"]
    request = approval_gateway.get_request(request_id)
    assert request["status"] == "approved"


def test_approval_triggers_connector_resume(resume_service, approval_gateway):
    request_id = _make_approval_request(
        approval_gateway,
        payload={"platform": "pinterest", "worker_id": "worker-1"},
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id)

    # start_platform_onboarding for Pinterest produces a human checkpoint.
    assert result["status"] == "awaiting_human_intervention"
    assert result["approval_request_id"] == request_id
    assert result["workflow_id"]


# ---------------------------------------------------------------------------
# 4-5: workflow persistence + restart recovery
# ---------------------------------------------------------------------------
def test_workflow_is_loaded_from_persistent_store(resume_service, approval_gateway, stores):
    request_id = _make_approval_request(approval_gateway)
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id)
    workflow_id = result["workflow_id"]

    # The persisted workflow is reachable through the store.
    persisted = stores["workflow"].get(workflow_id)
    assert persisted is not None
    assert persisted["mission_id"] == "mission-1"
    assert persisted["worker_id"] == "worker-1"
    assert persisted["platform"] == "pinterest"


def test_resume_works_with_newly_created_connector_instance(
    approval_gateway, stores, human_intervention_manager
):
    """Simulate a process restart: a brand-new PinterestConnector with empty
    in-memory dicts but shared stores must still be able to resume a workflow
    that was started by the previous instance."""

    # Step 1: original connector starts onboarding, persisting via store.
    original = PinterestConnector(
        workflow_store=stores["workflow"],
        connection_store=stores["connection"],
    )
    start = original.start_onboarding("worker-restart", mission_id="mission-restart")
    workflow_id = start["workflow_id"]

    # Step 2: build an approval for ``resume_platform_onboarding`` and approve.
    request_id = _make_approval_request(
        approval_gateway,
        action_type="resume_platform_onboarding",
        payload={
            "platform": "pinterest",
            "worker_id": "worker-restart",
            "workflow_id": workflow_id,
            "human_input": {"oauth_code": "test-code", "email": "user@example.com"},
        },
    )
    approval_gateway.approve_request(request_id)

    # Step 3: brand-new connector + brand-new registry + resume service.
    fresh_connector = PinterestConnector(
        workflow_store=stores["workflow"],
        connection_store=stores["connection"],
    )
    assert workflow_id not in fresh_connector._onboarding_workflows

    registry = ConnectorRegistry()
    registry.register(fresh_connector)
    service = ApprovalResumeService(
        approval_gateway=approval_gateway,
        connector_registry=registry,
        human_intervention_manager=human_intervention_manager,
    )

    result = service.resume(request_id)
    # The resume advanced the workflow to step 2 (email verification), which
    # produces another human checkpoint.
    assert result["status"] == "awaiting_human_intervention"
    assert result["workflow_id"] == workflow_id


# ---------------------------------------------------------------------------
# 6: connector can produce another human checkpoint
# ---------------------------------------------------------------------------
def test_resume_produces_follow_on_human_checkpoint(
    resume_service, approval_gateway, human_intervention_manager
):
    request_id = _make_approval_request(
        approval_gateway,
        action_type="resume_platform_onboarding",
        payload={
            "platform": "pinterest",
            "worker_id": "worker-1",
            "workflow_id": "wf-follow-on",
            "human_input": {"oauth_code": "c"},
        },
    )
    # Pre-create a workflow so resume finds something.
    workflow_store = resume_service._connector_registry.get("pinterest")._workflow_store
    workflow_store.create(
        workflow_id="wf-follow-on",
        mission_id="mission-1",
        worker_id="worker-1",
        platform="pinterest",
        status="awaiting_human",
        current_step=1,
        total_steps=3,
        checkpoint_data={},
        step_history=[{"step": 1, "status": "pending"}],
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id)

    assert result["status"] == "awaiting_human_intervention"
    assert result["workflow_id"] == "wf-follow-on"
    assert result["checkpoint"]["platform"] == "pinterest"
    assert result["checkpoint"]["status"] == "awaiting_human"


# ---------------------------------------------------------------------------
# 7: completed onboarding marks platform connection connected
# ---------------------------------------------------------------------------
def test_completed_onboarding_marks_platform_connection_connected(
    resume_service, approval_gateway, stores
):
    workflow_store = stores["workflow"]
    workflow_store.create(
        workflow_id="wf-complete",
        mission_id="mission-1",
        worker_id="worker-finish",
        platform="pinterest",
        status="awaiting_human",
        current_step=3,
        total_steps=3,
        checkpoint_data={},
        step_history=[
            {"step": 1, "status": "completed"},
            {"step": 2, "status": "completed"},
            {"step": 3, "status": "pending"},
        ],
    )

    request_id = _make_approval_request(
        approval_gateway,
        action_type="resume_platform_onboarding",
        payload={
            "platform": "pinterest",
            "worker_id": "worker-finish",
            "workflow_id": "wf-complete",
            "human_input": {"account_configured": True},
        },
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id)
    assert result["status"] == "completed"

    persisted = stores["connection"].get("worker-finish", "pinterest")
    assert persisted is not None
    assert persisted["status"] == "connected"


# ---------------------------------------------------------------------------
# 8-9: rejection and expiry do not resume
# ---------------------------------------------------------------------------
def test_rejected_approval_does_not_resume(resume_service, approval_gateway):
    request_id = _make_approval_request(approval_gateway)
    approval_gateway.reject_request(request_id, reason="not now")

    result = resume_service.resume(request_id)
    assert result["status"] == "approval_rejected"
    assert "rejected" in result["error"].lower()


def test_expired_approval_does_not_resume(approval_gateway):
    """Approvals with expires_at in the past must not trigger resume."""
    approval_gateway_local = ApprovalGateway()
    request_id = _make_approval_request(
        approval_gateway_local, ttl_hours=0  # 0-hour TTL produces a near-now expiry
    )
    approval_gateway_local.approve_request(request_id)

    # Replace the in-memory record with one whose expires_at is in the past.
    past_dt = datetime.now(timezone.utc) - timedelta(hours=1)
    existing = approval_gateway_local._memory_store[request_id]
    existing.expires_at = past_dt

    registry = ConnectorRegistry()
    registry.register(PinterestConnector())
    service = ApprovalResumeService(
        approval_gateway=approval_gateway_local,
        connector_registry=registry,
        human_intervention_manager=HumanInterventionManager(),
    )

    result = service.resume(request_id)
    assert result["status"] == "approval_expired"
    assert "expired" in result["error"].lower()


@pytest.fixture
def resume_gateway_factory():
    """Marker fixture so pytest recognizes the test parameter."""
    return None


# ---------------------------------------------------------------------------
# 10: duplicate processing does not execute twice
# ---------------------------------------------------------------------------
def test_duplicate_approval_processing_does_not_execute_twice(
    resume_service, approval_gateway, stores
):
    """Resume must be idempotent. Calling resume twice for the same approval
    must not advance the workflow twice or create a duplicate resume."""
    workflow_store = stores["workflow"]

    # Pre-create a workflow at the final step so the first resume completes.
    workflow_store.create(
        workflow_id="wf-idem",
        mission_id="mission-1",
        worker_id="worker-idem",
        platform="pinterest",
        status="awaiting_human",
        current_step=3,
        total_steps=3,
        checkpoint_data={},
        step_history=[
            {"step": 1, "status": "completed"},
            {"step": 2, "status": "completed"},
            {"step": 3, "status": "pending"},
        ],
    )
    request_id = _make_approval_request(
        approval_gateway,
        action_type="resume_platform_onboarding",
        payload={
            "platform": "pinterest",
            "worker_id": "worker-idem",
            "workflow_id": "wf-idem",
            "human_input": {"account_configured": True},
        },
    )
    approval_gateway.approve_request(request_id)

    # First resume advances the workflow to completion and upserts the
    # platform connection.
    first = resume_service.resume(request_id)
    assert first["status"] == "completed"

    # Second resume (with cache cleared to simulate a fresh service)
    # must return the existing completed state via the state-level guard,
    # without executing the connector action a second time.
    resume_service._executed.clear()
    second = resume_service.resume(request_id)
    assert second["status"] == "completed"
    assert second.get("note") == "workflow_already_completed"

    # Third resume, with the cache populated, must hit the in-process
    # idempotency guard and not perform additional work.
    third = resume_service.resume(request_id)
    assert third["status"] in {"completed", "awaiting_resume"}
    if third["status"] == "awaiting_resume":
        # In-process cache caught the duplicate.
        assert third["approval_request_id"] == request_id
    else:
        # State guard caught the duplicate.
        assert third.get("note") == "workflow_already_completed"


# ---------------------------------------------------------------------------
# 11-12: explicit failure paths
# ---------------------------------------------------------------------------
def test_unknown_workflow_returns_explicit_failure(resume_service, approval_gateway):
    request_id = _make_approval_request(
        approval_gateway,
        action_type="resume_platform_onboarding",
        payload={
            "platform": "pinterest",
            "worker_id": "worker-1",
            "workflow_id": "does-not-exist",
            "human_input": {},
        },
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id)
    assert result["status"] == "resumed"
    # The connector returns a failure inside its result dict for an unknown
    # workflow id; the resume service surfaces it as a failed connector
    # outcome. The status key is therefore still ``resumed`` because the
    # dispatch was attempted — verify the inner error is explicit.
    inner = result.get("result", {})
    assert not inner.get("success")
    assert "not found" in inner.get("error", "").lower()


def test_connector_failure_returns_explicit_failure(resume_service, approval_gateway):
    # Use an unknown platform so the registry lookup fails.
    request_id = _make_approval_request(
        approval_gateway,
        payload={"platform": "no-such-platform", "worker_id": "worker-1"},
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id)
    assert result["status"] == "resume_failed"
    assert "no connector registered" in result["error"].lower()


def test_approval_not_found(resume_service):
    result = resume_service.resume("nonexistent-id")
    assert result["status"] == "approval_not_found"


def test_payload_missing_platform(resume_service, approval_gateway):
    request_id = _make_approval_request(
        approval_gateway,
        payload={"worker_id": "worker-1"},  # no platform
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id)
    assert result["status"] == "resume_failed"
    assert "platform" in result["error"].lower()


# ---------------------------------------------------------------------------
# 13-14: regression — pre-existing approval/connector tests still pass
# ---------------------------------------------------------------------------
def test_existing_approval_gateway_flow_still_works(approval_gateway):
    """Sanity check: the original approval gateway operations still pass."""
    request_id = _make_approval_request(approval_gateway)
    listed = approval_gateway.list_requests(mission_id="mission-1")
    assert any(r["id"] == request_id for r in listed)

    approval_gateway.approve_request(request_id)
    assert approval_gateway.is_approved(request_id)

    approval_gateway.reject_request("other-id", reason="x")
    # P1-2: second approval of an already-approved request is idempotent
    # at the state level — the DB state is the source of truth and the
    # already-terminal record is returned without a second state change.
    second = approval_gateway.approve_request(request_id)
    assert second["success"] is True
    assert second.get("idempotent") is True
    assert second["request"]["status"] == "approved"


def test_existing_p0_5_persistence_still_works(stores):
    """Sanity check: P0-5 stores still function correctly."""
    stores["workflow"].create(
        workflow_id="wf-regression",
        mission_id=None,
        worker_id="w",
        platform="pinterest",
        status="awaiting_human",
        current_step=1,
        total_steps=3,
        checkpoint_data={},
        step_history=[],
    )
    assert stores["workflow"].get("wf-regression") is not None

    stores["connection"].upsert(
        owner_id="w",
        platform="pinterest",
        status="connected",
    )
    assert stores["connection"].get("w", "pinterest")["status"] == "connected"


# ---------------------------------------------------------------------------
# 15: publish_content approval → resume pipeline (P1-11)
# ---------------------------------------------------------------------------
def test_publish_content_requires_platform_connection(resume_service, approval_gateway):
    """publish_content resume fails when the platform is not connected."""
    request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={
            "platform": "pinterest",
            "worker_id": "worker-1",
            "content": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"},
        },
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id, current_user_id="test-user-123")
    assert result["status"] == "resume_failed"
    assert "connection" in result["error"].lower()


def test_publish_content_resume_success(resume_service, approval_gateway):
    """A fully-connected platform resumes publish_content successfully."""
    connect_request_id = _make_approval_request(
        approval_gateway,
        action_type="connect_platform",
        payload={
            "platform": "pinterest",
            "worker_id": "pub-worker",
            "auth_data": {"oauth_code": "test-code"},
        },
    )
    approval_gateway.approve_request(connect_request_id)
    resume_service.resume(connect_request_id)

    publish_request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={
            "platform": "pinterest",
            "worker_id": "pub-worker",
            "content": {
                "board_name": "My Board",
                "pin_text": "Check this out!",
                "link_url": "https://example.com/product",
                "opportunity_id": "opp-42",
            },
        },
    )
    approval_gateway.approve_request(publish_request_id)

    result = resume_service.resume(publish_request_id, current_user_id="test-user-123")
    assert result["status"] == "resumed"
    assert result["action_type"] == "publish_content"
    assert result["platform"] == "pinterest"
    assert result["worker_id"] == "pub-worker"
    assert result["pin_id"]
    # Server-derived operation_key must be p1-11:{approval_request_id}.
    assert result["operation_key"] == f"p1-11:{publish_request_id}"
    inner = result["result"]
    assert inner["success"]
    assert inner["status"] == "published"
    assert "utm_source=aea" in inner["link_url"]


def test_publish_content_server_ignores_client_operation_key(
    resume_service, approval_gateway
):
    """Caller-supplied operation_key must not override the server-derived key."""
    connect_request_id = _make_approval_request(
        approval_gateway,
        action_type="connect_platform",
        payload={
            "platform": "pinterest",
            "worker_id": "ignore-worker",
            "auth_data": {"oauth_code": "test-code"},
        },
    )
    approval_gateway.approve_request(connect_request_id)
    resume_service.resume(connect_request_id)

    publish_request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={
            "platform": "pinterest",
            "worker_id": "ignore-worker",
            "operation_key": "client-should-be-ignored",
            "content": {
                "board_name": "B",
                "pin_text": "T",
                "link_url": "https://a.co",
            },
        },
    )
    approval_gateway.approve_request(publish_request_id)

    result = resume_service.resume(publish_request_id, current_user_id="test-user-123")
    assert result["status"] == "resumed"
    assert result["operation_key"] == f"p1-11:{publish_request_id}"
    assert result["operation_key"] != "client-should-be-ignored"


def test_publish_content_strips_sensitive_keys(resume_service, approval_gateway):
    """publish_content resume strips OAuth secrets from the content payload."""
    connect_request_id = _make_approval_request(
        approval_gateway,
        action_type="connect_platform",
        payload={
            "platform": "pinterest",
            "worker_id": "strip-worker",
            "auth_data": {"oauth_code": "test-code"},
        },
    )
    approval_gateway.approve_request(connect_request_id)
    resume_service.resume(connect_request_id)

    publish_request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={
            "platform": "pinterest",
            "worker_id": "strip-worker",
            "content": {
                "board_name": "B",
                "pin_text": "T",
                "link_url": "https://a.co",
                "access_token": "SECRET-TOK",
                "oauth_code": "SECRET-CODE",
                "client_secret": "SECRET-SECRET",
            },
        },
    )
    approval_gateway.approve_request(publish_request_id)

    result = resume_service.resume(publish_request_id, current_user_id="test-user-123")
    assert result["status"] == "resumed"
    inner = result["result"]
    assert inner["success"]
    # Sensitive keys must not appear in the published result or content.
    assert "access_token" not in inner
    assert "oauth_code" not in inner
    assert "client_secret" not in inner
    if "content" in inner:
        content = inner["content"]
        assert "access_token" not in content
        assert "oauth_code" not in content
        assert "client_secret" not in content


def test_publish_content_duplicate_is_completed(resume_service, approval_gateway):
    """A second resume of the same publish_content approval returns completed."""
    connect_request_id = _make_approval_request(
        approval_gateway,
        action_type="connect_platform",
        payload={
            "platform": "pinterest",
            "worker_id": "dup-worker",
            "auth_data": {"oauth_code": "test-code"},
        },
    )
    approval_gateway.approve_request(connect_request_id)
    resume_service.resume(connect_request_id)

    publish_request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={
            "platform": "pinterest",
            "worker_id": "dup-worker",
            "content": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"},
        },
    )
    approval_gateway.approve_request(publish_request_id)

    first = resume_service.resume(publish_request_id, current_user_id="test-user-123")
    assert first["status"] == "resumed"

    # Second resume must return completed with pin_already_published.
    second = resume_service.resume(publish_request_id, current_user_id="test-user-123")
    assert second["status"] == "completed"
    assert second.get("note") == "pin_already_published"


def test_publish_content_duplicate_after_restart(resume_service, approval_gateway):
    """Durable idempotency: a fresh connector/store sees the prior pin."""
    connect_request_id = _make_approval_request(
        approval_gateway,
        action_type="connect_platform",
        payload={
            "platform": "pinterest",
            "worker_id": "restart-dup-worker",
            "auth_data": {"oauth_code": "test-code"},
        },
    )
    approval_gateway.approve_request(connect_request_id)
    resume_service.resume(connect_request_id)

    publish_request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={
            "platform": "pinterest",
            "worker_id": "restart-dup-worker",
            "content": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"},
        },
    )
    approval_gateway.approve_request(publish_request_id)

    first = resume_service.resume(
        publish_request_id, current_user_id="test-user-123"
    )
    assert first["status"] == "resumed"

    # Simulate process restart: the in-process _executed cache is lost, but
    # the pin store (simulating DB persistence) retains the published pin.
    # The state guard should detect the duplicate via the pin store.
    resume_service._executed.clear()

    second = resume_service.resume(
        publish_request_id, current_user_id="test-user-123"
    )
    assert second["status"] == "completed"
    assert second.get("note") == "pin_already_published"


def test_publish_content_resume_rejects_request_owner_fallback_when_user_identity_missing(
    resume_service, approval_gateway
):
    """A forged owner_id in the approval payload must never authorize a publish for another user."""
    connect_request_id = _make_approval_request(
        approval_gateway,
        action_type="connect_platform",
        payload={
            "platform": "pinterest",
            "worker_id": "owner-hardening-worker",
            "auth_data": {"oauth_code": "test-code"},
        },
        ttl_hours=24,
    )
    approval_gateway.approve_request(connect_request_id)
    resume_service.resume(connect_request_id, current_user_id="user-a")

    malicious_request_id = approval_gateway.create_request(
        mission_id="mission-malicious",
        action_type="publish_content",
        risk_level="sensitive",
        payload={
            "platform": "pinterest",
            "worker_id": "owner-hardening-worker",
            "content": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"},
        },
        owner_id="user-b",
    )["request"]["id"]
    approval_gateway.approve_request(malicious_request_id)

    # The request record's owner_id must be authoritative; a missing
    # authenticated user cannot be used as a fallback to another owner's
    # protected publish operation.
    result = resume_service.resume(malicious_request_id)
    assert result["status"] in {"approval_unauthorized", "resume_failed"}
    assert "authenticated user" in result["error"].lower() or "owner" in result["error"].lower()


def test_publish_content_missing_worker_id_fails(resume_service, approval_gateway):
    """publish_content without worker_id returns resume_failed."""
    request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={
            "platform": "pinterest",
            # no worker_id
            "content": {"board_name": "B", "pin_text": "T", "link_url": "https://a.co"},
        },
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id, current_user_id="test-user-123")
    assert result["status"] == "resume_failed"
    assert "worker_id" in result["error"].lower()


def test_publish_content_unknown_platform_fails(resume_service, approval_gateway):
    """publish_content with an unknown platform fails gracefully."""
    request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={"platform": "unknown-platform", "worker_id": "w"},
    )
    approval_gateway.approve_request(request_id)

    result = resume_service.resume(request_id)
    assert result["status"] == "resume_failed"
    assert "no connector registered" in result["error"].lower()


def test_publish_content_invalid_url_scheme(resume_service, approval_gateway):
    """publish_content rejects non-http(s) link_url schemes."""
    connect_request_id = _make_approval_request(
        approval_gateway,
        action_type="connect_platform",
        payload={
            "platform": "pinterest",
            "worker_id": "url-worker",
            "auth_data": {"oauth_code": "test-code"},
        },
    )
    approval_gateway.approve_request(connect_request_id)
    resume_service.resume(connect_request_id)

    publish_request_id = _make_approval_request(
        approval_gateway,
        action_type="publish_content",
        payload={
            "platform": "pinterest",
            "worker_id": "url-worker",
            "content": {
                "board_name": "B",
                "pin_text": "T",
                "link_url": "javascript:alert(1)",
            },
        },
    )
    approval_gateway.approve_request(publish_request_id)

    result = resume_service.resume(publish_request_id, current_user_id="test-user-123")
    assert result["status"] == "resume_failed"
    assert "scheme" in result["error"].lower() or "http" in result["error"].lower()