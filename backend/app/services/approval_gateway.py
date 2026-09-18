"""Approval gateway service for human approval workflows (P1-2).

This module provides the approval request management system for sensitive
actions that require human review before execution. P1-2 makes
``public.approval_requests`` the **authoritative source of truth** for all
persisted approval state, while still preserving a safe in-memory fallback
for tests and local environments where Supabase is genuinely unavailable.

P1-2 contract:

* Every persisted approval is owned by the database. A successful DB
  response is the truth; the in-memory dict is at most a write-through
  cache. The fallback never overrides a successful DB read.
* ``create_request()`` / ``get_request()`` / ``list_requests()`` are
  DB-first. The in-memory fallback is used only when Supabase is **not
  configured** (``database.is_supabase_configured()`` returns ``False``).
  Real DB errors are surfaced — they are not silently converted into
  "DB unavailable" and replayed against stale memory.
* State transitions are explicit and idempotent:

      pending   -> approved
      pending   -> rejected
      pending   -> expired

  ``approved``/``rejected``/``expired`` are terminal. A duplicate approve
  or reject of an already-terminal request returns the existing record
  with ``success=True`` and an ``idempotent=True`` marker, without
  creating a new approval or re-running any side effect.
* Transitions are guarded at the DB level using a single atomic
  ``UPDATE … WHERE id = ? AND status = expected`` against
  ``approval_requests``. The Supabase Python client does not expose a
  true ``RETURNING`` clause, so a successful update is detected by the
  presence of rows in the response data. When the update matches zero
  rows the gateway distinguishes "not found" from "concurrent
  modification" by re-reading the row.
* The original action payload/metadata is preserved across
  create/approve/reject. Sensitive fields (OAuth secrets, tokens,
  passwords, client secrets, etc.) are stripped from the payload before
  it is written to ``approval_requests.metadata`` using the same
  forbidden-key list as the connector layer.
* P1-1 compatibility is preserved: ``approve_request`` / ``reject_request``
  keep their existing return shape; ``ApprovalResumeService`` reads the
  approval through ``get_request`` and observes a normalized
  ``status``, ``expires_at``, ``payload``, ``mission_id``, ``action_type``
  exactly as before.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


TABLE_NAME = "approval_requests"

# Canonical approval states. The schema-level enum in the migration uses
# the same string values; we keep them centralised here so callers and
# tests can rely on a single source of truth.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"

ALL_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED)

# Forbidden OAuth / credential keys. Mirrors the list used by the
# Pinterest connector and the platform connection store so secrets are
# stripped from any payload before it is persisted to
# ``approval_requests.metadata``. The match is case-insensitive.
FORBIDDEN_METADATA_KEYS: frozenset[str] = frozenset({
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
    "code",
    "redirect_uri",
    "secret",
    "private_key",
    "cookie",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sanitize_metadata(payload: Any) -> dict[str, Any]:
    """Recursively strip forbidden OAuth/credential keys from a payload.

    The function walks dicts and lists and removes any key whose lower-cased
    name appears in :data:`FORBIDDEN_METADATA_KEYS`. Non-sensitive scalar
    values are returned as-is. The result is a new structure; the input
    is not mutated.
    """

    def _clean(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, val in value.items():
                if not isinstance(key, str):
                    continue
                if key.lower() in FORBIDDEN_METADATA_KEYS:
                    continue
                cleaned[key] = _clean(val)
            return cleaned
        if isinstance(value, list):
            return [_clean(v) for v in value]
        return value

    return _clean(payload) if isinstance(payload, dict) else {}


def _is_isoformat(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except Exception:
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plus_hours_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
class ApprovalRequest:
    """In-memory representation of an approval request.

    The class is preserved for backwards compatibility with existing
    callers (notably the Sprint 7.2-A test suite) that import
    ``ApprovalRequest`` directly. New code should treat approvals as
    dictionaries returned by :class:`ApprovalGateway`.
    """

    def __init__(
        self,
        request_id: str,
        mission_id: str,
        action_type: str,
        risk_level: str,
        payload: dict[str, Any],
        created_at: datetime | None = None,
        status: str = STATUS_PENDING,
        expires_at: datetime | None = None,
        approved_at: datetime | None = None,
        rejected_at: datetime | None = None,
        approved_by: str | None = None,
        rejected_by: str | None = None,
        rejection_reason: str | None = None,
        owner_id: str | None = None,
    ) -> None:
        self.request_id = request_id
        self.mission_id = mission_id
        self.action_type = action_type
        self.risk_level = risk_level
        self.payload = payload
        self.created_at = created_at or datetime.now(timezone.utc)
        self.status = status
        self.expires_at = expires_at or (self.created_at + timedelta(hours=24))
        self.approved_at = approved_at
        self.rejected_at = rejected_at
        self.approved_by = approved_by
        self.rejected_by = rejected_by
        self.rejection_reason = rejection_reason
        self.owner_id = owner_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.request_id,
            "mission_id": self.mission_id,
            "action_type": self.action_type,
            "risk_level": self.risk_level,
            "status": self.status,
            "payload": self.payload,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejected_at": self.rejected_at.isoformat() if self.rejected_at else None,
            "approved_by": self.approved_by,
            "rejected_by": self.rejected_by,
            "rejection_reason": self.rejection_reason,
        }


# ---------------------------------------------------------------------------
# Repository: thin wrapper over the Supabase client
# ---------------------------------------------------------------------------
class _ApprovalRepository:
    """Thin DB wrapper for the ``approval_requests`` table.

    The repository exposes a small, deliberate set of operations that
    return a structured ``(ok, row|reason, error)`` tuple so the
    :class:`ApprovalGateway` can distinguish:

    * successful read/write
    * row not found
    * concurrent modification (DB write matched zero rows because the
      row's status changed underneath us)
    * DB unavailable (Supabase is not configured)
    * real DB / query / auth error (must not be silently swallowed)
    """

    def __init__(self, client: Any | None, *, available: bool) -> None:
        self._client = client
        self._available = available  # True iff Supabase is configured

    @property
    def available(self) -> bool:
        return self._available and self._client is not None

    def select_by_id(self, request_id: str) -> dict[str, Any]:
        """Read a single row by id.

        Returns a dict with one of:
        ``{"ok": True, "row": {...}}``
        ``{"ok": False, "reason": "not_found"}``
        ``{"ok": False, "reason": "db_unavailable"}``
        ``{"ok": False, "reason": "db_error", "error": str}``
        """

        if not self.available:
            return {"ok": False, "reason": "db_unavailable"}

        try:
            response = (
                self._client.table(TABLE_NAME)
                .select("*")
                .eq("id", request_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # pragma: no cover - real DB error
            logger.exception("DB error reading approval %s", request_id)
            return {"ok": False, "reason": "db_error", "error": str(exc)}

        rows = response.data or []
        if not rows:
            return {"ok": False, "reason": "not_found"}
        return {"ok": True, "row": rows[0]}

    def select_list(
        self,
        *,
        mission_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "reason": "db_unavailable"}

        try:
            query = self._client.table(TABLE_NAME).select("*")
            if mission_id:
                query = query.eq("mission_id", mission_id)
            if status:
                query = query.eq("status", status)
            query = query.order("requested_at", desc=True).limit(limit)
            response = query.execute()
        except Exception as exc:  # pragma: no cover - real DB error
            logger.exception("DB error listing approvals")
            return {"ok": False, "reason": "db_error", "error": str(exc)}

        return {"ok": True, "rows": response.data or []}

    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "reason": "db_unavailable"}

        try:
            response = self._client.table(TABLE_NAME).insert(row).execute()
        except Exception as exc:  # pragma: no cover - real DB error
            logger.exception("DB error inserting approval")
            return {"ok": False, "reason": "db_error", "error": str(exc)}

        rows = response.data or []
        if not rows:
            return {"ok": False, "reason": "db_error", "error": "insert returned no rows"}
        return {"ok": True, "row": rows[0]}

    def update_status_if(
        self,
        request_id: str,
        *,
        expected_status: str,
        new_status: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomic compare-and-set on ``status``.

        Updates the row to ``new_status`` only when its current status is
        ``expected_status``. Returns:

        ``{"ok": True, "row": {...}}`` on success
        ``{"ok": False, "reason": "concurrent_modification"}`` when the
            update matched zero rows because the row's status changed
            underneath us (or because the row no longer exists)
        ``{"ok": False, "reason": "not_found"}`` when the row is absent
        ``{"ok": False, "reason": "db_unavailable"}`` when Supabase is
            not configured
        ``{"ok": False, "reason": "db_error", "error": str}`` on a real
            query / connection / auth error
        """

        if not self.available:
            return {"ok": False, "reason": "db_unavailable"}

        update_fields: dict[str, Any] = {
            "status": new_status,
            "updated_at": _now_iso(),
        }
        if extra:
            update_fields.update(extra)

        try:
            response = (
                self._client.table(TABLE_NAME)
                .update(update_fields)
                .eq("id", request_id)
                .eq("status", expected_status)
                .execute()
            )
        except Exception as exc:  # pragma: no cover - real DB error
            logger.exception(
                "DB error in compare-and-set %s -> %s", expected_status, new_status
            )
            return {"ok": False, "reason": "db_error", "error": str(exc)}

        rows = response.data or []
        if rows:
            return {"ok": True, "row": rows[0]}

        # The update matched zero rows. Disambiguate: was the row
        # missing, or did its current status differ from expected?
        probe = self.select_by_id(request_id)
        if probe.get("ok") is True:
            return {"ok": False, "reason": "concurrent_modification", "row": probe["row"]}
        if probe.get("reason") == "not_found":
            return {"ok": False, "reason": "not_found"}
        # Real DB error during probe.
        return {"ok": False, "reason": probe.get("reason", "db_error"), "error": probe.get("error")}

    def update_status(
        self,
        request_id: str,
        *,
        new_status: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Unconditional status update (used for the pending -> expired
        transition driven by the ``expires_at`` clock).

        Returns the same shape as :meth:`update_status_if` minus the
        ``concurrent_modification`` reason.
        """

        if not self.available:
            return {"ok": False, "reason": "db_unavailable"}

        update_fields: dict[str, Any] = {
            "status": new_status,
            "updated_at": _now_iso(),
        }
        if extra:
            update_fields.update(extra)

        try:
            response = (
                self._client.table(TABLE_NAME)
                .update(update_fields)
                .eq("id", request_id)
                .execute()
            )
        except Exception as exc:  # pragma: no cover - real DB error
            logger.exception("DB error updating approval status")
            return {"ok": False, "reason": "db_error", "error": str(exc)}

        rows = response.data or []
        if rows:
            return {"ok": True, "row": rows[0]}
        return {"ok": False, "reason": "not_found"}


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
class ApprovalGateway:
    """Manage approval requests backed by ``public.approval_requests``.

    Persistence rules (P1-2):

    1. When Supabase is configured, the DB is the source of truth. Every
       successful operation returns a dictionary derived from the DB row.
    2. When Supabase is not configured, the in-memory dict is used as a
       safe local-only fallback. It is never used to override a
       successful DB read on a different process.
    3. Real DB errors are not silently swallowed. Callers receive
       ``{"success": False, "error": "db_error: ..."}`` so failures are
       observable.
    """

    def __init__(self, client: Any | None = None) -> None:
        """Initialise the gateway.

        Args:
            client: Optional pre-resolved Supabase client. When ``None``,
                the gateway resolves ``app.database.supabase_client`` and
                :func:`app.database.is_supabase_configured` lazily.
        """

        self._explicit_client = client
        self._client = self._resolve_client()
        # In-memory dict used only when the DB is not configured or as a
        # write-through cache after a successful DB mutation.
        self._memory_store: dict[str, ApprovalRequest] = {}

    def _repo(self, client: Any | None = None) -> _ApprovalRepository:
        """Return a repository bound to the current client.

        Resolved on every call so callers (notably tests) that mutate
        ``self._client`` after construction observe the change.
        """

        effective_client = client if client is not None else self._client
        return _ApprovalRepository(effective_client, available=self._is_configured())

    def _repo_available(self) -> bool:
        if not self._is_configured():
            return False
        return self._client is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def create_request(
        self,
        mission_id: str,
        action_type: str,
        risk_level: str,
        payload: dict[str, Any],
        owner_id: str | None = None,
        ttl_hours: int = 24,
        client: Any | None = None,
    ) -> dict[str, Any]:
        """Create a new approval request.

        Args:
            mission_id: Associated mission identifier.
            action_type: Type of action requiring approval.
            risk_level: Risk level classification.
            payload: Action payload data.
            owner_id: Canonical owner identifier (auth.users.id).
            ttl_hours: Time-to-live in hours.
            client: Optional Supabase client. When ``None``, uses the shared client.

        Returns:
            Dictionary with success status and request details.
        """

        request_id = str(uuid4())
        now_iso = _now_iso()
        expires_iso = _plus_hours_iso(ttl_hours)
        safe_payload = _sanitize_metadata(payload)

        repo = self._repo(client)
        if repo.available:
            db_payload: dict[str, Any] = {
                "id": request_id,
                "mission_id": mission_id,
                "action_type": action_type,
                "risk_level": risk_level,
                "status": STATUS_PENDING,
                "requested_at": now_iso,
                "expires_at": expires_iso,
                "metadata": safe_payload,
            }
            if owner_id:
                db_payload["owner_id"] = owner_id
            result = repo.insert(db_payload)
            if result.get("ok") is True:
                normalized = self._normalize_request(result["row"])
                # Write-through cache so subsequent in-process calls
                # don't need to round-trip to the DB.
                self._write_through(normalized)
                return {"success": True, "request": normalized}
            return {
                "success": False,
                "error": f"db_error: {result.get('error', result.get('reason', 'unknown'))}",
            }

        # DB not configured: in-memory fallback path.
        request = ApprovalRequest(
            request_id=request_id,
            mission_id=mission_id,
            action_type=action_type,
            risk_level=risk_level,
            payload=safe_payload,
            expires_at=datetime.fromisoformat(expires_iso),
        )
        self._memory_store[request_id] = request
        return {"success": True, "request": request.to_dict()}

    def get_request(self, request_id: str, client: Any | None = None) -> dict[str, Any] | None:
        """Retrieve an approval request by ID.

        The DB is consulted first when configured. The in-memory dict is
        only consulted when the DB is not configured. Real DB errors are
        not converted into "DB unavailable" — they propagate to the
        caller as ``None`` (so the existing ``approval not found``
        contract is preserved) and a warning is logged.
        """

        repo = self._repo(client)
        if repo.available:
            result = repo.select_by_id(request_id)
            if result.get("ok") is True:
                normalized = self._normalize_request(result["row"])
                self._write_through(normalized)
                return normalized
            reason = result.get("reason")
            if reason == "not_found":
                return None
            if reason == "db_error":
                logger.error(
                    "ApprovalGateway.get_request: DB error for %s: %s",
                    request_id,
                    result.get("error"),
                )
                return None
            # db_unavailable falls through to memory as a last-resort hint.
            return self._get_from_memory(request_id)

        return self._get_from_memory(request_id)

    def list_requests(
        self,
        mission_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        client: Any | None = None,
    ) -> list[dict[str, Any]]:
        """List approval requests with optional filtering.

        DB-first when configured. When the DB is not configured, only the
        local in-memory dict is consulted. When the DB call returns a
        real error, the gateway returns an empty list and logs the
        error so callers do not silently see stale memory.
        """

        repo = self._repo(client)
        if repo.available:
            result = repo.select_list(
                mission_id=mission_id, status=status, limit=limit
            )
            if result.get("ok") is True:
                rows = [self._normalize_request(r) for r in result.get("rows", [])]
                rows = [r for r in rows if r is not None]
                for r in rows:
                    self._write_through(r)
                return rows
            reason = result.get("reason")
            if reason == "db_error":
                logger.error(
                    "ApprovalGateway.list_requests: DB error: %s",
                    result.get("error"),
                )
                return []
            # db_unavailable falls through to memory below.
            return self._list_from_memory(mission_id=mission_id, status=status, limit=limit)

        return self._list_from_memory(mission_id=mission_id, status=status, limit=limit)

    def approve_request(
        self,
        request_id: str,
        approved_by: str | None = None,
        client: Any | None = None,
    ) -> dict[str, Any]:
        """Approve a pending approval request.

        The transition is performed atomically against the DB using a
        ``WHERE id = ? AND status = 'pending'`` guard. Idempotent
        re-approval of an already-``approved`` request returns the
        existing record with ``success=True`` and ``idempotent=True``
        without performing a second state mutation. Any other terminal
        state (``rejected``/``expired``) is rejected as an invalid
        transition.
        """

        if not request_id:
            return {"success": False, "error": "request_id is required"}

        now_iso = _now_iso()
        extra = {
            "approved_at": now_iso,
            "approved_by": approved_by,
        }

        if self._repo(client).available:
            return self._transition(
                request_id=request_id,
                from_status=STATUS_PENDING,
                to_status=STATUS_APPROVED,
                extra=extra,
                on_terminal_idempotent=STATUS_APPROVED,
                operation="approve",
                actor=approved_by,
                client=client,
            )

        # DB not configured: operate on the in-memory dict.
        return self._approve_in_memory(request_id, approved_by)

    def reject_request(
        self,
        request_id: str,
        reason: str = "",
        rejected_by: str | None = None,
        client: Any | None = None,
    ) -> dict[str, Any]:
        """Reject a pending approval request.

        The transition is performed atomically against the DB using a
        ``WHERE id = ? AND status = 'pending'`` guard. Idempotent
        re-rejection of an already-``rejected`` request returns the
        existing record with ``success=True`` and ``idempotent=True``.
        Any other terminal state (``approved``/``expired``) is rejected
        as an invalid transition.
        """

        if not request_id:
            return {"success": False, "error": "request_id is required"}

        now_iso = _now_iso()
        extra = {
            "rejected_at": now_iso,
            "rejected_by": rejected_by,
            "reason": reason,
        }

        if self._repo(client).available:
            return self._transition(
                request_id=request_id,
                from_status=STATUS_PENDING,
                to_status=STATUS_REJECTED,
                extra=extra,
                on_terminal_idempotent=STATUS_REJECTED,
                operation="reject",
                actor=rejected_by,
                reason=reason,
                client=client,
            )

        return self._reject_in_memory(request_id, reason, rejected_by)

    def is_approved(self, request_id: str) -> bool:
        request = self.get_request(request_id)
        if not request:
            return False
        return (request.get("status") or "").lower() == STATUS_APPROVED

    def is_expired(self, request_id: str) -> bool:
        """Check whether a request has expired.

        The clock is read against the authoritative record. When the
        clock has crossed ``expires_at`` and the request is still
        ``pending``, the gateway atomically transitions the row to
        ``expired`` so subsequent reads return the terminal state.
        """

        request = self.get_request(request_id)
        if not request:
            return False

        if (request.get("status") or "").lower() == STATUS_EXPIRED:
            return True

        expires_at = request.get("expires_at")
        if not _is_isoformat(expires_at):
            return False

        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) <= exp_dt:
                return False
        except Exception:
            return False

        # Transition pending -> expired. Atomic at the DB layer.
        if self._repo().available and (request.get("status") or "").lower() == STATUS_PENDING:
            result = self._repo().update_status_if(
                request_id,
                expected_status=STATUS_PENDING,
                new_status=STATUS_EXPIRED,
            )
            if result.get("ok") is True:
                normalized = self._normalize_request(result["row"])
                self._write_through(normalized)
                return True
            # If the row has already moved to a different state, the
            # authoritative status still answers the question.
            if result.get("reason") == "concurrent_modification":
                current = (result.get("row") or {}).get("status")
                return current == STATUS_EXPIRED
            # db_error or db_unavailable: fall through and trust the
            # clock check (the row is past its expiry in wall time).
            return True

        if not self._repo().available:
            # Memory fallback
            self._mark_expired_in_memory(request_id)
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _resolve_client(self) -> Any | None:
        if self._explicit_client is not None:
            return self._explicit_client
        if database_module is None:
            return None
        return getattr(database_module, "supabase_client", None)

    def _is_configured(self) -> bool:
        # An explicit client (passed at construction) is always considered
        # configured, even when the Supabase environment is missing.
        if self._explicit_client is not None:
            return True
        # Tests (and a small number of callers) may inject a client after
        # construction by mutating ``self._client`` directly. Honour that
        # explicit assignment as authoritative configuration.
        if self._client is not None:
            return True
        if database_module is None:
            return False
        checker = getattr(database_module, "is_supabase_configured", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # pragma: no cover - defensive
                return False
        # Backwards compatibility: if the helper isn't available, fall
        # back to the legacy attribute check.
        return getattr(database_module, "supabase_client", None) is not None

    def _write_through(self, normalized: dict[str, Any]) -> None:
        """Mirror a DB record into the in-memory cache."""
        try:
            record = ApprovalRequest(
                request_id=normalized["id"],
                mission_id=normalized.get("mission_id") or "",
                action_type=normalized.get("action_type") or "",
                risk_level=normalized.get("risk_level") or "",
                payload=normalized.get("payload") or {},
                status=normalized.get("status") or STATUS_PENDING,
                created_at=(
                    datetime.fromisoformat(normalized["created_at"])
                    if _is_isoformat(normalized.get("created_at"))
                    else None
                ),
                expires_at=(
                    datetime.fromisoformat(normalized["expires_at"])
                    if _is_isoformat(normalized.get("expires_at"))
                    else None
                ),
                approved_at=(
                    datetime.fromisoformat(normalized["approved_at"])
                    if _is_isoformat(normalized.get("approved_at"))
                    else None
                ),
                rejected_at=(
                    datetime.fromisoformat(normalized["rejected_at"])
                    if _is_isoformat(normalized.get("rejected_at"))
                    else None
                ),
                approved_by=normalized.get("approved_by"),
                rejected_by=normalized.get("rejected_by"),
                rejection_reason=normalized.get("rejection_reason"),
                owner_id=normalized.get("owner_id"),
            )
            self._memory_store[record.request_id] = record
        except Exception:  # pragma: no cover - defensive
            logger.exception("ApprovalGateway._write_through: failed for %s", normalized.get("id"))

    def _get_from_memory(self, request_id: str) -> dict[str, Any] | None:
        record = self._memory_store.get(request_id)
        return record.to_dict() if record else None

    def _list_from_memory(
        self,
        *,
        mission_id: str | None,
        status: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for record in self._memory_store.values():
            d = record.to_dict()
            if mission_id and d.get("mission_id") != mission_id:
                continue
            if status and d.get("status") != status:
                continue
            results.append(d)
        results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return results[:limit]

    def _transition(
        self,
        *,
        request_id: str,
        from_status: str,
        to_status: str,
        extra: dict[str, Any],
        on_terminal_idempotent: str,
        operation: str,
        actor: str | None = None,
        reason: str | None = None,
        client: Any | None = None,
    ) -> dict[str, Any]:
        """Atomically transition a DB row from ``from_status`` to ``to_status``."""

        result = self._repo(client).update_status_if(
            request_id,
            expected_status=from_status,
            new_status=to_status,
            extra=extra,
        )

        if result.get("ok") is True:
            normalized = self._normalize_request(result["row"])
            self._write_through(normalized)
            return {"success": True, "request": normalized}

        reason_code = result.get("reason")
        if reason_code == "db_error":
            return {
                "success": False,
                "error": f"db_error: {result.get('error', 'unknown')}",
            }
        if reason_code == "db_unavailable":
            return {
                "success": False,
                "error": "db_unavailable",
            }
        if reason_code == "not_found":
            return {"success": False, "error": f"Request {request_id} not found"}

        # reason_code == "concurrent_modification": the row's current
        # status did not match ``from_status``. Inspect the row to
        # decide between "idempotent" and "invalid transition".
        current_row = result.get("row") or {}
        current_status = (current_row.get("status") or "").lower()
        if current_status == on_terminal_idempotent:
            normalized = self._normalize_request(current_row)
            self._write_through(normalized)
            return {
                "success": True,
                "request": normalized,
                "idempotent": True,
            }
        # Some other terminal/non-terminal state: invalid transition.
        if current_status in (STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED):
            return {
                "success": False,
                "error": (
                    f"Cannot {operation} request in state '{current_status}'; "
                    f"expected '{from_status}'"
                ),
            }
        # Unknown state — surface a clear error.
        return {
            "success": False,
            "error": (
                f"Request is in unexpected state '{current_status}'; cannot {operation}"
            ),
        }

    def _approve_in_memory(
        self, request_id: str, approved_by: str | None
    ) -> dict[str, Any]:
        record = self._memory_store.get(request_id)
        if not record:
            return {"success": False, "error": f"Request {request_id} not found"}
        if record.status == STATUS_APPROVED:
            return {
                "success": True,
                "request": record.to_dict(),
                "idempotent": True,
            }
        if record.status != STATUS_PENDING:
            return {
                "success": False,
                "error": (
                    f"Cannot approve request in state '{record.status}'; "
                    f"expected 'pending'"
                ),
            }
        now = datetime.now(timezone.utc)
        record.status = STATUS_APPROVED
        record.approved_at = now
        record.approved_by = approved_by
        self._memory_store[request_id] = record
        return {"success": True, "request": record.to_dict()}

    def _reject_in_memory(
        self, request_id: str, reason: str, rejected_by: str | None
    ) -> dict[str, Any]:
        record = self._memory_store.get(request_id)
        if not record:
            return {"success": False, "error": f"Request {request_id} not found"}
        if record.status == STATUS_REJECTED:
            return {
                "success": True,
                "request": record.to_dict(),
                "idempotent": True,
            }
        if record.status != STATUS_PENDING:
            return {
                "success": False,
                "error": (
                    f"Cannot reject request in state '{record.status}'; "
                    f"expected 'pending'"
                ),
            }
        now = datetime.now(timezone.utc)
        record.status = STATUS_REJECTED
        record.rejected_at = now
        record.rejection_reason = reason
        record.rejected_by = rejected_by
        self._memory_store[request_id] = record
        return {"success": True, "request": record.to_dict()}

    def _mark_expired_in_memory(self, request_id: str) -> None:
        record = self._memory_store.get(request_id)
        if record and record.status == STATUS_PENDING:
            record.status = STATUS_EXPIRED
            self._memory_store[request_id] = record

    @staticmethod
    def _normalize_request(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a database row into the gateway's standard shape.

        The normalized dictionary always includes the keys consumed by
        :class:`ApprovalResumeService` (``id``, ``status``, ``mission_id``,
        ``action_type``, ``payload``, ``expires_at``). The ``payload``
        key is sourced from the ``metadata`` JSONB column.
        """

        created_at = row.get("created_at") or row.get("requested_at")
        return {
            "id": row.get("id"),
            "mission_id": row.get("mission_id"),
            "action_type": row.get("action_type"),
            "risk_level": row.get("risk_level"),
            "status": row.get("status", STATUS_PENDING),
            "payload": row.get("metadata") or {},
            "owner_id": row.get("owner_id"),
            "created_at": created_at,
            "expires_at": row.get("expires_at"),
            "approved_at": row.get("approved_at"),
            "rejected_at": row.get("rejected_at"),
            "approved_by": row.get("approved_by"),
            "rejected_by": row.get("rejected_by"),
            "rejection_reason": row.get("reason"),
        }


__all__ = [
    "ApprovalGateway",
    "ApprovalRequest",
    "STATUS_PENDING",
    "STATUS_APPROVED",
    "STATUS_REJECTED",
    "STATUS_EXPIRED",
    "ALL_STATUSES",
    "FORBIDDEN_METADATA_KEYS",
]
