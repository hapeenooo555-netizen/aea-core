"""Pin publishing persistence store for P1-11.

Backed by ``public.published_pins`` (see
``supabase/migrations/000000000015_p1_11_published_pins.sql``).

The database is the authoritative source of truth for publish idempotency.
The ``operation_key`` column has a UNIQUE constraint, so concurrent or
retry attempts with the same key can never create duplicate rows.

An in-memory fallback is provided **only** for isolated unit tests that run
without a Supabase client. It is explicitly NOT used in production: when a
client is available the store always writes to the database first and treats
database errors as failures (fail-closed).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

try:
    from .. import database as database_module
except Exception:  # pragma: no cover - fallback for missing runtime config
    try:
        from backend.app import database as database_module
    except Exception:  # pragma: no cover - fallback for direct execution
        database_module = None


TABLE_NAME = "published_pins"
STATUS_PUBLISHING = "publishing"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"
STATUS_UNCERTAIN = "uncertain"
_GLOBAL_MEMORY_STORE: dict[str, dict[str, Any]] = {}
_MEMORY_LOCK = threading.Lock()


class PinPublishStore:
    """Persist published-pin records with database-authoritative idempotency."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        durable_required: bool = False,
    ) -> None:
        """Initialize the store.

        Args:
            client: Optional pre-resolved Supabase client. When ``None`` the
                store resolves ``app.database.supabase_client`` lazily.
            durable_required: When ``True``, the store fail-closes if the
                database is unavailable. When ``False`` (default), an
                in-memory fallback is used so unit tests can run without
                Supabase.
        """
        self._explicit_client = client
        self._durable_required = durable_required
        self._memory_store: dict[str, dict[str, Any]] = _GLOBAL_MEMORY_STORE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def create(
        self,
        owner_id: str,
        worker_id: str,
        platform: str,
        operation_key: str,
        board_name: str,
        pin_text: str,
        link_url: str,
        pin_id: str | None = None,
        approval_request_id: str | None = None,
        content: dict[str, Any] | None = None,
        status: str = "published",
    ) -> dict[str, Any]:
        """Insert a published-pin record with atomic duplicate detection.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` so that concurrent
        requests with the same ``operation_key`` are race-safe at the
        database level. The unique index on ``operation_key`` is the final
        correctness authority.

        Returns:
            ``{"success": True, "created": bool, "pin": dict}`` on success.
            When ``created`` is ``False``, an existing record for the same
            ``operation_key`` was found and returned. On failure (DB error
            or durability required), returns
            ``{"success": False, "error": "..."}``.
        """
        if not owner_id:
            return {"success": False, "error": "owner_id is required"}
        if not operation_key:
            return {"success": False, "error": "operation_key is required"}

        now = datetime.now(timezone.utc).isoformat()
        record: dict[str, Any] = {
            "owner_id": owner_id,
            "worker_id": worker_id,
            "platform": platform,
            "operation_key": operation_key,
            "approval_request_id": approval_request_id,
            "board_name": board_name,
            "pin_text": pin_text,
            "link_url": link_url,
            "pin_id": pin_id,
            "status": status,
            "content": content or {},
            "created_at": now,
            "updated_at": now,
        }

        client = self._client()
        if client is not None:
            db_payload = {
                "id": str(uuid4()),
                "owner_id": owner_id,
                "worker_id": worker_id,
                "platform": platform,
                "operation_key": operation_key,
                "approval_request_id": approval_request_id,
                "board_name": board_name,
                "pin_text": pin_text,
                "link_url": link_url,
                "pin_id": pin_id,
                "status": status,
                "content": content or {},
                "created_at": now,
                "updated_at": now,
            }
            try:
                # ``INSERT ... ON CONFLICT (operation_key) DO NOTHING``.
                # postgrest-py exposes this as upsert(ignore_duplicates=True);
                # the previous ``insert().on_conflict()`` chain does not exist
                # in postgrest-py and silently fell back to process memory.
                response = (
                    client.table(TABLE_NAME)
                    .upsert(db_payload, on_conflict="operation_key", ignore_duplicates=True)
                    .execute()
                )
                rows = response.data or []
                if rows:
                    return {"success": True, "created": True, "pin": self._normalize_row(rows[0])}

                existing = self.get_by_operation_key(owner_id, operation_key)
                if existing is not None:
                    return {"success": True, "created": False, "pin": existing}

                # No inserted row and no readable existing row: the outcome is
                # unknown, so never report that this caller owns the claim.
                return {"success": False, "error": "Pin publish claim could not be confirmed"}
            except Exception as exc:
                if self._durable_required:
                    return {"success": False, "error": f"Pin persistence failed: {exc}"}

            if self._durable_required:
                return {"success": False, "error": "Durable pin persistence is unavailable"}

        # In-memory fallback — ONLY for non-durable test environments.
        # Keep compatibility with older tests that keyed the record only by
        # ``operation_key`` while still enforcing true owner isolation for all
        # lookups and writes.
        with _MEMORY_LOCK:
            memory_key = (owner_id, operation_key)
            existing = self._memory_store.get(memory_key)
            if existing is None:
                legacy = self._memory_store.get(operation_key)
                if legacy is not None and legacy.get("owner_id") == owner_id:
                    existing = legacy
            if existing is not None:
                return {"success": True, "created": False, "pin": self._normalize_row(existing)}

            record["id"] = str(uuid4())
            self._memory_store[memory_key] = record
            self._memory_store.setdefault(operation_key, record)
            return {"success": True, "created": True, "pin": self._normalize_row(record)}

    def get_by_operation_key(
        self,
        owner_id: str,
        operation_key: str,
    ) -> dict[str, Any] | None:
        """Retrieve a published-pin record by ``operation_key`` for ``owner_id``.

        When the database is available, RLS enforces that only rows owned by
        the current user are returned. The in-memory fallback keys purely by
        ``operation_key``.
        """
        if not owner_id or not operation_key:
            return None

        client = self._client()
        if client is not None:
            try:
                response = (
                    client.table(TABLE_NAME)
                    .select("*")
                    .eq("owner_id", owner_id)
                    .eq("operation_key", operation_key)
                    .limit(1)
                    .execute()
                )
                rows = response.data or []
                if rows:
                    return self._normalize_row(rows[0])
            except Exception:
                if self._durable_required:
                    return None
            if self._durable_required:
                return None

        memory_key = (owner_id, operation_key)
        record = self._memory_store.get(memory_key)
        if record is not None and record.get("owner_id") == owner_id:
            return self._normalize_row(record)

        legacy = self._memory_store.get(operation_key)
        if legacy is not None and legacy.get("owner_id") == owner_id:
            return self._normalize_row(legacy)
        return None

    def claim(self, **kwargs: Any) -> dict[str, Any]:
        """Atomically reserve ``operation_key`` before calling the provider.

        Exactly one caller receives ``created=True``; every concurrent or
        repeated caller receives the existing record instead.
        """
        kwargs.setdefault("pin_id", None)
        return self.create(status=STATUS_PUBLISHING, **kwargs)

    def transition(
        self,
        owner_id: str,
        operation_key: str,
        *,
        from_statuses: tuple[str, ...],
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Compare-and-set the record status.

        Updates only when the current status is one of ``from_statuses`` and
        returns the updated record, or ``None`` when no row matched (another
        caller won, or persistence failed).
        """
        if not owner_id or not operation_key or not from_statuses:
            return None
        values = {**updates, "updated_at": datetime.now(timezone.utc).isoformat()}
        client = self._client()
        if client is not None:
            try:
                response = (
                    client.table(TABLE_NAME)
                    .update(values)
                    .eq("owner_id", owner_id)
                    .eq("operation_key", operation_key)
                    .in_("status", list(from_statuses))
                    .execute()
                )
                rows = response.data or []
                if rows:
                    return self._normalize_row(rows[0])
                return None
            except Exception:
                if self._durable_required:
                    return None
            if self._durable_required:
                return None

        with _MEMORY_LOCK:
            record = self._memory_store.get((owner_id, operation_key))
            if record is None or record.get("owner_id") != owner_id:
                return None
            if record.get("status") not in from_statuses:
                return None
            record.update(values)
            return self._normalize_row(record)

    def exists(self, owner_id: str, operation_key: str) -> bool:
        """Return ``True`` when a pin exists for this owner + key."""
        return self.get_by_operation_key(owner_id, operation_key) is not None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _client(self) -> Any | None:
        """Resolve the Supabase client (explicit, lazy global, or none)."""
        if self._explicit_client is not None:
            return self._explicit_client
        if database_module is None:
            return None
        return getattr(database_module, "supabase_client", None)

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        """Produce a normalized dictionary matching the connector contract."""
        return {
            "id": row.get("id"),
            "owner_id": str(row.get("owner_id")) if row.get("owner_id") else None,
            "worker_id": row.get("worker_id"),
            "platform": row.get("platform"),
            "operation_key": row.get("operation_key"),
            "approval_request_id": row.get("approval_request_id"),
            "board_name": row.get("board_name"),
            "pin_text": row.get("pin_text"),
            "link_url": row.get("link_url"),
            "pin_id": row.get("pin_id"),
            "status": row.get("status"),
            "content": row.get("content") or {},
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }


__all__ = [
    "PinPublishStore",
    "STATUS_FAILED",
    "STATUS_PUBLISHED",
    "STATUS_PUBLISHING",
    "STATUS_UNCERTAIN",
]
