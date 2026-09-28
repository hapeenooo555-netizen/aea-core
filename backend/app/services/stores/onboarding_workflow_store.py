"""Onboarding workflow persistence store.

Backed by ``public.onboarding_workflows`` (see
``database/migrations/sprint7_2b_platform_connectors.sql``).

        Legacy callers may use the in-memory fallback when no client is configured.
        Strict P1-7C callers fail closed when the database cannot prove a workflow
        read or write succeeded.

The store never persists OAuth secrets, tokens, or authorization codes.
``checkpoint_data`` and ``step_history`` are stored as JSONB and are
expected to carry non-sensitive context only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

try:
    from .. import database as database_module
except Exception:  # pragma: no cover - defensive import fallback
    try:
        from backend.app import database as database_module
    except Exception:  # pragma: no cover - defensive import fallback
        database_module = None


TABLE_NAME = "onboarding_workflows"


class OnboardingWorkflowStore:
    """Persist onboarding workflow records with optional DB backing."""

    def __init__(self, client: Any | None = None, *, durable_required: bool = False) -> None:
        """Initialize the store.

        Args:
            client: Optional pre-resolved Supabase client. When ``None`` the
                store resolves ``app.database.supabase_client`` lazily.
        """
        self._explicit_client = client
        self._durable_required = durable_required
        self._memory_store: dict[str, dict[str, Any]] = {}
        # Lock that serializes get_or_create_for_approval across threads
        # in this process. The database is the durable authority; this
        # lock only protects the in-memory fallback from a TOCTOU race.
        self._memory_lock = RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def create(
        self,
        workflow_id: str,
        mission_id: str | None,
        worker_id: str,
        platform: str,
        status: str,
        current_step: int,
        total_steps: int,
        checkpoint_data: dict[str, Any] | None,
        step_history: list[dict[str, Any]] | None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new onboarding workflow record.

        Returns:
            A normalized workflow dictionary.
        """
        if not workflow_id:
            return {"success": False, "error": "workflow_id is required"}
        if not worker_id:
            return {"success": False, "error": "worker_id is required"}
        if not platform:
            return {"success": False, "error": "platform is required"}

        now = datetime.now(timezone.utc).isoformat()
        safe_checkpoint = self._sanitize_json(checkpoint_data) or {}
        safe_history = self._sanitize_json(step_history) or []

        record: dict[str, Any] = {
            "workflow_id": workflow_id,
            "mission_id": mission_id,
            "worker_id": worker_id,
            "platform": platform,
            "status": status or "pending",
            "current_step": int(current_step),
            "total_steps": int(total_steps),
            "checkpoint_data": safe_checkpoint,
            "step_history": safe_history,
            "owner_id": owner_id,
            "created_at": now,
            "updated_at": now,
        }

        client = self._client()
        if client is not None:
            try:
                response = client.table(TABLE_NAME).insert(
                    self._to_db_row(record, owner_id=owner_id)
                ).execute()
                if response.data:
                    return {
                        "success": True,
                        "workflow": self._normalize_row(response.data[0], record),
                    }
            except Exception as exc:  # pragma: no cover - defensive fallback
                if self._durable_required:
                    return {"success": False, "error": f"Workflow persistence failed: {exc}"}

        if self._durable_required:
            return {"success": False, "error": "Durable workflow persistence is unavailable"}

        # Fallback to in-memory storage
        self._memory_store[workflow_id] = record
        return {"success": True, "workflow": self._normalize_row(None, record)}

    def get(self, workflow_id: str) -> dict[str, Any] | None:
        """Retrieve a workflow by id.

        Args:
            workflow_id: The workflow identifier.

        Returns:
            Normalized workflow dictionary or ``None`` if not found.
        """
        client = self._client()
        if client is not None:
            try:
                response = (
                    client.table(TABLE_NAME)
                    .select("*")
                    .eq("id", workflow_id)
                    .limit(1)
                    .execute()
                )
                rows = response.data or []
                if rows:
                    return self._normalize_row(rows[0], None)
            except Exception as exc:  # pragma: no cover - defensive fallback
                if self._durable_required:
                    raise RuntimeError(f"Workflow read failed: {exc}") from exc
            if self._durable_required:
                return None

        record = self._memory_store.get(workflow_id)
        if record is not None:
            return self._normalize_row(None, record)
        return None

    def update(
        self,
        workflow_id: str,
        *,
        status: str | None = None,
        current_step: int | None = None,
        checkpoint_data: dict[str, Any] | None = None,
        step_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update mutable fields on an existing workflow.

        Returns:
            Dictionary with ``success`` flag and the updated normalized record.
        """
        if not workflow_id:
            return {"success": False, "error": "workflow_id is required"}

        updates: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if status is not None:
            updates["status"] = status
        if current_step is not None:
            updates["current_step"] = int(current_step)
        if checkpoint_data is not None:
            updates["checkpoint_data"] = self._sanitize_json(checkpoint_data) or {}
        if step_history is not None:
            updates["step_history"] = self._sanitize_json(step_history) or []

        client = self._client()
        if client is not None:
            try:
                response = (
                    client.table(TABLE_NAME)
                    .update(updates)
                    .eq("id", workflow_id)
                    .execute()
                )
                rows = response.data or []
                if rows:
                    return {
                        "success": True,
                        "workflow": self._normalize_row(rows[0], None),
                    }
            except Exception as exc:  # pragma: no cover - defensive fallback
                if self._durable_required:
                    return {"success": False, "error": f"Workflow update failed: {exc}"}

            if self._durable_required:
                return {"success": False, "error": "Durable workflow update is unavailable"}

        # In-memory fallback
        existing = self._memory_store.get(workflow_id)
        if existing is None:
            return {
                "success": False,
                "error": f"Workflow {workflow_id} not found",
                "workflow_id": workflow_id,
            }
        existing.update(updates)
        self._memory_store[workflow_id] = existing
        return {
            "success": True,
            "workflow": self._normalize_row(None, existing),
        }

    def get_by_started_by_approval_id(
        self,
        approval_id: str,
    ) -> dict[str, Any] | None:
        """Retrieve a workflow previously started by the given approval.

        Returns the durable workflow bound to ``approval_id`` or ``None``
        when no such workflow exists. This is a read-only operation and
        never mutates state.

        When the database is unavailable the in-memory fallback is
        consulted. The in-memory fallback is not durable across process
        restarts; the database is the source of truth.
        """
        if not approval_id:
            return None

        client = self._client()
        if client is not None:
            try:
                response = (
                    client.table(TABLE_NAME)
                    .select("*")
                    .eq("started_by_approval_id", approval_id)
                    .limit(1)
                    .execute()
                )
                rows = response.data or []
                if rows:
                    return self._normalize_row(rows[0], None)
            except Exception as exc:  # pragma: no cover - defensive fallback
                if self._durable_required:
                    raise RuntimeError(f"Workflow read failed: {exc}") from exc
            if self._durable_required:
                return None

        for record in self._memory_store.values():
            if record.get("started_by_approval_id") == approval_id:
                return self._normalize_row(None, record)
        return None

    def get_or_create_for_approval(
        self,
        *,
        approval_id: str,
        workflow_id: str,
        mission_id: str | None,
        worker_id: str,
        platform: str,
        status: str,
        current_step: int,
        total_steps: int,
        checkpoint_data: dict[str, Any] | None,
        step_history: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Atomically return the existing workflow for an approval or create one.

        Contract:
          - Returns ``{"success": True, "workflow": <row>, "created": bool}``
            on success. ``created=True`` indicates this call inserted;
            ``created=False`` indicates an existing workflow was returned.
          - When the database is available, the partial unique index
            ``ux_onboarding_workflows_started_by_approval_id`` is the
            final correctness authority. Concurrent calls for the same
            ``approval_id`` will see exactly one inserted row.
          - When the database is unavailable, the in-memory fallback uses
            a process-local lock to avoid a TOCTOU race within a single
            process. This fallback is NOT durable across process
            restarts and is not a substitute for the database.

        Raises no exceptions on the happy path. Database errors other
        than a successful get-or-create degrade to the in-memory path;
        callers that require durable state should treat the returned
        ``created`` flag and the workflow's ``started_by_approval_id``
        field as the canonical record.
        """
        if not approval_id:
            return {"success": False, "error": "approval_id is required"}
        if not workflow_id:
            return {"success": False, "error": "workflow_id is required"}
        if not worker_id:
            return {"success": False, "error": "worker_id is required"}
        if not platform:
            return {"success": False, "error": "platform is required"}

        safe_checkpoint = self._sanitize_json(checkpoint_data) or {}
        safe_history = self._sanitize_json(step_history) or []
        now = datetime.now(timezone.utc).isoformat()

        client = self._client()
        if client is not None:
            try:
                response = client.rpc(
                    "claim_onboarding_workflow_for_approval",
                    {
                        "p_approval_id": approval_id,
                        "p_workflow_id": workflow_id,
                        "p_mission_id": mission_id,
                        "p_worker_id": worker_id,
                        "p_platform": platform,
                        "p_status": status or "pending",
                        "p_current_step": int(current_step),
                        "p_total_steps": int(total_steps),
                        "p_checkpoint_data": safe_checkpoint,
                        "p_step_history": safe_history,
                    },
                ).execute()
                data = getattr(response, "data", None) or []
                if data:
                    first = data[0] if isinstance(data[0], dict) else None
                    if first:
                        workflow_json = first.get("workflow")
                        created = bool(first.get("created"))
                        if workflow_json:
                            return {
                                "success": True,
                                "workflow": self._normalize_row(workflow_json, None),
                                "created": created,
                            }
            except Exception as exc:  # pragma: no cover - defensive fallback
                if self._durable_required:
                    return {"success": False, "error": f"Workflow claim failed: {exc}"}

            if self._durable_required:
                return {"success": False, "error": "Durable workflow claiming is unavailable"}

        # In-memory fallback. The lock makes the get-or-create atomic
        # within this process. A real concurrent process can still race
        # the in-memory path; the database is the durable authority.
        with self._memory_lock:
            for record in self._memory_store.values():
                if record.get("started_by_approval_id") == approval_id:
                    return {
                        "success": True,
                        "workflow": self._normalize_row(None, record),
                        "created": False,
                    }

            new_record: dict[str, Any] = {
                "workflow_id": workflow_id,
                "mission_id": mission_id,
                "worker_id": worker_id,
                "platform": platform,
                "status": status or "pending",
                "current_step": int(current_step),
                "total_steps": int(total_steps),
                "checkpoint_data": safe_checkpoint,
                "step_history": safe_history,
                "started_by_approval_id": approval_id,
                "created_at": now,
                "updated_at": now,
            }
            self._memory_store[workflow_id] = new_record
            return {
                "success": True,
                "workflow": self._normalize_row(None, new_record),
                "created": True,
            }

    def list_by_worker(
        self,
        worker_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List workflows for a worker.

        Args:
            worker_id: The worker identifier.
            status: Optional filter by workflow status.

        Returns:
            List of normalized workflow dictionaries.
        """
        results: list[dict[str, Any]] = []

        client = self._client()
        if client is not None:
            try:
                query = client.table(TABLE_NAME).select("*").eq("worker_id", worker_id)
                if status:
                    query = query.eq("status", status)
                response = query.execute()
                for row in response.data or []:
                    normalized = self._normalize_row(row, None)
                    if normalized:
                        results.append(normalized)
                return results
            except Exception as exc:  # pragma: no cover - defensive fallback
                if self._durable_required:
                    raise RuntimeError(f"Workflow list failed: {exc}") from exc
            if self._durable_required:
                return []

        for record in self._memory_store.values():
            if record.get("worker_id") != worker_id:
                continue
            if status and record.get("status") != status:
                continue
            results.append(self._normalize_row(None, record))
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

    @staticmethod
    def _sanitize_json(value: Any) -> Any:
        """Return a JSON-safe copy of a value, dropping sensitive keys."""

        sensitive_keys = {
            "access_token",
            "refresh_token",
            "authorization_code",
            "oauth_code",
            "client_secret",
            "api_key",
            "token",
            "password",
        }

        def _clean(item: Any) -> Any:
            if isinstance(item, dict):
                cleaned: dict[str, Any] = {}
                for key, val in item.items():
                    if key.lower() in sensitive_keys:
                        continue
                    cleaned[key] = _clean(val)
                return cleaned
            if isinstance(item, list):
                return [_clean(v) for v in item]
            return item

        return _clean(value)

    @staticmethod
    def _to_db_row(
        record: dict[str, Any],
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Convert a normalized record to a Supabase row payload."""
        row = {
            "id": record["workflow_id"],
            "mission_id": record.get("mission_id"),
            "worker_id": record["worker_id"],
            "platform": record["platform"],
            "status": record["status"],
            "current_step": record["current_step"],
            "total_steps": record["total_steps"],
            "checkpoint_data": record.get("checkpoint_data") or {},
            "step_history": record.get("step_history") or [],
        }
        if owner_id is not None:
            row["owner_id"] = owner_id
        approval_id = record.get("started_by_approval_id")
        if approval_id:
            row["started_by_approval_id"] = approval_id
        return row

    @staticmethod
    def _normalize_row(
        row: dict[str, Any] | None,
        fallback: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Produce a normalized dictionary that matches the connector contract."""
        if row is None and fallback is None:
            return None

        source = row if row is not None else fallback
        if source is None:
            return None

        checkpoint = source.get("checkpoint_data") or {}
        step_history = source.get("step_history") or []

        normalized = {
            "workflow_id": source.get("id") or source.get("workflow_id"),
            "mission_id": source.get("mission_id"),
            "worker_id": source.get("worker_id"),
            "platform": source.get("platform"),
            "status": source.get("status"),
            "current_step": source.get("current_step"),
            "total_steps": source.get("total_steps"),
            "checkpoint_data": checkpoint,
            "step_history": step_history,
            "started_by_approval_id": source.get("started_by_approval_id"),
            "created_at": source.get("created_at"),
            "updated_at": source.get("updated_at"),
        }
        owner_id = source.get("owner_id")
        if owner_id is None and fallback is not None:
            owner_id = fallback.get("owner_id")
        if owner_id is not None:
            normalized["owner_id"] = owner_id
        return normalized


__all__ = ["OnboardingWorkflowStore"]