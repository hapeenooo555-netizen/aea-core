"""Mission persistence service for Sprint 3.1.

This module provides a lightweight service layer for creating, retrieving,
and updating mission records in the Supabase-backed ``missions`` table.
The implementation is intentionally simple and defensive so tests and local
runs continue to work even when the database client is unavailable.
"""

from __future__ import annotations

from typing import Any

try:
    from .. import database as database_module
except Exception:  # pragma: no cover - fallback for missing runtime config
    try:
        from backend.app import database as database_module
    except Exception:  # pragma: no cover - fallback for direct execution
        database_module = None


class MissionEngine:
    """Persist and query mission records with the shared Supabase client."""

    def __init__(self, client: Any | None = None) -> None:
        """Initialize the engine with the shared Supabase client."""

        self._client = client if client is not None else self._get_client()

    def create_mission(
        self,
        title: str,
        description: str,
        worker_id: str | None = None,
        priority: str = "normal",
        owner_id: str | None = None,
        client: Any | None = None,
    ) -> dict[str, Any]:
        """Create a new mission record.

        Args:
            title: The mission title.
            description: A descriptive mission summary.
            worker_id: An optional worker identifier to assign to the mission.
            priority: A textual priority such as ``high`` or ``normal``.
            owner_id: Canonical owner identifier (auth.users.id).
            client: Optional Supabase client. When ``None``, uses the shared client.

        Returns:
            A structured dictionary describing the operation result.
        """

        if not title.strip():
            return {"success": False, "error": "Mission title is required"}

        db_client = client if client is not None else self._client
        if not db_client:
            return {"success": False, "error": "Supabase client is not available"}

        payload: dict[str, Any] = {
            "title": title.strip(),
            "description": description or "",
            "priority": priority or "normal",
            "status": "pending",
        }

        if worker_id:
            payload["assigned_worker"] = worker_id

        if owner_id:
            payload["owner_id"] = owner_id

        try:
            response = self._client.table("missions").insert(payload).execute()
            record = self._normalize_mission(response.data[0]) if response.data else None
            return {"success": True, "mission": record}
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return {"success": False, "error": str(exc)}

    def get_mission(
        self,
        mission_id: str,
        owner_id: str | None = None,
        client: Any | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve a single mission by identifier.

        When ``owner_id`` is supplied the query is explicitly scoped to that
        owner in addition to the RLS policy, providing defence-in-depth against
        cross-user access.  When the authenticated user is not the owner the
        row is not returned.

        Args:
            mission_id: The unique mission identifier.
            owner_id: Canonical owner identifier (auth.users.id). When provided,
                the query is filtered to ``owner_id = auth.uid()``.
            client: Optional Supabase client. When ``None``, uses the shared client.

        Returns:
            A normalized mission dictionary when found, otherwise ``None``.
        """

        db_client = client if client is not None else self._client
        if not db_client:
            return None

        try:
            query = db_client.table("missions").select("*").eq("id", mission_id)
            if owner_id:
                query = query.eq("owner_id", owner_id)
            response = query.limit(1).execute()
            rows = response.data or []
            if not rows:
                return None
            return self._normalize_mission(rows[0])
        except Exception:  # pragma: no cover - defensive runtime handling
            return None

    def get_worker_missions(self, worker_id: str, limit: int = 20, client: Any | None = None) -> list[dict[str, Any]]:
        """List missions assigned to a worker.

        Args:
            worker_id: The worker identifier whose missions should be returned.
            limit: The maximum number of missions to retrieve.
            client: Optional Supabase client. When ``None``, uses the shared client.

        Returns:
            A list of normalized mission dictionaries ordered from newest to
            oldest.
        """

        db_client = client if client is not None else self._client
        if not db_client:
            return []

        try:
            response = (
                db_client.table("missions")
                .select("*")
                .eq("assigned_worker", worker_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            rows = response.data or []
            return [self._normalize_mission(row) for row in rows if self._normalize_mission(row)]
        except Exception:  # pragma: no cover - defensive runtime handling
            return []

    def update_status(self, mission_id: str, status: str, client: Any | None = None) -> dict[str, Any]:
        """Update the lifecycle status of a mission.

        Args:
            mission_id: The unique mission identifier.
            status: The next lifecycle status.
            client: Optional Supabase client. When ``None``, uses the shared client.

        Returns:
            A structured dictionary describing the operation result.
        """

        allowed_statuses = {"pending", "active", "completed", "failed"}
        if status not in allowed_statuses:
            return {"success": False, "error": f"Unsupported status: {status}"}

        db_client = client if client is not None else self._client
        if not db_client:
            return {"success": False, "error": "Supabase client is not available"}

        try:
            response = db_client.table("missions").update({"status": status}).eq("id", mission_id).execute()
            record = self._normalize_mission(response.data[0]) if response.data else None
            return {"success": True, "mission": record}
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return {"success": False, "error": str(exc)}

    def complete_mission(self, mission_id: str, result: dict[str, Any], client: Any | None = None) -> dict[str, Any]:
        """Mark a mission as completed and persist its execution result.

        Args:
            mission_id: The unique mission identifier.
            result: A dictionary payload describing the completed mission output.
            client: Optional Supabase client. When ``None``, uses the shared client.

        Returns:
            A structured dictionary describing the operation result.
        """

        db_client = client if client is not None else self._client
        if not db_client:
            return {"success": False, "error": "Supabase client is not available"}

        payload: dict[str, Any] = {
            "status": "completed",
            "result": result or {},
            "error": None,
        }

        try:
            response = db_client.table("missions").update(payload).eq("id", mission_id).execute()
            record = self._normalize_mission(response.data[0]) if response.data else None
            return {"success": True, "mission": record}
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return {"success": False, "error": str(exc)}

    def fail_mission(self, mission_id: str, error: str, client: Any | None = None) -> dict[str, Any]:
        """Mark a mission as failed and persist the error details.

        Args:
            mission_id: The unique mission identifier.
            error: A descriptive error message for the failed mission.
            client: Optional Supabase client. When ``None``, uses the shared client.

        Returns:
            A structured dictionary describing the operation result.
        """

        db_client = client if client is not None else self._client
        if not db_client:
            return {"success": False, "error": "Supabase client is not available"}

        payload: dict[str, Any] = {
            "status": "failed",
            "error": error or "Mission failed",
            "result": {},
        }

        try:
            response = db_client.table("missions").update(payload).eq("id", mission_id).execute()
            record = self._normalize_mission(response.data[0]) if response.data else None
            return {"success": True, "mission": record}
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return {"success": False, "error": str(exc)}

    def _get_client(self) -> Any | None:
        """Retrieve the shared Supabase client when it is available."""

        if database_module is None:
            return None

        try:
            if hasattr(database_module, "is_supabase_configured") and not database_module.is_supabase_configured():
                return None

            client = getattr(database_module, "supabase_client", None)
            if client:
                return client

            getter = getattr(database_module, "get_supabase_client", None)
            if callable(getter):
                return getter()
        except Exception:  # pragma: no cover - defensive runtime handling
            return None

        return None

    def _normalize_mission(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        """Normalize a Supabase mission row into a consistent dictionary."""

        if not row:
            return None

        worker_id = row.get("assigned_worker") or row.get("worker_id")
        return {
            "id": row.get("id"),
            "title": row.get("title"),
            "description": row.get("description"),
            "worker_id": worker_id,
            "assigned_worker": worker_id,
            "status": row.get("status"),
            "priority": row.get("priority"),
            "owner_id": row.get("owner_id"),
            "result": row.get("result") or {},
            "error": row.get("error"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }


__all__ = ["MissionEngine"]
