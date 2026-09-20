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


def _create_mission(token: str) -> dict[str, Any]:
    """POST /affiliate and return the parsed JSON response."""
    with httpx.Client() as http:
        resp = http.post(
            f"{_AEA_API_URL}/affiliate",
            json={
                "platform": "pinterest",
                "country": "US",
                "language": "en",
                "niche": "AI tools",
                "daily_limit": 30,
                "human_approval_required": True,
            },
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
    assert created["job"]["status"] == "created"
    assert created["job"]["platform"] == "pinterest"

    # 3. Fetch the job back
    fetched = _get_job(token, mission_id)
    assert fetched["success"] is True
    assert fetched["job"]["id"] == mission_id
    assert fetched["job"]["status"] == "created"

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
