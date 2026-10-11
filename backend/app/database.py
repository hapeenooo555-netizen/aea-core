"""Database module for AEA Core.

Provides database client initialization and connection management.
"""

from __future__ import annotations

import os
from typing import Any

supabase_client: Any = None
_supabase_health_client: Any = None


def _resolve_supabase_credential() -> tuple[str, str]:
    """Resolve URL and public API key for user-scoped requests.

    SUPABASE_KEY takes precedence. SUPABASE_ANON_KEY is the fallback.
    The service-role key is intentionally not used here so normal application
    data access continues to respect RLS.
    """
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    return url, key


def is_supabase_configured() -> bool:
    """Return whether URL and API key configuration are present."""
    url, key = _resolve_supabase_credential()
    return bool(url and key)


def get_supabase_client() -> Any:
    """Get or initialize the user-scoped Supabase client, or return None."""
    global supabase_client
    if supabase_client is not None:
        return supabase_client
    url, key = _resolve_supabase_credential()
    if not (url and key):
        return None
    try:
        from supabase import create_client

        supabase_client = create_client(url, key)
        return supabase_client
    except Exception:  # pragma: no cover - defensive runtime handling
        return None


def check_supabase_connection() -> bool:
    """Verify Supabase with a bounded, read-only query using the public client.

    This probe deliberately uses the same anon-key client as normal
    unauthenticated requests, not the service-role key. With owner-based RLS,
    an unauthenticated query may return zero rows; a successful PostgREST
    response still verifies the API/database path without bypassing RLS.
    No exception details are returned to callers.
    """
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("missions").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def get_supabase_url() -> str:
    """Return the configured Supabase URL, or an empty string."""
    url, _ = _resolve_supabase_credential()
    return url


def get_supabase_anon_key() -> str:
    """Return the configured public API key, or an empty string."""
    _, key = _resolve_supabase_credential()
    return key


def get_supabase_client_for_user(user_access_token: str) -> Any:
    """Create a Supabase client scoped to a specific user's access token.

    The client uses the anon key and attaches the user's JWT via auth.set_session
    so PostgreSQL RLS evaluates the authenticated user. The token is never
    logged, persisted, or returned by this function.
    """
    url, anon_key = _resolve_supabase_credential()
    if not (url and anon_key):
        return None
    try:
        from supabase import create_client

        client = create_client(url, anon_key)
        client.auth.set_session(access_token=user_access_token, refresh_token="")
        return client
    except Exception:  # pragma: no cover - defensive runtime handling
        return None


if is_supabase_configured():
    supabase_client = get_supabase_client()
