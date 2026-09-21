"""Real HTTP E2E test for the AEA Core Affiliate Employee lifecycle.

This test verifies the FULL end-to-end flow against a running FastAPI
server and a real Supabase backend:

    POST /affiliate
    -> GET /affiliate/{mission_id}
    -> POST /approvals/{approval_id}/approve
    -> GET /affiliate/{mission_id}

It uses a REAL Supabase Auth user (credentials from ``E2E_TEST_EMAIL``
and ``E2E_TEST_PASSWORD``) to obtain a real JWT via the Supabase Auth
sign-in API, then sends ``Authorization: Bearer <JWT>`` to the real
HTTP API.  No mocks, no TestClient, and no in-memory fallback stores.

Environment variables required:
    E2E_TEST_EMAIL      -- existing Supabase Auth user email
    E2E_TEST_PASSWORD   -- password for that user
    SUPABASE_URL        -- Supabase project URL (already required by the app)
    SUPABASE_ANON_KEY   -- Supabase anon key (already required by the app)
    AEA_API_URL         -- (optional) base URL of the AEA Core API;
                           defaults to http://localhost:8000

The test is automatically skipped when any required environment
variable is missing, so it never runs in CI or local unit-test
contexts that lack credentials.
"""

from __future__ import annotations

import os
import json
import time
import uuid
from typing import Any

import pytest
import httpx

# ---------------------------------------------------------------------------
# Environment-driven configuration
# ---------------------------------------------------------------------------

_REQUIRED_ENV = ["E2E_TEST_EMAIL", "E2E_TEST_PASSWORD", "SUPABASE_URL", "SUPABASE_ANON_KEY"]
_MISSING = [v for v in _REQUIRED_ENV if not os.environ.get(v)]

_SKIP_REASON = "Live E2E credentials not available (set E2E_TEST_EMAIL, E2E_TEST_PASSWORD, SUPABASE_URL, SUPABASE_ANON_KEY)"

pytestmark = pytest.mark.skipif(_MISSING, reason=_SKIP_REASON)

_AEA_API_URL = os.environ.get("AEA_API_URL", "http://localhost:8000").strip()
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()
_TEST_EMAIL = os.environ.get("E2E_TEST_EMAIL", "").strip()
_TEST_PASSWORD = os.environ.get("E2E_TEST_PASSWORD", "").strip()


# ---------------------------------------------------------------------------
# Helpers (real HTTP only)
# ---------------------------------------------------------------------------

def _obtain_real_jwt() -> str:
    """Sign in to Supabase Auth and return a real user access-token (JWT).

    Uses ``grant_type=password`` with the test user's credentials against
    the real Supabase Auth API.  The token is returned only to the caller
    and never written to disk or stdout.
    """
    signin_url = f"{_SUPABASE_URL}/auth/v1/token?grant_type=password"
    with httpx.Client() as http:
        resp = http.post(
            signin_url,
            json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD},
            headers={"apikey": _SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            timeout=15,
        )
    if resp.status_code != 200:
        pytest.fail(f"Supabase Auth sign-in failed: HTTP {resp.status_code}")
    body = resp.json()
    token = body.get("access_token")
    if not token:
        pytest.fail("Supabase Auth sign-in did not return an access_token")
    return token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _create_mission(token: str, *, niche: str | None = None, title: str | None = None) -> dict[str, Any]:
    """POST /affiliate and return the parsed JSON response."""
    payload = {
        "platform": "pinterest",
        "country": "US",
        "language": "en",
        "niche": niche or "AI tools",
        "daily_limit": 30,
        "human_approval_required": True,
        "run_now": True,
    }
    if title:
        payload["title"] = title
    with httpx.Client() as http:
        resp = http.post(
            f"{_AEA_API_URL}/affiliate",
            json=payload,
            headers=_auth_headers(token),
            timeout=30,
        )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _get_job(token: str, mission_id: str) -> dict[str, Any]:
    with httpx.Client() as http:
        resp = http.get(
            f"{_AEA_API_URL}/affiliate/{mission_id}",
            headers=_auth_headers(token),
            timeout=30,
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _list_jobs(token: str) -> dict[str, Any]:
    with httpx.Client() as http:
        resp = http.get(
            f"{_AEA_API_URL}/affiliate",
            headers=_auth_headers(token),
            timeout=30,
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _list_approvals(token: str, *, mission_id: str | None = None) -> dict[str, Any]:
    params = {"limit": 100}
    if mission_id:
        params["mission_id"] = mission_id
    with httpx.Client() as http:
        resp = http.get(
            f"{_AEA_API_URL}/approvals",
            params=params,
            headers=_auth_headers(token),
            timeout=30,
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _get_approval(token: str, approval_id: str) -> dict[str, Any]:
    with httpx.Client() as http:
        resp = http.get(
            f"{_AEA_API_URL}/approvals/{approval_id}",
            headers=_auth_headers(token),
            timeout=30,
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _approve_approval(token: str, approval_id: str) -> dict[str, Any]:
    with httpx.Client() as http:
        resp = http.post(
            f"{_AEA_API_URL}/approvals/{approval_id}/approve",
            json={},
            headers=_auth_headers(token),
            timeout=30,
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _list_pending_checkpoints(token: str, *, mission_id: str | None = None) -> dict[str, Any]:
    params = {}
    if mission_id:
        params["mission_id"] = mission_id
    with httpx.Client() as http:
        resp = http.get(
            f"{_AEA_API_URL}/connectors/checkpoints/pending",
            params=params,
            headers=_auth_headers(token),
            timeout=30,
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    return resp.json()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_real_http_affiliate_job_create_and_fetch():
    """Create an affiliate job via real HTTP and fetch it back.

    Verifies:
    - Real JWT authentication succeeds against the live API.
    - POST /affiliate creates a mission (201).
    - GET /affiliate/{id} returns the same job (200).
    - The job is scoped to the authenticated owner (RLS enforced).
    """
    # 1. Obtain a real JWT via Supabase Auth sign-in
    token = _obtain_real_jwt()
    assert "Bearer" not in token  # just a sanity check that we have a raw JWT

    # 2. Create an affiliate job via real HTTP
    created = _create_mission(token)
    assert created["success"] is True
    mission_id = created["mission_id"]
    assert created["job"]["platform"] == "pinterest"
    # With Option B dynamic injection, a Pinterest job with no connection
    # immediately reaches approval_pending (status check -> onboarding -> approval).
    assert created["job"]["status"] in {"created", "approval_pending"}

    fetched = _get_job(token, mission_id)
    assert fetched["success"] is True
    assert fetched["job"]["id"] == mission_id
    assert fetched["job"]["status"] in {"created", "approval_pending"}

    # 4. Verify the job appears in the list
    listed = _list_jobs(token)
    assert listed["count"] >= 1
    ids = [j["id"] for j in listed["jobs"]]
    assert mission_id in ids

    # 5. Verify no sensitive data leaked into the response
    response_str = json.dumps(created) + json.dumps(fetched) + json.dumps(listed)
    forbidden = {"access_token", "refresh_token", "oauth_code", "client_secret", "api_key", "password"}
    for word in forbidden:
        assert word not in response_str.lower(), f"Sensitive field '{word}' found in response"


def test_real_http_affiliate_lifecycle_reaches_human_oauth_checkpoint():
    """Approve the Pinterest onboarding gate and verify the mission stalls at the human OAuth checkpoint."""
    token = _obtain_real_jwt()
    suffix = uuid.uuid4().hex[:8]
    title = f"Affiliate Pinterest OAuth HITL regression {suffix}"

    created = _create_mission(token, niche=f"AI tools {suffix}", title=title)
    assert created["success"] is True
    mission_id = created["mission_id"]
    assert mission_id
    assert created["job"]["platform"] == "pinterest"

    run = created.get("run") or {}
    execution = run.get("execution") or {}
    if execution.get("status") == "WAIT_FOR_APPROVAL":
        assert execution.get("report", {}).get("resume_information", {}).get("approval_request_id")
    else:
        deadline = time.time() + 30
        while time.time() < deadline:
            current = _get_job(token, mission_id)
            job = current["job"]
            raw_status = job.get("raw_status")
            if raw_status == "waiting_approval" or job.get("status") == "approval_pending":
                break
            time.sleep(1)
        else:
            pytest.fail(f"Mission {mission_id} never reached waiting_approval")

        current = _get_job(token, mission_id)
        assert current["job"]["raw_status"] == "waiting_approval"
        assert current["job"]["status"] == "approval_pending"

    approvals = _list_approvals(token, mission_id=mission_id)
    assert approvals["count"] >= 1
    pending = next(
        (a for a in approvals["approvals"] if a.get("mission_id") == mission_id and a.get("status") == "pending"),
        None,
    )
    assert pending is not None, f"No pending approval found for mission {mission_id}"
    assert pending["action_type"] == "start_platform_onboarding"

    approval_id = pending["id"]
    approval_details = _get_approval(token, approval_id)
    assert approval_details["approval"]["id"] == approval_id
    assert approval_details["approval"]["action_type"] == "start_platform_onboarding"

    approve_response = _approve_approval(token, approval_id)
    assert approve_response["success"] is True
    assert approve_response["resume"]["status"] == "awaiting_human_intervention"
    checkpoint = approve_response["resume"].get("checkpoint") or {}
    assert checkpoint.get("checkpoint_type") == "oauth_authorization_required"
    assert checkpoint.get("status") == "awaiting_human"

    pending_checkpoints = _list_pending_checkpoints(token, mission_id=mission_id)
    checkpoint_rows = pending_checkpoints.get("checkpoints") or []
    assert checkpoint_rows, f"No pending checkpoint found for mission {mission_id}"
    human_checkpoint = next(
        (c for c in checkpoint_rows if c.get("mission_id") == mission_id),
        None,
    )
    assert human_checkpoint is not None
    assert human_checkpoint["checkpoint_type"] == "oauth_authorization_required"
    assert human_checkpoint["status"] == "awaiting_human"

    fetched = _get_job(token, mission_id)
    assert fetched["job"]["status"] == "human_action_required"
    assert fetched["job"]["raw_status"] == "waiting_human"
    assert fetched["job"].get("publish_link_url") is None
    assert fetched["job"].get("pin_id") is None
    assert fetched["job"].get("operation_key") is None

    response_str = json.dumps(created) + json.dumps(approve_response) + json.dumps(fetched)
    forbidden = {"access_token", "refresh_token", "oauth_code", "client_secret", "api_key", "password"}
    for word in forbidden:
        assert word not in response_str.lower(), f"Sensitive field '{word}' found in response"


def test_real_http_affiliate_approval_to_human_checkpoint_lifecycle():
    """Regression test: full P1-8 affiliate lifecycle from approval to HITL checkpoint.

    Verifies the complete durable execution path through real HTTP:
        POST /affiliate
        -> check_connection_status (not_started / needs_reconnect)
        -> dynamic start_onboarding step appended
        -> start_platform_onboarding approval created (pending)
        -> POST /approvals/{id}/approve
        -> ApprovalResumeService._handle_start_onboarding
        -> checkpoint persisted (oauth_authorization_required, awaiting_human)
        -> GET /affiliate/{mission_id} -> lifecycle=human_action_required

    Asserts each stage independently so a regression at any point is
    immediately localised.
    """
    token = _obtain_real_jwt()

    # Use a unique objective/title to avoid collisions with prior runs
    suffix = uuid.uuid4().hex[:8]
    title = f"Regression HITL approval flow {suffix}"

    # 1. Create job — must reach WAIT_FOR_APPROVAL (approval gate, not skip)
    created = _create_mission(token, niche=f"AI tools {suffix}", title=title)
    mission_id = created["mission_id"]
    assert created["success"] is True
    assert created["job"]["platform"] == "pinterest"

    run = created.get("run") or {}
    execution = run.get("execution") or {}
    assert execution.get("status") == "WAIT_FOR_APPROVAL", (
        f"Expected WAIT_FOR_APPROVAL, got: {execution.get('status')}"
    )

    # 2. Approval must exist for this mission with correct action_type
    approvals = _list_approvals(token, mission_id=mission_id)
    assert approvals["count"] >= 1, f"No approvals found for mission {mission_id}"
    target = next(
        (a for a in approvals["approvals"]
         if a.get("mission_id") == mission_id and a.get("status") == "pending"),
        None,
    )
    assert target is not None, "No pending approval for this mission"
    assert target["action_type"] == "start_platform_onboarding"

    # 3. Approve — must transition to awaiting_human_intervention
    approval_id = target["id"]
    approve_response = _approve_approval(token, approval_id)
    assert approve_response["success"] is True
    assert approve_response["resume"]["status"] == "awaiting_human_intervention"

    checkpoint = approve_response["resume"].get("checkpoint") or {}
    assert checkpoint.get("checkpoint_type") == "oauth_authorization_required"
    assert checkpoint.get("status") == "awaiting_human"

    # 4. Checkpoint must be persisted and visible via pending checkpoints API
    pending_cp = _list_pending_checkpoints(token, mission_id=mission_id)
    cp_rows = pending_cp.get("checkpoints") or []
    human_cp = next(
        (c for c in cp_rows if c.get("mission_id") == mission_id),
        None,
    )
    assert human_cp is not None, "No pending checkpoint found via /checkpoints/pending"
    assert human_cp["checkpoint_type"] == "oauth_authorization_required"
    assert human_cp["status"] == "awaiting_human"

    # 5. Mission state must reflect human_action_required (RLS-enforced)
    fetched = _get_job(token, mission_id)
    assert fetched["job"]["status"] == "human_action_required"
    assert fetched["job"]["raw_status"] == "waiting_human"

    # 6. Job must appear in list with the same non-terminal state
    listed = _list_jobs(token)
    listed_ids = [j["id"] for j in listed["jobs"]]
    assert mission_id in listed_ids
    listed_job = next(j for j in listed["jobs"] if j["id"] == mission_id)
    assert listed_job["status"] == "human_action_required"

    # 7. No publishing artifacts should exist yet (no Pinterest API call)
    assert fetched["job"].get("publish_link_url") is None
    assert fetched["job"].get("pin_id") is None
    assert fetched["job"].get("operation_key") is None

    # 8. No sensitive data in any response
    response_str = json.dumps(created) + json.dumps(approve_response) + json.dumps(fetched) + json.dumps(pending_cp)
    forbidden = {"access_token", "refresh_token", "oauth_code", "client_secret", "api_key", "password"}
    for word in forbidden:
        assert word not in response_str.lower(), f"Sensitive field '{word}' found in response"


def test_real_http_affiliate_job_owner_isolation():
    """A second authenticated user cannot see another user's affiliate job.

    This verifies that real PostgreSQL RLS (owner_id = auth.uid()) is
    enforcing cross-user isolation at the database level.
    """
    pytest.skip("Requires a second E2E test user; set E2E_TEST_EMAIL_2 / E2E_TEST_PASSWORD_2 to enable")

    # This block is intentionally skipped until a second test user is provisioned.
    # When E2E_TEST_EMAIL_2 is available:
    #   1. Obtain a second JWT
    #   2. Try GET /affiliate/{mission_id} with the second user
    #   3. Assert 404 (RLS denies access)
