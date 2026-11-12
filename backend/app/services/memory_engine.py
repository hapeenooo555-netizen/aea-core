"""Atlas memory persistence service for Sprint 3.

This module provides a lightweight service layer for storing and retrieving
worker memory entries in the Supabase-backed ``agent_memory_entries`` table.
The implementation is intentionally simple and does not include any AI or
reasoning logic.
"""

from __future__ import annotations

from typing import Any

try:
    from ..database import supabase_client
except ImportError:  # pragma: no cover - fallback for direct module execution
    from backend.app.database import supabase_client


class AtlasMemoryEngine:
    """Persist and query Atlas worker memory entries.

    The engine uses the shared Supabase client to interact with the
    ``agent_memory_entries`` table. Each operation returns a structured
    dictionary or a list of dictionaries so callers can handle the result
    consistently.
    """

    def __init__(self, client: Any | None = None, owner_id: str | None = None) -> None:
        """Initialize the memory engine with the shared Supabase client."""

        self._client = client if client is not None else supabase_client
        self._owner_id = owner_id

    def store_memory(
        self,
        worker_id: str,
        memory_type: str,
        content: dict[str, Any],
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Store a memory entry for a worker."""

        if not self._client:
            return {"success": False, "error": "Supabase client is not available"}

        effective_owner_id = owner_id if owner_id is not None else self._owner_id
        payload = self._to_storage_record(worker_id, memory_type, content, owner_id=effective_owner_id)

        try:
            response = self._client.table("agent_memory_entries").insert(payload).execute()
            record = self._from_storage_record(response.data[0]) if response.data else None
            return {"success": True, "memory": record}
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return {"success": False, "error": str(exc)}

    def get_recent_memories(self, worker_id: str, limit: int = 10, client: Any | None = None, owner_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieve recent memories scoped to the authenticated owner and worker."""

        db_client = client if client is not None else self._client
        effective_owner_id = owner_id if owner_id is not None else self._owner_id
        if not db_client or not effective_owner_id:
            return []

        try:
            response = (
                db_client.table("agent_memory_entries")
                .select("*")
                .eq("worker_id", worker_id)
                .eq("owner_id", effective_owner_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            rows = response.data or []
            return [self._from_storage_record(row) for row in rows]
        except Exception:  # pragma: no cover - defensive runtime handling
            return []

    def search_memories(self, keyword: str, client: Any | None = None, owner_id: str | None = None) -> list[dict[str, Any]]:
        """Search only the calling owner's memory entries."""

        db_client = client if client is not None else self._client
        effective_owner_id = owner_id if owner_id is not None else self._owner_id
        if not db_client or not effective_owner_id:
            return []

        try:
            response = db_client.table("agent_memory_entries").select("*").eq("owner_id", effective_owner_id).execute()
            rows = response.data or []
        except Exception:  # pragma: no cover - defensive runtime handling
            return []

        lowered_keyword = keyword.lower()
        matched_rows: list[dict[str, Any]] = []

        for row in rows:
            normalized = self._from_storage_record(row)
            if not normalized:
                continue

            haystack_parts = [
                str(normalized.get("memory_type", "")),
                str(normalized.get("content", "")),
                str(normalized.get("metadata", "")),
            ]
            haystack = " ".join(haystack_parts).lower()
            if lowered_keyword in haystack:
                matched_rows.append(normalized)

        return matched_rows

    def delete_memory(self, memory_id: str, client: Any | None = None, owner_id: str | None = None) -> dict[str, Any]:
        """Delete a memory entry only when its owner is explicitly scoped."""

        db_client = client if client is not None else self._client
        effective_owner_id = owner_id if owner_id is not None else self._owner_id
        if not effective_owner_id:
            return {"success": False, "memory_id": memory_id, "error": "owner_id is required to delete memory"}
        if not db_client:
            return {"success": False, "memory_id": memory_id, "error": "Supabase client is not available"}

        try:
            response = db_client.table("agent_memory_entries").delete().eq("id", memory_id).eq("owner_id", effective_owner_id).execute()
            return {"success": True, "memory_id": memory_id, "deleted": bool(response.data)}
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return {"success": False, "memory_id": memory_id, "error": str(exc)}

    def _to_storage_record(
        self,
        worker_id: str,
        memory_type: str,
        content: dict[str, Any] | None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Convert a public memory payload into a storage-ready adapter payload."""

        normalized_content = content if isinstance(content, dict) else {}
        metadata = normalized_content.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        importance_score = normalized_content.get("importance_score", 0.5)
        try:
            importance_score = float(importance_score)
        except (TypeError, ValueError):
            importance_score = 0.5

        record: dict[str, Any] = {
            "worker_id": worker_id,
            "mission_id": normalized_content.get("mission_id"),
            "memory_type": memory_type,
            "content": normalized_content,
            "metadata": metadata,
            "importance_score": importance_score,
        }
        if owner_id:
            record["owner_id"] = owner_id
        return record

    def _from_storage_record(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        """Normalize a Supabase row into a backward-compatible dictionary shape."""

        if not row:
            return None

        content = row.get("content") or {}
        if not isinstance(content, dict):
            content = {"value": content}

        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        importance_score = row.get("importance_score", 0.5)
        try:
            importance_score = float(importance_score)
        except (TypeError, ValueError):
            importance_score = 0.5

        return {
            "id": row.get("id"),
            "worker_id": row.get("worker_id"),
            "mission_id": row.get("mission_id"),
            "memory_type": row.get("memory_type"),
            "content": content,
            "metadata": metadata,
            "importance_score": importance_score,
            "created_at": row.get("created_at"),
        }


__all__ = ["AtlasMemoryEngine"]
