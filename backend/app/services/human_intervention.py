"""Human intervention workflow for platform onboarding in Sprint 7.2-B.

This module provides structured checkpoint management for cases where human
participation is required (OTP, CAPTCHA, email verification, OAuth, identity
verification, KYC, payment confirmation, etc.).

Checkpoints integrate with the existing approval system where possible, but
also track workflow-specific state and metadata.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

try:
    from .. import database as database_module
except Exception:  # pragma: no cover - fallback for missing runtime config
    try:
        from backend.app import database as database_module
    except Exception:  # pragma: no cover - fallback for direct execution
        database_module = None


class HumanInterventionCheckpoint:
    """Represents a human intervention checkpoint in a workflow."""

    def __init__(
        self,
        checkpoint_id: str,
        mission_id: str,
        platform: str,
        checkpoint_type: str,
        status: str = "awaiting_human",
        instructions: str = "",
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
        expires_at: datetime | None = None,
    ):
        """Initialize a human intervention checkpoint.

        Args:
            checkpoint_id: Unique checkpoint identifier.
            mission_id: Associated mission identifier.
            platform: Platform name (e.g., 'pinterest').
            checkpoint_type: Type of checkpoint (otp_required, oauth_authorization_required, etc.).
            status: Current status (awaiting_human, completed, failed, expired).
            instructions: Human-readable instructions for completion.
            metadata: Additional context for the checkpoint.
            created_at: When the checkpoint was created.
            completed_at: When the checkpoint was completed.
            expires_at: When the checkpoint expires (default 24 hours).
        """
        self.checkpoint_id = checkpoint_id
        self.mission_id = mission_id
        self.platform = platform
        self.checkpoint_type = checkpoint_type
        self.status = status
        self.instructions = instructions
        self.metadata = metadata or {}
        self.created_at = created_at or datetime.now(timezone.utc)
        self.completed_at = completed_at
        self.expires_at = expires_at or (self.created_at + timedelta(hours=24))

    def to_dict(self) -> dict[str, Any]:
        """Convert checkpoint to dictionary.

        Returns:
            Dictionary representation of the checkpoint.
        """
        return {
            "id": self.checkpoint_id,
            "mission_id": self.mission_id,
            "platform": self.platform,
            "checkpoint_type": self.checkpoint_type,
            "status": self.status,
            "instructions": self.instructions,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "expires_at": self.expires_at.isoformat(),
        }

    def is_expired(self) -> bool:
        """Check if the checkpoint has expired.

        Returns:
            True if checkpoint is past expiration time.
        """
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) > self.expires_at


class HumanInterventionManager:
    """Manage human intervention checkpoints for platform workflows."""

    # Supported checkpoint types with descriptions
    CHECKPOINT_TYPES = {
        "otp_required": "One-time password verification required",
        "captcha_required": "CAPTCHA verification required",
        "email_verification_required": "Email verification required",
        "oauth_authorization_required": "OAuth authorization required",
        "identity_verification_required": "Identity verification required",
        "kyc_required": "KYC (Know Your Customer) verification required",
        "payment_confirmation_required": "Payment confirmation required",
        "manual_platform_step_required": "Manual step on platform required",
    }

    def __init__(self, client: Any | None = None) -> None:
        """Initialize the human intervention manager.

        Args:
            client: Optional pre-resolved Supabase client. When ``None`` the
                manager resolves ``app.database.supabase_client`` lazily.
                Passing a user-scoped client (see
                :func:`app.database.get_supabase_client_for_user`) ensures
                RLS policies enforce row-level ownership on every checkpoint
                operation.
        """
        self._explicit_client = client
        self._client = self._get_client()
        # In-memory store for checkpoints (for testing and fallback)
        self._memory_store: dict[str, HumanInterventionCheckpoint] = {}

    def create_checkpoint(
        self,
        mission_id: str,
        platform: str,
        checkpoint_type: str,
        instructions: str,
        metadata: dict[str, Any] | None = None,
        ttl_hours: int = 24,
    ) -> dict[str, Any]:
        """Create a new human intervention checkpoint.

        Args:
            mission_id: Associated mission identifier.
            platform: Platform name.
            checkpoint_type: Type of checkpoint.
            instructions: Human-readable instructions.
            metadata: Additional context.
            ttl_hours: Time-to-live in hours.

        Returns:
            Dictionary with success status and checkpoint details.
        """
        if checkpoint_type not in self.CHECKPOINT_TYPES:
            return {
                "success": False,
                "error": f"Unsupported checkpoint type: {checkpoint_type}",
            }

        checkpoint_id = str(uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=ttl_hours)

        checkpoint = HumanInterventionCheckpoint(
            checkpoint_id=checkpoint_id,
            mission_id=mission_id,
            platform=platform,
            checkpoint_type=checkpoint_type,
            status="awaiting_human",
            instructions=instructions,
            metadata=metadata or {},
            created_at=now,
            expires_at=expires_at,
        )

        # Try to persist to database
        if self._client:
            try:
                db_payload = {
                    "id": checkpoint_id,
                    "mission_id": mission_id,
                    "platform": platform,
                    "checkpoint_type": checkpoint_type,
                    "status": "awaiting_human",
                    "instructions": instructions,
                    "metadata": metadata or {},
                    "created_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                }
                response = self._client.table("human_intervention_checkpoints").insert(db_payload).execute()
                if response.data:
                    return {"success": True, "checkpoint": checkpoint.to_dict()}
            except Exception:  # pragma: no cover - defensive fallback
                pass

        # Store in memory as fallback
        self._memory_store[checkpoint_id] = checkpoint
        return {"success": True, "checkpoint": checkpoint.to_dict()}

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        """Retrieve a human intervention checkpoint by ID.

        Args:
            checkpoint_id: The checkpoint identifier.

        Returns:
            Checkpoint details or None if not found.
        """
        # Check database first
        if self._client:
            try:
                response = (
                    self._client.table("human_intervention_checkpoints")
                    .select("*")
                    .eq("id", checkpoint_id)
                    .limit(1)
                    .execute()
                )
                if response.data:
                    row = response.data[0]
                    return self._normalize_checkpoint(row)
            except Exception:  # pragma: no cover - defensive fallback
                pass

        # Check memory store
        checkpoint = self._memory_store.get(checkpoint_id)
        if checkpoint:
            return checkpoint.to_dict()

        return None

    def find_checkpoint_by_oauth_state(
        self,
        oauth_state: str,
        owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Find a pending checkpoint by its OAuth state stored in metadata.

        Queries ``human_intervention_checkpoints`` for rows where
        ``status = 'awaiting_human'`` and ``metadata.oauth_state`` matches.
        When ``owner_id`` is supplied, also joins through ``missions`` to
        verify ownership at the query level.

        Falls back to an in-memory scan when the database is unavailable.

        Args:
            oauth_state: The CSRF state token to look up.
            owner_id: Optional owner identity for ownership scoping.

        Returns:
            Normalized checkpoint dict or None.
        """
        if not oauth_state:
            return None

        if self._client:
            try:
                query = (
                    self._client.table("human_intervention_checkpoints")
                    .select("*")
                    .eq("status", "awaiting_human")
                    .eq("metadata->oauth_state", oauth_state)
                )
                if owner_id:
                    query = query.or_(
                        f"and(mission_id,in:(select id from missions where owner_id.eq.{owner_id}))"
                    )
                response = query.limit(1).execute()
                rows = response.data or []
                if rows:
                    return self._normalize_checkpoint(rows[0])
            except Exception:  # pragma: no cover - defensive fallback
                pass

        # In-memory fallback
        for checkpoint in self._memory_store.values():
            if checkpoint.status != "awaiting_human":
                continue
            md = checkpoint.metadata or {}
            if md.get("oauth_state") == oauth_state:
                return checkpoint.to_dict()

        return None

    def list_pending_checkpoints(
        self,
        mission_id: str | None = None,
        platform: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List pending human intervention checkpoints.

        Args:
            mission_id: Optional filter by mission ID.
            platform: Optional filter by platform.
            limit: Maximum number of results.

        Returns:
            List of checkpoint dictionaries.
        """
        results = []

        # Try database first
        if self._client:
            try:
                query = self._client.table("human_intervention_checkpoints").select("*").eq("status", "awaiting_human")

                if mission_id:
                    query = query.eq("mission_id", mission_id)
                if platform:
                    query = query.eq("platform", platform)

                response = query.limit(limit).execute()
                for row in response.data or []:
                    normalized = self._normalize_checkpoint(row)
                    if normalized:
                        results.append(normalized)

                return results
            except Exception:  # pragma: no cover - defensive fallback
                pass

        # Fall back to memory store
        for checkpoint in self._memory_store.values():
            if checkpoint.status != "awaiting_human":
                continue
            if mission_id and checkpoint.mission_id != mission_id:
                continue
            if platform and checkpoint.platform != platform:
                continue
            results.append(checkpoint.to_dict())

        return results[:limit]

    def complete_checkpoint(
        self,
        checkpoint_id: str,
        human_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mark a checkpoint as completed by the human.

        Args:
            checkpoint_id: The checkpoint identifier.
            human_input: Data provided by the human completing the checkpoint.

        Returns:
            Dictionary with success status and updated checkpoint.
        """
        # Get the checkpoint
        checkpoint_data = self.get_checkpoint(checkpoint_id)
        if not checkpoint_data:
            return {
                "success": False,
                "error": f"Checkpoint {checkpoint_id} not found",
            }

        now = datetime.now(timezone.utc)

        # Update in database if available
        if self._client:
            try:
                response = (
                    self._client.table("human_intervention_checkpoints")
                    .update({
                        "status": "completed",
                        "completed_at": now.isoformat(),
                        "metadata": {
                            **checkpoint_data.get("metadata", {}),
                            "human_input_provided": True,
                        },
                    })
                    .eq("id", checkpoint_id)
                    .execute()
                )
                if response.data:
                    return {"success": True, "checkpoint": self._normalize_checkpoint(response.data[0])}
            except Exception:  # pragma: no cover - defensive fallback
                pass

        # Update in memory store
        if checkpoint_id in self._memory_store:
            checkpoint = self._memory_store[checkpoint_id]
            checkpoint.status = "completed"
            checkpoint.completed_at = now
            if human_input:
                checkpoint.metadata["human_input_provided"] = True
            return {"success": True, "checkpoint": checkpoint.to_dict()}

        return {
            "success": False,
            "error": "Failed to update checkpoint",
            "checkpoint_id": checkpoint_id,
        }

    def fail_checkpoint(
        self,
        checkpoint_id: str,
        reason: str = "User did not complete the required step",
    ) -> dict[str, Any]:
        """Mark a checkpoint as failed.

        Args:
            checkpoint_id: The checkpoint identifier.
            reason: Reason for failure.

        Returns:
            Dictionary with success status and updated checkpoint.
        """
        # Get the checkpoint
        checkpoint_data = self.get_checkpoint(checkpoint_id)
        if not checkpoint_data:
            return {
                "success": False,
                "error": f"Checkpoint {checkpoint_id} not found",
            }

        now = datetime.now(timezone.utc)

        # Update in database if available
        if self._client:
            try:
                response = (
                    self._client.table("human_intervention_checkpoints")
                    .update({
                        "status": "failed",
                        "completed_at": now.isoformat(),
                        "metadata": {
                            **checkpoint_data.get("metadata", {}),
                            "failure_reason": reason,
                        },
                    })
                    .eq("id", checkpoint_id)
                    .execute()
                )
                if response.data:
                    return {"success": True, "checkpoint": self._normalize_checkpoint(response.data[0])}
            except Exception:  # pragma: no cover - defensive fallback
                pass

        # Update in memory store
        if checkpoint_id in self._memory_store:
            checkpoint = self._memory_store[checkpoint_id]
            checkpoint.status = "failed"
            checkpoint.completed_at = now
            checkpoint.metadata["failure_reason"] = reason
            return {"success": True, "checkpoint": checkpoint.to_dict()}

        return {
            "success": False,
            "error": "Failed to update checkpoint",
            "checkpoint_id": checkpoint_id,
        }

    def _normalize_checkpoint(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        """Normalize a database row into a checkpoint dictionary.

        Args:
            row: Database row.

        Returns:
            Normalized checkpoint dictionary or None.
        """
        if not row:
            return None

        return {
            "id": row.get("id"),
            "mission_id": row.get("mission_id"),
            "platform": row.get("platform"),
            "checkpoint_type": row.get("checkpoint_type"),
            "status": row.get("status", "awaiting_human"),
            "instructions": row.get("instructions", ""),
            "metadata": row.get("metadata") or {},
            "created_at": row.get("created_at"),
            "completed_at": row.get("completed_at"),
            "expires_at": row.get("expires_at"),
        }

    def _get_client(self) -> Any:
        """Get the Supabase client.

        Returns:
            Supabase client or None. If an explicit (typically user-scoped)
            client was injected at construction time it is used so that RLS
            evaluates against the owning user identity. Otherwise the global
            ``supabase_client`` is returned as a fallback.
        """
        if self._explicit_client is not None:
            return self._explicit_client
        if database_module:
            return getattr(database_module, "supabase_client", None)
        return None
