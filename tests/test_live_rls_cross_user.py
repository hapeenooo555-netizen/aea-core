"""Opt-in real HTTP cross-user isolation smoke test.

This test creates one uniquely named mission in an explicitly designated TEST
environment, then verifies a second authenticated user cannot fetch or list it.
It is skipped unless dedicated test credentials and AEA_TEST_API_URL are set.
Never point AEA_TEST_API_URL at production.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import httpx
import pytest

_REQUIRED = (
    "AEA_TEST_ENVIRONMENT",
    "AEA_TEST_API_URL",
    "E2E_TEST_EMAIL",
    "E2E_TEST_PASSWORD",
    "E2E_TEST_EMAIL_2",
    "E2E_TEST_PASSWORD_2",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
)
_MISSING = [name for name in _REQUIRED if not os.environ.get(name)]
_LIVE_TEST_SKIP = pytest.mark.skipif(
    bool(_MISSING),
    reason="Opt-in RLS smoke test requires dedicated test-environment URL and two test users",
)

_PRODUCTION_HOSTS = {"heroic-charisma-production-8896.up.railway.app"}


def _base_url() -> str:
    if os.environ.get("AEA_TEST_ENVIRONMENT") != "1":
        pytest.skip("Set AEA_TEST_ENVIRONMENT=1 only for a disposable test/staging environment")
    value = os.environ["AEA_TEST_API_URL"].strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        pytest.fail("AEA_TEST_API_URL must use HTTPS outside localhost")
    if parsed.hostname in _PRODUCTION_HOSTS:
        pytest.fail("Refusing to create test records on the known production host")
    if not parsed.hostname:
        pytest.fail("AEA_TEST_API_URL must be an absolute URL")
    return value


def _token(email_var: str, password_var: str) -> str:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/token?grant_type=password"
    response = httpx.post(
        url,
        json={"email": os.environ[email_var], "password": os.environ[password_var]},
        headers={"apikey": os.environ["SUPABASE_ANON_KEY"]},
        timeout=15,
    )
    if response.status_code != 200:
        pytest.fail(f"Test-user sign-in failed with HTTP {response.status_code}")
    token = response.json().get("access_token")
    if not token:
        pytest.fail("Supabase Auth returned no access token for a test user")
    return token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_guard_refuses_known_production_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEA_TEST_ENVIRONMENT", "1")
    monkeypatch.setenv("AEA_TEST_API_URL", "https://heroic-charisma-production-8896.up.railway.app")
    with pytest.raises(pytest.fail.Exception, match="Refusing to create test records"):
        _base_url()


def test_guard_requires_explicit_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEA_TEST_ENVIRONMENT", raising=False)
    monkeypatch.setenv("AEA_TEST_API_URL", "https://staging.example.test")
    with pytest.raises(pytest.skip.Exception, match="disposable test/staging"):
        _base_url()


@_LIVE_TEST_SKIP
def test_second_user_cannot_read_or_list_first_users_mission() -> None:
    """Verify API-level owner isolation using two real Supabase JWTs."""
    base_url = _base_url()
    token_a = _token("E2E_TEST_EMAIL", "E2E_TEST_PASSWORD")
    token_b = _token("E2E_TEST_EMAIL_2", "E2E_TEST_PASSWORD_2")
    unique = uuid.uuid4().hex[:12]

    # Test writes are limited to a uniquely titled mission in the explicitly
    # configured test/staging environment; no approval or external action runs.
    create_response = httpx.post(
        f"{base_url}/affiliate",
        json={
            "platform": "pinterest",
            "country": "US",
            "language": "en",
            "niche": f"RLS test {unique}",
            "daily_limit": 1,
            "human_approval_required": True,
            "run_now": False,
            "title": f"TEST RLS isolation {unique}",
        },
        headers=_headers(token_a),
        timeout=30,
    )
    assert create_response.status_code == 201, (
        f"Test mission creation failed with HTTP {create_response.status_code}"
    )
    payload = create_response.json()
    mission_id = payload.get("mission_id")
    assert payload.get("success") is True and mission_id, "Test API returned no mission ID"

    # A non-owner must not fetch the known resource ID. Prefer 404 to avoid
    # disclosing whether another user's resource exists.
    get_response = httpx.get(
        f"{base_url}/affiliate/{mission_id}",
        headers=_headers(token_b),
        timeout=20,
    )
    assert get_response.status_code == 404, (
        f"Cross-user mission fetch should return 404, got {get_response.status_code}"
    )

    list_response = httpx.get(
        f"{base_url}/affiliate",
        headers=_headers(token_b),
        timeout=20,
    )
    assert list_response.status_code == 200, (
        f"Second user's list request failed with HTTP {list_response.status_code}"
    )
    listed = list_response.json().get("jobs", [])
    assert all(item.get("id") != mission_id for item in listed), (
        "Second user's list exposed the first user's mission"
    )
