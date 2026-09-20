"""Focused tests for POST /connectors/onboarding/{workflow_id}/resume checkpoint completion.

Tests the smallest fix: after a successful connector.resume_onboarding(),
the endpoint must call HumanInterventionManager.complete_checkpoint()
with the supplied checkpoint_id.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException as StarletteHTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.connectors import resume_onboarding as resume_endpoint
from app.routers.connectors import OnboardingResumeRequest


TEST_USER_ID = "user-a-uuid"
TEST_WORKFLOW_ID = "workflow-123"
TEST_CHECKPOINT_ID = "checkpoint-456"
TEST_MISSION_ID = "mission-789"


def _make_request(checkpoint_id=None, human_input=None):
    return OnboardingResumeRequest(
        checkpoint_id=checkpoint_id,
        human_input=human_input or {},
    )


def _patch_connector(resume_result):
    patcher = patch("app.routers.connectors._get_user_scoped_pinterest_connector")
    mock_fn = patcher.start()
    mock_connector = MagicMock()
    mock_connector.resume_onboarding.return_value = resume_result
    mock_fn.return_value = mock_connector
    return patcher, mock_connector


def _patch_manager(checkpoint_row):
    patcher = patch("app.routers.connectors.get_human_intervention_manager")
    mock_fn = patcher.start()
    mock_manager = MagicMock()
    mock_manager.get_checkpoint.return_value = checkpoint_row
    mock_manager.complete_checkpoint.return_value = {
        "success": True,
        "checkpoint": {"status": "completed"},
    }
    mock_fn.return_value = mock_manager
    return patcher, mock_manager


@pytest.fixture(autouse=True)
def _patch_supabase():
    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False):
        yield


# ---------------------------------------------------------------------------
# 1. Successful resume with checkpoint_id → checkpoint completed
# ---------------------------------------------------------------------------

def test_resume_with_checkpoint_completes_checkpoint_on_success():
    """When resume_onboarding succeeds, complete_checkpoint must be called."""
    resume_result = {
        "success": True,
        "status": "completed",
        "workflow_id": TEST_WORKFLOW_ID,
        "platform": "pinterest",
        "current_step": 3,
        "total_steps": 3,
        "requires_human_intervention": False,
    }
    conn_p, mock_connector = _patch_connector(resume_result)
    checkpoint_row = {
        "id": TEST_CHECKPOINT_ID,
        "mission_id": TEST_MISSION_ID,
        "status": "awaiting_human",
        "checkpoint_type": "oauth_authorization_required",
        "metadata": {"workflow_id": TEST_WORKFLOW_ID, "step": 1, "total_steps": 3},
    }
    mgr_p, mock_manager = _patch_manager(checkpoint_row)

    try:
        request = _make_request(
            checkpoint_id=TEST_CHECKPOINT_ID,
            human_input={"oauth_code": "test_code"},
        )
        result = asyncio.run(resume_endpoint(
            workflow_id=TEST_WORKFLOW_ID,
            request=request,
            current_user_id=TEST_USER_ID,
            client=None,
        ))

        assert result["success"] is True
        assert result["completed"] is True

        mock_connector.resume_onboarding.assert_called_once_with(
            TEST_WORKFLOW_ID, {"oauth_code": "test_code"},
        )
        mock_manager.complete_checkpoint.assert_called_once_with(
            TEST_CHECKPOINT_ID, human_input={"oauth_code": "test_code"},
        )
    finally:
        conn_p.stop()
        mgr_p.stop()


# ---------------------------------------------------------------------------
# 2. Failed resume → checkpoint NOT completed
# ---------------------------------------------------------------------------

def test_resume_failure_does_not_complete_checkpoint():
    """When resume_onboarding fails, complete_checkpoint must NOT be called."""
    resume_result = {
        "success": False,
        "error": "Workflow workflow-123 not found",
    }
    conn_p, mock_connector = _patch_connector(resume_result)
    checkpoint_row = {
        "id": TEST_CHECKPOINT_ID,
        "mission_id": TEST_MISSION_ID,
        "status": "awaiting_human",
        "checkpoint_type": "oauth_authorization_required",
        "metadata": {"workflow_id": TEST_WORKFLOW_ID},
    }
    mgr_p, mock_manager = _patch_manager(checkpoint_row)

    try:
        request = _make_request(
            checkpoint_id=TEST_CHECKPOINT_ID,
            human_input={"oauth_code": "bad"},
        )
        with pytest.raises(StarletteHTTPException) as exc_info:
            asyncio.run(resume_endpoint(
                workflow_id=TEST_WORKFLOW_ID,
                request=request,
                current_user_id=TEST_USER_ID,
                client=None,
            ))

        assert exc_info.value.status_code == 404

        mock_connector.resume_onboarding.assert_called_once()
        mock_manager.complete_checkpoint.assert_not_called()
    finally:
        conn_p.stop()
        mgr_p.stop()


# ---------------------------------------------------------------------------
# 3. Resume without checkpoint_id → existing behavior (no complete_checkpoint)
# ---------------------------------------------------------------------------

def test_resume_without_checkpoint_id_skips_complete():
    """When no checkpoint_id is supplied, complete_checkpoint must not be called."""
    resume_result = {
        "success": True,
        "status": "awaiting_human",
        "workflow_id": TEST_WORKFLOW_ID,
        "platform": "pinterest",
        "current_step": 2,
        "total_steps": 3,
        "requires_human_intervention": True,
        "checkpoint_type": "email_verification_required",
        "instructions": "Check your email",
        "metadata": {},
        "next_step": "email_verification_required",
    }
    conn_p, mock_connector = _patch_connector(resume_result)
    mgr_p, mock_manager = _patch_manager(None)

    try:
        request = _make_request(
            checkpoint_id=None,
            human_input={"oauth_code": "test_code"},
        )
        result = asyncio.run(resume_endpoint(
            workflow_id=TEST_WORKFLOW_ID,
            request=request,
            current_user_id=TEST_USER_ID,
            client=None,
        ))

        assert result["success"] is True
        assert result["requires_human_intervention"] is True

        mock_connector.resume_onboarding.assert_called_once()
        mock_manager.complete_checkpoint.assert_not_called()
    finally:
        conn_p.stop()
        mgr_p.stop()


# ---------------------------------------------------------------------------
# 4. Ownership protection — checkpoint resolves to different mission
# ---------------------------------------------------------------------------

def test_resume_checkpoint_ownership_protection():
    """If checkpoint resolves to a mission not owned by the user, return 404."""
    resume_result = {
        "success": True,
        "status": "completed",
        "workflow_id": TEST_WORKFLOW_ID,
        "platform": "pinterest",
        "current_step": 3,
        "total_steps": 3,
        "requires_human_intervention": False,
    }
    conn_p, mock_connector = _patch_connector(resume_result)
    checkpoint_row = {
        "id": TEST_CHECKPOINT_ID,
        "mission_id": "other-mission-not-owned",
        "status": "awaiting_human",
        "checkpoint_type": "oauth_authorization_required",
        "metadata": {"workflow_id": TEST_WORKFLOW_ID},
    }
    mgr_p, mock_manager = _patch_manager(checkpoint_row)

    # Build a mock client whose PostgREST query returns no rows (ownership check fails)
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

    try:
        request = _make_request(
            checkpoint_id=TEST_CHECKPOINT_ID,
            human_input={"oauth_code": "x"},
        )
        with pytest.raises(StarletteHTTPException) as exc_info:
            asyncio.run(resume_endpoint(
                workflow_id=TEST_WORKFLOW_ID,
                request=request,
                current_user_id=TEST_USER_ID,
                client=mock_client,
            ))

        assert exc_info.value.status_code == 404
        mock_connector.resume_onboarding.assert_not_called()
        mock_manager.complete_checkpoint.assert_not_called()
    finally:
        conn_p.stop()
        mgr_p.stop()
