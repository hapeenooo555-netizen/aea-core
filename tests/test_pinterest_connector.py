"""Tests for Pinterest connector."""

import pytest

from app.services.connectors.pinterest_connector import PinterestConnector


def test_pinterest_platform_name():
    """Test Pinterest connector platform name."""
    connector = PinterestConnector()

    assert connector.platform == "pinterest"


def test_pinterest_capabilities():
    """Test Pinterest connector capabilities."""
    connector = PinterestConnector()
    caps = connector.capabilities

    assert caps.platform == "pinterest"
    assert caps.health_check
    assert caps.account_status
    assert caps.onboarding
    assert caps.connect_account
    assert caps.publish_content  # Now implemented
    assert not caps.get_analytics  # Not yet implemented


def test_pinterest_health_check():
    """Test Pinterest health check."""
    connector = PinterestConnector()

    result = connector.health_check()

    assert result["success"]
    assert result["platform"] == "pinterest"
    assert result["status"] == "available"
    assert "details" in result


def test_pinterest_account_status_default():
    """Test Pinterest account status when not initialized."""
    connector = PinterestConnector()

    result = connector.get_account_status("worker-123")

    assert result["success"]
    assert result["status"] == "not_started"
    assert not result["details"]["connected"]


def test_pinterest_connect_account_rejects_placeholder_worker_ids():
    """A default/placeholder worker ID must never create an implicit Pinterest connection."""
    connector = PinterestConnector()

    result = connector.connect_account("default", {"oauth_code": "test-code"})

    assert not result["success"]
    assert "explicit" in result["error"].lower()
    assert connector.get_account_status("default")["status"] == "not_started"


def test_pinterest_start_onboarding():
    """Test starting Pinterest onboarding."""
    connector = PinterestConnector()

    result = connector.start_onboarding("worker-123")

    assert result["success"]
    assert result["status"] == "awaiting_human"
    assert result["platform"] == "pinterest"
    assert result["workflow_id"]
    assert result["current_step"] == 1
    assert result["requires_human_intervention"]
    assert result["checkpoint_type"] == "oauth_authorization_required"
    assert "instructions" in result


def test_pinterest_resume_onboarding_invalid_workflow():
    """Test resuming onboarding with invalid workflow ID."""
    connector = PinterestConnector()

    result = connector.resume_onboarding("invalid-workflow-id", {})

    assert not result["success"]
    assert "not found" in result["error"].lower()


def test_pinterest_resume_onboarding_step_two():
    """Test resuming onboarding progresses to step 2."""
    connector = PinterestConnector()

    # Start onboarding
    start_result = connector.start_onboarding("worker-123")
    workflow_id = start_result["workflow_id"]

    # Resume with oauth completion
    resume_result = connector.resume_onboarding(
        workflow_id,
        {"oauth_code": "test-code", "email": "user@example.com"},
    )

    assert resume_result["success"]
    assert resume_result["status"] == "awaiting_human"
    assert resume_result["current_step"] == 2
    assert resume_result["checkpoint_type"] == "email_verification_required"
    assert "instructions" in resume_result


def test_pinterest_resume_onboarding_step_three():
    """Test resuming onboarding progresses to step 3."""
    connector = PinterestConnector()

    # Start onboarding
    start_result = connector.start_onboarding("worker-123")
    workflow_id = start_result["workflow_id"]

    # Complete step 1
    step2_result = connector.resume_onboarding(
        workflow_id,
        {"oauth_code": "test-code", "email": "user@example.com"},
    )

    # Complete step 2
    step3_result = connector.resume_onboarding(
        workflow_id,
        {"email_verified": True},
    )

    assert step3_result["success"]
    assert step3_result["status"] == "awaiting_human"
    assert step3_result["current_step"] == 3
    assert step3_result["checkpoint_type"] == "manual_platform_step_required"


def test_pinterest_resume_onboarding_completes():
    """Test that onboarding completes after all steps."""
    connector = PinterestConnector()

    # Start onboarding
    start_result = connector.start_onboarding("worker-123")
    workflow_id = start_result["workflow_id"]

    # Complete step 1
    connector.resume_onboarding(
        workflow_id,
        {"oauth_code": "test-code", "email": "user@example.com"},
    )

    # Complete step 2
    connector.resume_onboarding(
        workflow_id,
        {"email_verified": True},
    )

    # Complete step 3
    final_result = connector.resume_onboarding(
        workflow_id,
        {"account_configured": True},
    )

    assert final_result["success"]
    assert final_result["status"] == "completed"
    assert not final_result["requires_human_intervention"]


def test_pinterest_connect_account():
    """Test connecting Pinterest account."""
    connector = PinterestConnector()

    result = connector.connect_account("worker-123", {"oauth_code": "test-code"})

    assert result["success"]
    assert result["status"] == "connected"


def test_pinterest_connect_account_missing_oauth():
    """Test connect account fails without OAuth code."""
    connector = PinterestConnector()

    result = connector.connect_account("worker-123", {})

    assert not result["success"]
    assert "authorization code" in result["error"].lower()


def test_pinterest_publish_content_requires_connection(supabase_disabled):
    """publish_content fails when the platform connection is not established."""
    connector = PinterestConnector()

    result = connector.publish_content("worker-123", {"board_name": "board", "pin_text": "text", "link_url": "https://a.co"})

    assert not result["success"]
    assert "connection is not established" in result["error"].lower()


def test_pinterest_publish_content_validates_required_fields(supabase_disabled):
    """publish_content fails when required fields are missing."""
    connector = PinterestConnector()

    # No board_name
    result = connector.publish_content("worker-123", {"pin_text": "text", "link_url": "https://a.co"})
    assert not result["success"]
    assert "board_name" in result["error"].lower()

    # No pin_text
    result = connector.publish_content("worker-123", {"board_name": "board", "link_url": "https://a.co"})
    assert not result["success"]
    assert "pin_text" in result["error"].lower()

    # No link_url
    result = connector.publish_content("worker-123", {"board_name": "board", "pin_text": "text"})
    assert not result["success"]
    assert "link_url" in result["error"].lower()


def test_pinterest_publish_content_success(supabase_disabled):
    """publish_content succeeds when connected with valid content."""
    connector = PinterestConnector()

    # Establish connection first
    connector.connect_account("worker-123", {"oauth_code": "test-code"})

    result = connector.publish_content(
        "worker-123",
        {
            "board_name": "My Board",
            "pin_text": "Check this out!",
            "link_url": "https://example.com/product",
        },
        idempotency_key="p1-11:test-approval-1",
        owner_id="user-uuid-123",
    )

    assert result["success"]
    assert result["status"] == "published"
    assert result["platform"] == "pinterest"
    assert result["board_name"] == "My Board"
    assert result["pin_text"] == "Check this out!"
    assert result["pin_id"]
    assert result["operation_key"] == "p1-11:test-approval-1"


def test_pinterest_publish_content_strips_sensitive_keys(supabase_disabled):
    """publish_content must not include sensitive auth keys in the result."""
    connector = PinterestConnector()
    connector.connect_account("worker-123", {"oauth_code": "test-code"})

    result = connector.publish_content(
        "worker-123",
        {
            "board_name": "My Board",
            "pin_text": "Text",
            "link_url": "https://example.com",
            "access_token": "SHOULD-NOT-APPEAR",
            "refresh_token": "SHOULD-NOT-APPEAR",
            "oauth_code": "SHOULD-NOT-APPEAR",
        },
        idempotency_key="p1-11:test-approval-2",
        owner_id="user-uuid-123",
    )

    assert result["success"]
    assert "access_token" not in result
    assert "refresh_token" not in result
    assert "oauth_code" not in result


def test_pinterest_publish_content_appends_affiliate_tracking(supabase_disabled):
    """publish_content appends utm_source/utm_campaign for opportunity_id."""
    connector = PinterestConnector()
    connector.connect_account("worker-123", {"oauth_code": "test-code"})

    result = connector.publish_content(
        "worker-123",
        {
            "board_name": "Board",
            "pin_text": "Text",
            "link_url": "https://example.com/product",
            "opportunity_id": "opp-42",
        },
        idempotency_key="p1-11:test-approval-3",
        owner_id="user-uuid-123",
    )

    assert result["success"]
    assert "utm_source=aea" in result["link_url"]
    assert "utm_campaign=opportunity_opp-42" in result["link_url"]


def test_pinterest_publish_content_idempotency(supabase_disabled):
    """publish_content with the same idempotency_key returns the prior pin."""
    connector = PinterestConnector()
    connector.connect_account("worker-123", {"oauth_code": "test-code"})

    content = {"board_name": "Board", "pin_text": "Text", "link_url": "https://example.com"}
    first = connector.publish_content(
        "worker-123", content, idempotency_key="p1-11:dedup-test", owner_id="user-uuid-123"
    )
    second = connector.publish_content(
        "worker-123", content, idempotency_key="p1-11:dedup-test", owner_id="user-uuid-123"
    )

    assert first["success"]
    assert "duplicate" in second.get("status", "")
    assert second["pin_id"] == first["pin_id"]
