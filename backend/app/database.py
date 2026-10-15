"""Database module for AEA Core.

Normal application queries use the public key plus the user's JWT so RLS
continues to enforce per-user ownership. A separate, narrowly scoped
service-role client is used only for the read-only health probe.
"""

from __future__ import annotations

import os
from typing import Any

supabase_client: Any = None
_supabase_health_client: Any = None


def _resolve_supabase_credential() -> tuple[str, str]:
    """Resolve URL and public API key for user-scoped requests."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    return url, key


def is_supabase_configured() -> bool:
    """Return whether URL and public API key configuration are present."""
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


def _get_supabase_health_client() -> Any:
    """Create a separate service-role client exclusively for the health probe.

    The database migration revokes SELECT on protected tables from anon and
    grants it to authenticated. Since /health has no user JWT, the public
    client cannot verify a SELECT against missions. This client is isolated
    from normal application data paths and never returns query data.
    """
    global _supabase_health_client
    if _supabase_health_client is not None:
        return _supabase_health_client

    url = os.getenv("SUPABASE_URL", "")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not (url and service_role_key):
        return None
    try:
        from supabase import create_client

        _supabase_health_client = create_client(url, service_role_key)
        return _supabase_health_client
    except Exception:  # pragma: no cover - defensive runtime handling
        return None



_meta_webhook_client: Any = None


def get_supabase_service_client() -> Any:
    """Return a dedicated server-side client for verified inbound Meta webhooks.

    Never expose this privileged client to browser-facing routes. The webhook
    must verify Meta's signature and resolve an explicit tenant owner before use.
    """
    global _meta_webhook_client
    if _meta_webhook_client is not None:
        return _meta_webhook_client

    url = os.getenv("SUPABASE_URL", "")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not (url and service_role_key):
        return None
    try:
        from supabase import create_client

        _meta_webhook_client = create_client(url, service_role_key)
        return _meta_webhook_client
    except Exception:  # pragma: no cover - defensive runtime handling
        return None

_oauth_callback_client: Any = None


def get_supabase_oauth_callback_client() -> Any:
    """Return a dedicated service-role client for state-bound OAuth callbacks.

    Callers must validate the persisted, high-entropy, short-lived OAuth state
    and its owner/workflow binding before performing any writes. This client
    is never returned to a browser or exposed through an API response.
    """
    global _oauth_callback_client
    if _oauth_callback_client is not None:
        return _oauth_callback_client

    url = os.getenv("SUPABASE_URL", "")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not (url and service_role_key):
        return None
    try:
        from supabase import create_client

        _oauth_callback_client = create_client(url, service_role_key)
        return _oauth_callback_client
    except Exception:  # pragma: no cover - defensive runtime handling
        return None


def check_supabase_connection() -> bool:
    """Verify connectivity with one bounded, read-only query.

    The dedicated service-role client bypasses RLS, so the probe is
    intentionally constrained to SELECT id FROM missions LIMIT 1. Returned
    rows and exception details are discarded. The privileged client is never
    used by normal application requests.
    """
    client = _get_supabase_health_client()
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
