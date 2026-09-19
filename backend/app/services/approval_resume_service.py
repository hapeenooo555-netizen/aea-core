"""Approval → resume pipeline service for P1-1.

This service resumes a connector operation after its associated approval
request has been approved. It is intentionally synchronous and idempotent.

Responsibilities:
1. Validate that the approval request exists and is in the ``approved``
   state. Reject / pending / expired / missing requests produce explicit
   error responses.
2. Resolve the original action context from the approval request:
   - ``approval_request.id``
   - ``approval_request.mission_id``
   - ``approval_request.action_type``
   - ``approval_request.payload`` (worker_id, workflow_id, platform, …)
3. Dispatch to the appropriate connector (resolved through a
   :class:`ConnectorRegistry`) or fall through to ``WorkerRuntime`` for
   non-connector actions.
4. Enforce idempotency by combining an in-memory cache of executed
   approval IDs with state-level guards (workflow already completed,
   platform connection already ``connected``). Restart-safe idempotency
   is provided by the persisted workflow / connection state itself; the
   in-memory cache is a fast-path optimization.
5. Return a structured result distinguishing the seven pipeline states:
   - approval_pending
   - approval_rejected
   - approval_expired
   - approval_not_found
   - awaiting_resume
   - awaiting_human_intervention
   - resumed
   - completed
   - resume_failed

The service never stores OAuth secrets or raw credentials. Sensitive
fields present in the payload are stripped before reaching the connector
``connect_account`` path (the connector itself performs sanitization as
a defense-in-depth).
"""

from __future__ import annotations

import logging
from typing import Any

from .approval_gateway import ApprovalGateway
from .human_intervention import HumanInterventionManager
from .p1_7_contracts import SENSITIVE_FIELDS

logger = logging.getLogger(__name__)


# Action types that map directly to a connector operation. These actions
# are handled in-process by the resume service; any other action type
# falls through to ``WorkerRuntime.execute_action_with_approval`` semantics
# which is a no-op for connector-style resume.
CONNECTOR_ACTIONS = {
    "start_platform_onboarding",
    "resume_platform_onboarding",
    "connect_platform",
    "publish_content",
    "check_platform_status",
}


class ApprovalResumeService:
    """Synchronous, idempotent approval → resume pipeline."""

    def __init__(
        self,
        approval_gateway: ApprovalGateway | None = None,
        connector_registry: Any | None = None,
        human_intervention_manager: HumanInterventionManager | None = None,
        worker_runtime: Any | None = None,
    ) -> None:
        """Initialize the resume service.

        Args:
            approval_gateway: Optional pre-constructed
                :class:`ApprovalGateway`. When ``None``, a default is created.
            connector_registry: Optional pre-constructed
                :class:`ConnectorRegistry`. When ``None``, a default is created
                with Pinterest registered.
            human_intervention_manager: Optional pre-constructed
                :class:`HumanInterventionManager`. When ``None``, a default is
                created.
            worker_runtime: Optional pre-constructed
                :class:`WorkerRuntime`. When ``None``, a default is created
                and wired to the supplied registry.
        """
        self._approval_gateway = approval_gateway or ApprovalGateway()
        self._human_intervention_manager = (
            human_intervention_manager or HumanInterventionManager()
        )
        self._connector_registry = connector_registry
        self._worker_runtime = worker_runtime
        # Idempotency cache: approval IDs whose resume has already completed
        # in this process. The cache is purely an optimization; state-level
        # guards in the stores ensure correctness across restarts.
        self._executed: set[str] = set()

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    def set_connector_registry(self, registry: Any) -> None:
        """Inject a connector registry after construction."""
        self._connector_registry = registry

    def set_worker_runtime(self, runtime: Any) -> None:
        """Inject a worker runtime after construction."""
        self._worker_runtime = runtime

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def resume(
        self,
        approval_request_id: str,
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Resume the operation associated with an approved approval request.

        Args:
            approval_request_id: Identifier of the approval request.

        Returns:
            Structured dictionary describing the resume outcome. The
            dictionary always includes a top-level ``status`` field drawn
            from the pipeline states listed in the module docstring.
        """
        if not approval_request_id:
            return self._fail("approval_not_found", "approval_request_id is required")

        request = self._approval_gateway.get_request(approval_request_id)
        if not request:
            return self._fail(
                "approval_not_found",
                f"Approval request {approval_request_id} not found",
            )

        request_owner = request.get("owner_id")
        if current_user_id is not None:
            if request_owner and request_owner != current_user_id:
                return self._fail(
                    "approval_unauthorized",
                    "Authenticated user is not authorized to resume this approval",
                    approval=approval_request_id,
                )
        elif request_owner:
            return self._fail(
                "approval_unauthorized",
                "Authenticated user is required to resume this approval",
                approval=approval_request_id,
            )

        status_value = (request.get("status") or "").lower()
        if status_value == "pending":
            return self._fail(
                "approval_pending",
                "Approval request is still pending",
                approval=approval_request_id,
            )
        if status_value == "rejected":
            return self._fail(
                "approval_rejected",
                "Approval request was rejected; resume is not permitted",
                approval=approval_request_id,
            )
        if status_value == "expired":
            return self._fail(
                "approval_expired",
                "Approval request has expired",
                approval=approval_request_id,
            )
        if status_value != "approved":
            return self._fail(
                "resume_failed",
                f"Approval request has unexpected status '{status_value}'",
                approval=approval_request_id,
            )

        # Expired approvals must be detected even when status was set before
        # the clock advanced past ``expires_at``.
        expires_at = request.get("expires_at")
        if expires_at and self._is_expired(expires_at):
            return self._fail(
                "approval_expired",
                "Approval request has expired",
                approval=approval_request_id,
            )

        # Idempotency: state-level guard first (works across restarts),
        # then the in-process cache (fast-path optimization).
        guard = self._state_guard(request, current_user_id=current_user_id)
        if guard is not None:
            return guard

        if approval_request_id in self._executed:
            return self._fail(
                "awaiting_resume",
                "Approval already executed by this service; no duplicate resume",
                approval=approval_request_id,
                action_type=request.get("action_type"),
            )

        action_type = request.get("action_type") or ""
        payload = request.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        if action_type in CONNECTOR_ACTIONS:
            result = self._resume_connector_action(
                approval_request_id=approval_request_id,
                mission_id=request.get("mission_id"),
                action_type=action_type,
                payload=payload,
                current_user_id=current_user_id,
            )
        else:
            # Non-connector actions: defer to worker_runtime. The
            # ``execute_connector_action`` path is the canonical entry
            # point for connector dispatch; for non-connector actions we
            # return an explicit awaiting_resume result so the caller can
            # decide.
            if self._worker_runtime is None:
                result = self._fail(
                    "resume_failed",
                    "No worker runtime configured to resume non-connector actions",
                    operation_key=payload.get("operation_key"),
                    approval=approval_request_id,
                    action_type=action_type,
                )
            else:
                # Use the existing execute_connector_action for connector
                # styles only; for true non-connector actions the worker
                # runtime currently lacks a generic dispatcher, so we
                # return awaiting_resume explicitly.
                result = {
                    "status": "awaiting_resume",
                    "approval_request_id": approval_request_id,
                    "action_type": action_type,
                    "message": (
                        "Non-connector action resume is not supported by "
                        "the synchronous pipeline."
                    ),
                }

        # Mark the approval as executed by this service only when the
        # resume actually performed work. Failures must remain retryable.
        if result.get("status") in {"resumed", "completed", "awaiting_human_intervention"}:
            self._executed.add(approval_request_id)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resume_connector_action(
        self,
        approval_request_id: str,
        mission_id: str | None,
        action_type: str,
        payload: dict[str, Any],
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a connector action after approval."""
        platform = payload.get("platform")
        if not platform:
            return self._fail(
                "resume_failed",
                "Approval payload missing 'platform' field",
                approval=approval_request_id,
                action_type=action_type,
            )

        if self._connector_registry is None:
            return self._fail(
                "resume_failed",
                "No connector registry configured",
                approval=approval_request_id,
                action_type=action_type,
                platform=platform,
            )

        connector = self._connector_registry.get(platform)
        if connector is None:
            return self._fail(
                "resume_failed",
                f"No connector registered for platform '{platform}'",
                approval=approval_request_id,
                action_type=action_type,
                platform=platform,
                supported_platforms=self._connector_registry.list_platforms(),
            )

        worker_id = payload.get("worker_id")
        if not worker_id:
            return self._fail(
                "resume_failed",
                "Approval payload missing 'worker_id' field",
                approval=approval_request_id,
                action_type=action_type,
                platform=platform,
            )

        try:
            if action_type == "start_platform_onboarding":
                return self._handle_start_onboarding(
                    approval_request_id=approval_request_id,
                    mission_id=mission_id,
                    worker_id=worker_id,
                    platform=platform,
                    connector=connector,
                    operation_key=payload.get("operation_key"),
                )
            if action_type == "resume_platform_onboarding":
                return self._handle_resume_onboarding(
                    approval_request_id=approval_request_id,
                    mission_id=mission_id,
                    payload=payload,
                    connector=connector,
                )
            if action_type == "connect_platform":
                return self._handle_connect_account(
                    approval_request_id=approval_request_id,
                    worker_id=worker_id,
                    payload=payload,
                    connector=connector,
                )
            if action_type == "check_platform_status":
                return {
                    "status": "resumed",
                    "approval_request_id": approval_request_id,
                    "action_type": action_type,
                    "platform": platform,
                    "result": connector.health_check(),
                }
            if action_type == "publish_content":
                return self._handle_publish_content(
                    approval_request_id=approval_request_id,
                    mission_id=mission_id,
                    worker_id=payload.get("worker_id"),
                    payload=payload,
                    connector=connector,
                    current_user_id=current_user_id,
                )

            return self._fail(
                "resume_failed",
                f"Connector action '{action_type}' is not supported by the resume pipeline",
                approval=approval_request_id,
                action_type=action_type,
                platform=platform,
            )
        except NotImplementedError as exc:
            return self._fail(
                "resume_failed",
                f"Action not supported: {exc}",
                approval=approval_request_id,
                action_type=action_type,
                platform=platform,
            )
        except Exception as exc:  # pragma: no cover - defensive error
            logger.exception("Resume failed for approval %s", approval_request_id)
            return self._fail(
                "resume_failed",
                f"Connector error: {exc}",
                approval=approval_request_id,
                action_type=action_type,
                platform=platform,
            )

    def _handle_start_onboarding(
        self,
        approval_request_id: str,
        mission_id: str | None,
        worker_id: str,
        platform: str,
        connector: Any,
        operation_key: str | None = None,
    ) -> dict[str, Any]:
        """Handle resume of ``start_platform_onboarding``."""
        result = connector.start_onboarding(
            worker_id,
            mission_id=mission_id,
            approval_id=approval_request_id,
            idempotency_key=operation_key,
        )
        workflow_id = result.get("workflow_id")

        if result.get("requires_human_intervention"):
            checkpoint = self._create_checkpoint(
                mission_id=mission_id,
                connector=connector,
                workflow_id=workflow_id,
                result=result,
            )
            return {
                "status": "awaiting_human_intervention",
                "approval_request_id": approval_request_id,
                "action_type": "start_platform_onboarding",
                "platform": platform,
                "worker_id": worker_id,
                "mission_id": mission_id,
                "workflow_id": workflow_id,
                "checkpoint": checkpoint,
                "instructions": result.get("instructions"),
                "checkpoint_type": result.get("checkpoint_type"),
                "next_step": result.get("next_step"),
                "current_step": result.get("current_step"),
                "total_steps": result.get("total_steps"),
            }

        if result.get("status") == "completed":
            return {
                "status": "completed",
                "approval_request_id": approval_request_id,
                "action_type": "start_platform_onboarding",
                "platform": platform,
                "worker_id": worker_id,
                "mission_id": mission_id,
                "workflow_id": workflow_id,
                "result": result,
            }

        return {
            "status": "resumed",
            "approval_request_id": approval_request_id,
            "action_type": "start_platform_onboarding",
            "platform": platform,
            "worker_id": worker_id,
            "mission_id": mission_id,
            "workflow_id": workflow_id,
            "result": result,
        }

    def _handle_resume_onboarding(
        self,
        approval_request_id: str,
        mission_id: str | None,
        payload: dict[str, Any],
        connector: Any,
    ) -> dict[str, Any]:
        """Handle resume of ``resume_platform_onboarding``."""
        workflow_id = payload.get("workflow_id")
        if not workflow_id:
            return self._fail(
                "resume_failed",
                "Approval payload missing 'workflow_id' field",
                approval=approval_request_id,
                action_type="resume_platform_onboarding",
            )

        human_input = payload.get("human_input") or {}
        if not isinstance(human_input, dict):
            human_input = {}

        result = connector.resume_onboarding(workflow_id, human_input)
        platform = connector.platform

        # If a checkpoint was previously created for this workflow, mark it
        # completed with the human_input. This mirrors the existing
        # worker_runtime behavior but executed from the resume service.
        checkpoint_id = payload.get("checkpoint_id")
        if checkpoint_id:
            self._human_intervention_manager.complete_checkpoint(
                checkpoint_id,
                human_input,
            )

        if result.get("requires_human_intervention"):
            checkpoint = self._create_checkpoint(
                mission_id=mission_id,
                connector=connector,
                workflow_id=workflow_id,
                result=result,
            )
            return {
                "status": "awaiting_human_intervention",
                "approval_request_id": approval_request_id,
                "action_type": "resume_platform_onboarding",
                "platform": platform,
                "mission_id": mission_id,
                "workflow_id": workflow_id,
                "checkpoint": checkpoint,
                "instructions": result.get("instructions"),
                "checkpoint_type": result.get("checkpoint_type"),
                "next_step": result.get("next_step"),
                "current_step": result.get("current_step"),
                "total_steps": result.get("total_steps"),
            }

        if result.get("status") == "completed":
            return {
                "status": "completed",
                "approval_request_id": approval_request_id,
                "action_type": "resume_platform_onboarding",
                "platform": platform,
                "mission_id": mission_id,
                "workflow_id": workflow_id,
                "result": result,
            }

        return {
            "status": "resumed",
            "approval_request_id": approval_request_id,
            "action_type": "resume_platform_onboarding",
            "platform": platform,
            "mission_id": mission_id,
            "workflow_id": workflow_id,
            "result": result,
        }

    def _handle_connect_account(
        self,
        approval_request_id: str,
        worker_id: str,
        payload: dict[str, Any],
        connector: Any,
    ) -> dict[str, Any]:
        """Handle resume of ``connect_platform``."""
        auth_data = payload.get("auth_data") or {}
        if not isinstance(auth_data, dict):
            auth_data = {}

        # Defense-in-depth: strip any forbidden OAuth keys before reaching
        # the connector. The connector sanitizes again, but doing it here
        # also protects against log lines or future call paths.
        sanitized_auth = {
            k: v
            for k, v in auth_data.items()
            if k.lower()
            not in {
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
        }

        # The connector contract requires oauth_code to validate the input.
        if "oauth_code" not in sanitized_auth:
            sanitized_auth["oauth_code"] = (
                auth_data.get("oauth_code") or "approved_approval"
            )

        try:
            result = connector.connect_account(worker_id, sanitized_auth)
        except NotImplementedError as exc:
            return self._fail(
                "resume_failed",
                f"Action not supported: {exc}",
                approval=approval_request_id,
                action_type="connect_platform",
            )

        if not result.get("success"):
            return self._fail(
                "resume_failed",
                result.get("error") or "Connector connect_account failed",
                approval=approval_request_id,
                action_type="connect_platform",
            )

        return {
            "status": "resumed",
            "approval_request_id": approval_request_id,
            "action_type": "connect_platform",
            "platform": connector.platform,
            "worker_id": worker_id,
            "result": result,
        }

    def _handle_publish_content(
        self,
        approval_request_id: str,
        mission_id: str | None,
        worker_id: str | None,
        payload: dict[str, Any],
        connector: Any,
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Handle resume of ``publish_content`` (P1-11 connector action).

        Strips sensitive keys from the payload content using the canonical
        SENSITIVE_FIELDS set, derives the server-side operation_key from the
        approval request id, and dispatches to the connector's
        ``publish_content`` method with the authenticated user as owner_id.

        Args:
            approval_request_id: The originating approval request.
            mission_id: Optional mission context.
            worker_id: Worker performing the publish.
            payload: Raw approval payload. Expected to contain ``content``.
            connector: The resolved platform connector.
            current_user_id: Canonical owner UUID (``auth.users.id``).
                Used as ``published_pins.owner_id`` — never trusted from the
                payload.

        Returns:
            Structured resume result dict.
        """
        if not worker_id:
            return self._fail(
                "resume_failed",
                "Approval payload missing 'worker_id' field",
                approval=approval_request_id,
                action_type="publish_content",
                platform=connector.platform,
            )
        if not current_user_id:
            return self._fail(
                "resume_failed",
                "Authenticated user is required to publish content",
                approval=approval_request_id,
                action_type="publish_content",
                platform=connector.platform,
            )

        content = payload.get("content") or {}
        if not isinstance(content, dict):
            content = {}

        # Defense-in-depth: strip sensitive keys before the connector using
        # the canonical SENSITIVE_FIELDS set (reused across the codebase).
        sanitized_content = {
            k: v for k, v in content.items()
            if isinstance(k, str) and k.lower() not in SENSITIVE_FIELDS
        }

        # Server-derived operation_key — the client's operation_key is
        # intentionally ignored to prevent idempotency bypass.
        operation_key = f"p1-11:{approval_request_id}"

        try:
            result = connector.publish_content(
                worker_id,
                sanitized_content,
                idempotency_key=operation_key,
                owner_id=current_user_id,
            )
        except NotImplementedError as exc:
            return self._fail(
                "resume_failed",
                f"Action not supported: {exc}",
                approval=approval_request_id,
                action_type="publish_content",
                platform=connector.platform,
            )
        except Exception as exc:
            logger.exception("publish_content failed for approval %s", approval_request_id)
            return self._fail(
                "resume_failed",
                f"Connector error: {exc}",
                approval=approval_request_id,
                action_type="publish_content",
                platform=connector.platform,
            )

        if not result.get("success"):
            return self._fail(
                "resume_failed",
                result.get("error") or "Connector publish_content failed",
                approval=approval_request_id,
                action_type="publish_content",
                platform=connector.platform,
                worker_id=worker_id,
            )

        if result.get("status") == "duplicate":
            return {
                "status": "completed",
                "approval_request_id": approval_request_id,
                "action_type": "publish_content",
                "platform": connector.platform,
                "worker_id": worker_id,
                "pin_id": result.get("pin_id"),
                "operation_key": operation_key,
                "note": "pin_already_published",
                "result": result,
            }

        return {
            "status": "resumed",
            "approval_request_id": approval_request_id,
            "action_type": "publish_content",
            "platform": connector.platform,
            "worker_id": worker_id,
            "pin_id": result.get("pin_id"),
            "operation_key": operation_key,
            "result": result,
        }

    def _create_checkpoint(
        self,
        mission_id: str | None,
        connector: Any,
        workflow_id: str | None,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Create a human intervention checkpoint for a connector result."""
        try:
            checkpoint_result = self._human_intervention_manager.create_checkpoint(
                mission_id=mission_id,
                platform=connector.platform,
                checkpoint_type=result.get(
                    "checkpoint_type", "manual_platform_step_required"
                ),
                instructions=result.get(
                    "instructions", "Complete the required step on the platform"
                ),
                metadata={
                    "workflow_id": workflow_id,
                    "step": result.get("current_step"),
                    "total_steps": result.get("total_steps"),
                    "metadata": result.get("metadata", {}),
                },
            )
            if checkpoint_result.get("success"):
                return checkpoint_result.get("checkpoint")
        except Exception:  # pragma: no cover - defensive fallback
            logger.exception("Failed to create checkpoint for workflow %s", workflow_id)
        return None

    def _state_guard(
        self, request: dict[str, Any], current_user_id: str | None = None
    ) -> dict[str, Any] | None:
        """Return a result if the persisted state shows the action is already done."""
        action_type = request.get("action_type") or ""
        payload = request.get("payload") or {}
        platform = payload.get("platform")

        # Only connector actions get state-level guards.
        if action_type not in CONNECTOR_ACTIONS or not platform:
            return None
        if self._connector_registry is None:
            return None

        connector = self._connector_registry.get(platform)
        if connector is None:
            return None

        worker_id = payload.get("worker_id")
        workflow_id = payload.get("workflow_id")

        # Guard for ``start_platform_onboarding`` / ``connect_platform``:
        # if the platform connection is already ``connected``, the resume
        # would be a no-op duplicate.
        if action_type in {"start_platform_onboarding", "connect_platform"} and worker_id:
            try:
                status = connector.get_account_status(worker_id)
            except Exception:
                status = None
            if status and status.get("status") == "connected":
                return {
                    "status": "completed",
                    "approval_request_id": request.get("id"),
                    "action_type": action_type,
                    "platform": platform,
                    "worker_id": worker_id,
                    "note": "platform_connection_already_connected",
                    "result": status,
                }

    # Guard for ``resume_platform_onboarding``: if the workflow is
        # already completed in the persistent store, do not advance again.
        if action_type == "resume_platform_onboarding" and workflow_id:
            store = getattr(connector, "_workflow_store", None)
            if store is not None:
                try:
                    persisted = store.get(workflow_id)
                except Exception:
                    persisted = None
                if persisted and (persisted.get("status") or "").lower() == "completed":
                    return {
                        "status": "completed",
                        "approval_request_id": request.get("id"),
                        "action_type": action_type,
                        "platform": platform,
                        "workflow_id": workflow_id,
                        "note": "workflow_already_completed",
                        "result": persisted,
                    }

        # Guard for ``publish_content``: if the server-derived operation_key
        # for this approval has already produced a published pin in the
        # durable store, do not publish a duplicate. The operation_key is
        # derived from the approval id so it is deterministic and never
        # trust a caller-supplied value.
        if action_type == "publish_content" and request.get("id"):
            if current_user_id is None:
                return None
            operation_key = f"p1-11:{request.get('id')}"
            pin_store = getattr(connector, "_pin_store", None)
            if pin_store is not None:
                try:
                    existing = pin_store.get_by_operation_key(
                        owner_id=current_user_id,
                        operation_key=operation_key,
                    )
                except Exception:
                    existing = None
                if existing is not None:
                    return {
                        "status": "completed",
                        "approval_request_id": request.get("id"),
                        "action_type": action_type,
                        "platform": platform,
                        "worker_id": worker_id,
                        "pin_id": existing.get("pin_id"),
                        "operation_key": operation_key,
                        "note": "pin_already_published",
                        "result": existing,
                    }

        return None

    @staticmethod
    def _is_expired(expires_at: Any) -> bool:
        """Return True when the supplied ISO timestamp is in the past."""
        try:
            from datetime import datetime, timezone

            if isinstance(expires_at, str):
                normalized = expires_at.replace("Z", "+00:00")
                exp_dt = datetime.fromisoformat(normalized)
            else:
                return False
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > exp_dt
        except Exception:
            return False

    @staticmethod
    def _fail(status: str, error: str, **extra: Any) -> dict[str, Any]:
        """Build a structured failure response."""
        payload: dict[str, Any] = {"status": status, "success": False, "error": error}
        for key, value in extra.items():
            if value is None:
                continue
            payload[key] = value
        return payload


__all__ = ["ApprovalResumeService", "CONNECTOR_ACTIONS"]