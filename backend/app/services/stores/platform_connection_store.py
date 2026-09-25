"""Platform connection persistence store.

Backed by ``public.platform_connections`` (see
``database/migrations/sprint7_2b_platform_connectors.sql``).

Holds non-sensitive metadata only (status, external_account_id,
display_name, scopes). Never stores access tokens, refresh tokens,
client secrets, authorization codes, or other OAuth credentials.

Uses upsert semantics so repeated connection updates for the same
``(owner_id, platform)`` pair do not create duplicate rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

try:
    from .. import database as database_module
except Exception:  # pragma: no cover - defensive import fallback
    try:
        from backend.app import database as database_module
    except Exception:  # pragma: no cover - defensive import fallback
        database_module = None


TABLE_NAME = "platform_connections"


class PlatformConnectionStore:
    """Persist platform connection records with optional DB backing."""

    # Keys that must never be persisted regardless of caller.
    FORBIDDEN_KEYS = {
        "access_token",
        "refresh_token",
        "authorization_code",
        "oauth_code",
        "client_secret",
        "api_key",
        "token",
        "password",
        "id_token",
        "session",
    }

    def __init__(self, client: Any | None = None) -> None:
        """Initialize the store.

        Args:
            client: Optional pre-resolved Supabase client. When ``None`` the
                store resolves ``app.database.supabase_client`` lazily.
        """
        self._explicit_client = client
        # In-memory fallback keyed by (owner_id, platform).
        self._memory_store: dict[tuple[str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def upsert(
        self,
        owner_id: str,
        platform: str,
        *,
        status: str,
        external_account_id: str | None = None,
        display_name: str | None = None,
        scopes: list[str] | None = None,
        token_reference: str | None = None,
    ) -> dict[str, Any]:
        """Insert or update a platform connection for ``(owner_id, platform)``.

        Returns:
            Dictionary with ``success`` flag and the normalized record.
        """
        if not owner_id:
            return {"success": False, "error": "owner_id is required"}
        if not platform:
            return {"success": False, "error": "platform is required"}

        now = datetime.now(timezone.utc).isoformat()
        sanitized_scopes = self._sanitize_scopes(scopes)

        client = self._client()
        if client is not None:
            try:
                db_payload = {
                    "owner_id": owner_id,
                    "platform": platform,
                    "status": status,
                    "external_account_id": external_account_id,
                    "display_name": display_name,
                    "scopes": sanitized_scopes,
                    "token_reference": token_reference,
                    "updated_at": now,
                }
# The platform_connections table has a unique constraint on
# (owner_id, platform). To get upsert semantics we first try to
# update an existing row, then fall back to insert when no row
# was updated. If the insert fails due to the unique constraint
# (concurrent insert), we fall back to updating again.
                try:
                    update_response = (
                        client.table(TABLE_NAME)
                        .update(db_payload)
                        .eq("owner_id", owner_id)
                        .eq("platform", platform)
                        .execute()
                    )
                    rows = update_response.data or []
                    if rows:
                        return {
                            "success": True,
                            "connection": self._normalize_row(rows[0], None),
                        }
                except Exception:  # pragma: no cover - defensive fallback
                    pass

                db_payload["id"] = str(uuid4())
                db_payload["created_at"] = now
                insert_response = (
                    client.table(TABLE_NAME)
                    .insert(db_payload)
                    .execute()
                )
                rows = insert_response.data or []
                if rows:
                    return {
                        "success": True,
                        "connection": self._normalize_row(rows[0], None),
                    }
                # If insert failed (e.g., unique constraint on
                # (owner_id, platform)), fall back to updating the
                # existing row.
                update_response = (
                    client.table(TABLE_NAME)
                    .update(db_payload)
                    .eq("owner_id", owner_id)
                    .eq("platform", platform)
                    .execute()
                )
                rows = update_response.data or []
                if rows:
                    return {
                        "success": True,
                        "connection": self._normalize_row(rows[0], None),
                    }
            except Exception:  # pragma: no cover - defensive fallback
                pass

        # In-memory fallback path
        key = (owner_id, platform)
        existing = self._memory_store.get(key, {})
        record = {
            "id": existing.get("id") or str(uuid4()),
            "owner_id": owner_id,
            "platform": platform,
            "status": status,
            "external_account_id": external_account_id,
            "display_name": display_name,
            "scopes": sanitized_scopes,
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        self._memory_store[key] = record
        return {"success": True, "connection": self._normalize_row(None, record)}

    def get(self, owner_id: str, platform: str) -> dict[str, Any] | None:
        """Retrieve a single platform connection."""
        if not owner_id or not platform:
            return None

        client = self._client()
        if client is not None:
            try:
                response = (
                    client.table(TABLE_NAME)
                    .select("*")
                    .eq("owner_id", owner_id)
                    .eq("platform", platform)
                    .limit(1)
                    .execute()
                )
                rows = response.data or []
                if rows:
                    return self._normalize_row(rows[0], None)
            except Exception:  # pragma: no cover - defensive fallback
                pass

        record = self._memory_store.get((owner_id, platform))
        if record is not None:
            return self._normalize_row(None, record)
        return None

    def list_by_owner(self, owner_id: str) -> list[dict[str, Any]]:
        """List all platform connections for an owner."""
        if not owner_id:
            return []

        results: list[dict[str, Any]] = []

        client = self._client()
        if client is not None:
            try:
                response = (
                    client.table(TABLE_NAME)
                    .select("*")
                    .eq("owner_id", owner_id)
                    .execute()
                )
                for row in response.data or []:
                    normalized = self._normalize_row(row, None)
                    if normalized:
                        results.append(normalized)
                return results
            except Exception:  # pragma: no cover - defensive fallback
                pass

        for (stored_owner, _platform), record in self._memory_store.items():
            if stored_owner != owner_id:
                continue
            normalized = self._normalize_row(None, record)
            if normalized:
                results.append(normalized)
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _client(self) -> Any | None:
        """Resolve the Supabase client to use (explicit, lazy, or none)."""
        if self._explicit_client is not None:
            return self._explicit_client
        if database_module is None:
            return None
        return getattr(database_module, "supabase_client", None)

    @classmethod
    def _sanitize_scopes(cls, scopes: Any) -> list[str]:
        """Return a list of scope strings, free of forbidden OAuth values."""
        if not scopes:
            return []
        if isinstance(scopes, str):
            candidates = [scopes]
        elif isinstance(scopes, list):
            candidates = scopes
        else:
            return []
        cleaned: list[str] = []
        for value in candidates:
            if not isinstance(value, str):
                continue
            lowered = value.lower()
            if any(token in lowered for token in cls.FORBIDDEN_KEYS):
                continue
            cleaned.append(value)
        return cleaned

    @staticmethod
    def _normalize_row(
        row: dict[str, Any] | None,
        fallback: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Produce a normalized connection dictionary."""
        source = row if row is not None else fallback
        if source is None:
            return None

        scopes = source.get("scopes") or []
        if not isinstance(scopes, list):
            scopes = []

        return {
            "id": source.get("id"),
            "owner_id": source.get("owner_id"),
            "platform": source.get("platform"),
            "status": source.get("status"),
            "external_account_id": source.get("external_account_id"),
            "display_name": source.get("display_name"),
            "scopes": scopes,
            "created_at": source.get("created_at"),
            "updated_at": source.get("updated_at"),
        }


__all__ = ["PlatformConnectionStore"]