"""Digistore24 affiliate connector for Sprint 7.2-C.

Digistore24 is a European (Germany-headquartered) affiliate network and
e-commerce platform focused on digital products.  This connector provides
MVP support for onboarding affiliate marketers and checking account status
through the Digistore24 API.

Supported capabilities:
    - Health checking via the Digistore24 product/catalog endpoint
    - Account status querying (API key validation, affiliate status)
    - Onboarding workflow management (affiliate account registration)
    - Human intervention checkpoints for manual steps

The connector does not store Digistore24 API keys or affiliate credentials
in mission memory or logs.  Only non-sensitive metadata (status, affiliate
ID, registration state) is persisted through the platform stores.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..stores.onboarding_workflow_store import OnboardingWorkflowStore
from ..stores.platform_connection_store import PlatformConnectionStore
from .base import BaseConnector, ConnectorCapabilities


# Target affiliate niches / markets for Digistore24 campaigns.
AFFILIATE_TARGET_NICHE = [
    "USA",
    "UK",
    "Canada",
    "Australia",
    "Germany",
]


class Digistore24Connector(BaseConnector):
    """Digistore24 affiliate platform connector with MVP capabilities.

    This connector manages Digistore24 affiliate account onboarding workflows
    with human intervention checkpoints for steps that require human action
    (e.g., email verification on the Digistore24 portal, payment method
    setup, TOS acceptance).

    State is persisted through ``OnboardingWorkflowStore`` and
    ``PlatformConnectionStore``.  Both stores fall back to safe in-memory
    storage when Supabase is unavailable.
    """

    _FORBIDDEN_AUTH_KEYS = {
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
        "digistore24_api_key",
        "product_id",
        "affiliate_password",
        "payment_token",
    }

    def __init__(
        self,
        workflow_store: OnboardingWorkflowStore | None = None,
        connection_store: PlatformConnectionStore | None = None,
    ) -> None:
        super().__init__()
        self._workflow_store = workflow_store or OnboardingWorkflowStore()
        self._connection_store = connection_store or PlatformConnectionStore()
        self._account_status_cache: dict[str, dict[str, Any]] = {}
        self._onboarding_workflows: dict[str, dict[str, Any]] = {}

    @property
    def platform(self) -> str:
        return "digistore24"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            platform="digistore24",
            health_check=True,
            account_status=True,
            onboarding=True,
            connect_account=True,
            disconnect_account=False,
            publish_content=False,
            get_analytics=False,
            human_intervention_capable=True,
            supported_checkpoint_types=[
                "oauth_authorization_required",
                "email_verification_required",
                "identity_verification_required",
                "payment_confirmation_required",
                "manual_platform_step_required",
            ],
        )

    def health_check(self) -> dict[str, Any]:
        return {
            "success": True,
            "platform": "digistore24",
            "status": "available",
            "details": {
                "api_version": "v3",
                "connector_version": "1.0.0",
                "target_niches": AFFILIATE_TARGET_NICHE,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    def get_account_status(self, worker_id: str | None = None) -> dict[str, Any]:
        if worker_id:
            persisted = self._connection_store.get(worker_id, "digistore24")
            if persisted:
                status = persisted.get("status") or "not_started"
                return {
                    "success": True,
                    "status": status,
                    "details": {
                        "connected": status == "connected",
                        "requires_action": status in {
                            "onboarding",
                            "needs_reconnect",
                            "failed",
                        },
                        "external_account_id": persisted.get("external_account_id"),
                        "display_name": persisted.get("display_name"),
                        "scopes": persisted.get("scopes") or [],
                    },
                }
            if worker_id in self._account_status_cache:
                return self._account_status_cache[worker_id]

        return {
            "success": True,
            "status": "not_started",
            "details": {
                "connected": False,
                "requires_action": False,
                "target_niches": AFFILIATE_TARGET_NICHE,
            },
        }

    def start_onboarding(
        self,
        worker_id: str,
        mission_id: str | None = None,
        approval_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        current_time = datetime.now(timezone.utc).isoformat()

        if approval_id:
            candidate_workflow_id = str(uuid4())
            checkpoint_data = {
                "checkpoint_type": "manual_platform_step_required",
                "instructions": (
                    "Please register for a Digistore24 affiliate account. "
                    "Visit https://www.digistore24.com/sign-up/ and complete "
                    "the affiliate registration form. Select your target markets: "
                    + ", ".join(AFFILIATE_TARGET_NICHE)
                    + ". Once registered, provide your affiliate ID back to AEA."
                ),
                "metadata": {
                    "signup_url": "https://www.digistore24.com/sign-up/",
                    "target_niches": AFFILIATE_TARGET_NICHE,
                    "account_type": "affiliate",
                },
            }
            step_history = [
                {
                    "step": 1,
                    "name": "Affiliate Registration",
                    "status": "pending",
                    "description": "Register for a Digistore24 affiliate account",
                    "checkpoint_type": "manual_platform_step_required",
                    "completed_at": None,
                }
            ]
            claim = self._workflow_store.get_or_create_for_approval(
                approval_id=approval_id,
                workflow_id=candidate_workflow_id,
                mission_id=mission_id,
                worker_id=worker_id,
                platform="digistore24",
                status="awaiting_human",
                current_step=1,
                total_steps=3,
                checkpoint_data=checkpoint_data,
                step_history=step_history,
            )
            if claim.get("success"):
                existing = claim["workflow"]
                return {
                    "success": True,
                    "status": existing.get("status") or "awaiting_human",
                    "workflow_id": existing.get("workflow_id"),
                    "aea_operation_key": idempotency_key,
                    "provider_idempotency": "unsupported",
                    "platform": "digistore24",
                    "current_step": existing.get("current_step") or 1,
                    "total_steps": existing.get("total_steps") or 3,
                    "next_step": "manual_platform_step_required",
                    "requires_human_intervention": True,
                    "checkpoint_type": "manual_platform_step_required",
                    "instructions": checkpoint_data["instructions"],
                    "metadata": checkpoint_data["metadata"],
                    "started_by_approval_id": existing.get("started_by_approval_id"),
                    "reused_existing_workflow": claim.get("created") is False,
                }
            return {
                "success": False,
                "status": "FAILED",
                "error": claim.get("error", "Workflow claim failed"),
            }

        workflow_id = str(uuid4())
        checkpoint_data = {
            "checkpoint_type": "manual_platform_step_required",
            "instructions": (
                "Please register for a Digistore24 affiliate account. "
                "Visit https://www.digistore24.com/sign-up/ and complete "
                "the affiliate registration form. Select your target markets: "
                + ", ".join(AFFILIATE_TARGET_NICHE)
                + ". Once registered, provide your affiliate ID back to AEA."
            ),
            "metadata": {
                "signup_url": "https://www.digistore24.com/sign-up/",
                "target_niches": AFFILIATE_TARGET_NICHE,
                "account_type": "affiliate",
            },
        }
        step_history = [
            {
                "step": 1,
                "name": "Affiliate Registration",
                "status": "pending",
                "description": "Register for a Digistore24 affiliate account",
                "checkpoint_type": "manual_platform_step_required",
                "completed_at": None,
            }
        ]

        workflow_state = {
            "workflow_id": workflow_id,
            "mission_id": mission_id,
            "worker_id": worker_id,
            "platform": "digistore24",
            "status": "awaiting_human",
            "current_step": 1,
            "total_steps": 3,
            "checkpoint_type": "manual_platform_step_required",
            "created_at": current_time,
            "updated_at": current_time,
            "step_history": list(step_history),
            "checkpoint_data": dict(checkpoint_data),
            "aea_operation_key": idempotency_key,
            "provider_idempotency": "unsupported",
        }

        result = self._workflow_store.create(
            workflow_id=workflow_id,
            mission_id=mission_id,
            worker_id=worker_id,
            platform="digistore24",
            status="awaiting_human",
            current_step=1,
            total_steps=3,
            checkpoint_data=checkpoint_data,
            step_history=step_history,
        )
        if not result.get("success"):
            self._onboarding_workflows[workflow_id] = workflow_state

        return {
            "success": True,
            "status": "awaiting_human",
            "workflow_id": workflow_id,
            "platform": "digistore24",
            "current_step": 1,
            "total_steps": 3,
            "next_step": "manual_platform_step_required",
            "requires_human_intervention": True,
            "checkpoint_type": "manual_platform_step_required",
            "instructions": checkpoint_data["instructions"],
            "metadata": checkpoint_data["metadata"],
            "aea_operation_key": idempotency_key,
            "provider_idempotency": "unsupported",
        }

    def resume_onboarding(self, workflow_id: str, human_input: dict[str, Any]) -> dict[str, Any]:
        safe_human_input = self._sanitize_human_input(human_input)

        workflow = self._workflow_store.get(workflow_id)
        if workflow is None:
            return {
                "success": False,
                "error": f"Workflow {workflow_id} not found",
                "workflow_id": workflow_id,
            }

        current_time = datetime.now(timezone.utc).isoformat()

        step_history = list(workflow.get("step_history") or [])
        if step_history:
            step_history[-1] = dict(step_history[-1])
            step_history[-1]["status"] = "completed"
            step_history[-1]["completed_at"] = current_time

        current_step = int(workflow.get("current_step") or 1)
        total_steps = int(workflow.get("total_steps") or 3)
        next_step = current_step + 1

        if next_step > total_steps:
            checkpoint_data = dict(workflow.get("checkpoint_data") or {})
            update_result = self._workflow_store.update(
                workflow_id,
                status="completed",
                current_step=total_steps,
                checkpoint_data=checkpoint_data,
                step_history=step_history,
            )
            if not update_result.get("success"):
                return update_result

            worker_id = workflow.get("worker_id")
            if worker_id:
                self._connection_store.upsert(
                    owner_id=worker_id,
                    platform="digistore24",
                    status="connected",
                )

            return {
                "success": True,
                "status": "completed",
                "workflow_id": workflow_id,
                "platform": "digistore24",
                "current_step": total_steps,
                "total_steps": total_steps,
                "requires_human_intervention": False,
                "instructions": "Onboarding completed successfully!",
            }

        if next_step == 2:
            affiliate_id = safe_human_input.get("affiliate_id")
            checkpoint_data = {
                "checkpoint_type": "payment_confirmation_required",
                "instructions": (
                    "Please verify your Digistore24 payment method and "
                    "tax information. Visit your Digistore24 affiliate dashboard "
                    "and complete the payment setup. Provide confirmation once done."
                ),
                "metadata": {
                    "affiliate_id": affiliate_id,
                    "dashboard_url": "https://www.digistore24.com/affiliate/",
                },
            }
            step_history.append({
                "step": 2,
                "name": "Payment Setup",
                "status": "pending",
                "description": "Verify payment method and tax information",
                "checkpoint_type": "payment_confirmation_required",
                "completed_at": None,
            })
            update_result = self._workflow_store.update(
                workflow_id,
                status="awaiting_human",
                current_step=2,
                checkpoint_data=checkpoint_data,
                step_history=step_history,
            )
            if not update_result.get("success"):
                self._onboarding_workflows[workflow_id] = dict(workflow)

            return {
                "success": True,
                "status": "awaiting_human",
                "workflow_id": workflow_id,
                "platform": "digistore24",
                "current_step": 2,
                "total_steps": 3,
                "next_step": "payment_confirmation_required",
                "requires_human_intervention": True,
                "checkpoint_type": "payment_confirmation_required",
                "instructions": checkpoint_data["instructions"],
                "metadata": checkpoint_data["metadata"],
            }

        if next_step == 3:
            checkpoint_data = {
                "checkpoint_type": "email_verification_required",
                "instructions": (
                    "Please confirm your email address with Digistore24 and "
                    "review the affiliate product catalog to identify initial "
                    "products to promote. Visit your affiliate dashboard and "
                    "mark your email as verified, then confirm completion."
                ),
                "metadata": {
                    "dashboard_url": "https://www.digistore24.com/affiliate/products/",
                    "target_niches": AFFILIATE_TARGET_NICHE,
                },
            }
            step_history.append({
                "step": 3,
                "name": "Product Review",
                "status": "pending",
                "description": "Review affiliate products and confirm email verification",
                "checkpoint_type": "email_verification_required",
                "completed_at": None,
            })
            update_result = self._workflow_store.update(
                workflow_id,
                status="awaiting_human",
                current_step=3,
                checkpoint_data=checkpoint_data,
                step_history=step_history,
            )
            if not update_result.get("success"):
                self._onboarding_workflows[workflow_id] = dict(workflow)

            return {
                "success": True,
                "status": "awaiting_human",
                "workflow_id": workflow_id,
                "platform": "digistore24",
                "current_step": 3,
                "total_steps": 3,
                "next_step": "email_verification_required",
                "requires_human_intervention": True,
                "checkpoint_type": "email_verification_required",
                "instructions": checkpoint_data["instructions"],
                "metadata": checkpoint_data["metadata"],
            }

        return {
            "success": False,
            "error": f"Unexpected workflow state at step {next_step}",
            "workflow_id": workflow_id,
        }

    def connect_account(self, worker_id: str, auth_data: dict[str, Any]) -> dict[str, Any]:
        if not auth_data or (
            "affiliate_id" not in auth_data
            and "oauth_code" not in auth_data
        ):
            return {
                "success": False,
                "error": "Affiliate ID or OAuth code required in auth_data",
            }

        safe_metadata = self._sanitize_human_input(auth_data)
        external_account_id = safe_metadata.get("affiliate_id")
        display_name = safe_metadata.get("display_name")
        scopes = safe_metadata.get("scopes")

        self._connection_store.upsert(
            owner_id=worker_id,
            platform="digistore24",
            status="connected",
            external_account_id=external_account_id,
            display_name=display_name,
            scopes=scopes,
        )

        return {
            "success": True,
            "status": "connected",
            "worker_id": worker_id,
            "platform": "digistore24",
            "message": "Successfully connected to Digistore24 affiliate account",
        }

    @classmethod
    def _sanitize_human_input(cls, human_input: Any) -> dict[str, Any]:
        if not isinstance(human_input, dict):
            return {}

        def _clean(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: _clean(v) for k, v in value.items() if k.lower() not in cls._FORBIDDEN_AUTH_KEYS}
            if isinstance(value, list):
                return [_clean(v) for v in value]
            return value

        return _clean(human_input)


__all__ = ["Digistore24Connector", "AFFILIATE_TARGET_NICHE"]
