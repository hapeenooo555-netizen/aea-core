"""Meta marketing connector contracts for HAPE BROTHERS.

This connector is intentionally configuration-driven. It never persists access
tokens in missions, logs, or connector metadata. Actual network calls are
isolated behind a small transport interface so they can be tested safely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from .base import BaseConnector, ConnectorCapabilities


@dataclass
class MetaConfig:
    graph_base_url: str = "https://graph.facebook.com"
    api_version: str = "v23.0"

    @property
    def base_url(self) -> str:
        return f"{self.graph_base_url.rstrip('/')}/{self.api_version}"


class MetaMarketingConnector(BaseConnector):
    """Facebook/Instagram/WhatsApp execution boundary."""

    def __init__(
        self,
        *,
        transport: Callable[..., dict[str, Any]] | None = None,
        config: MetaConfig | None = None,
    ) -> None:
        super().__init__()
        self.config = config or MetaConfig(
            graph_base_url=os.getenv("META_GRAPH_BASE_URL", "https://graph.facebook.com"),
            api_version=os.getenv("META_GRAPH_API_VERSION", "v23.0"),
        )
        self._transport = transport

    @property
    def platform(self) -> str:
        return "meta"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            platform="meta",
            account_status=True,
            connect_account=True,
            disconnect_account=True,
            publish_content=True,
            human_intervention_capable=True,
            supported_checkpoint_types=[
                "oauth_authorization_required",
                "manual_platform_step_required",
            ],
        )

    def health_check(self) -> dict[str, Any]:
        configured = bool(
            os.getenv("META_ACCESS_TOKEN")
            or os.getenv("META_APP_ID")
        )
        return {
            "platform": self.platform,
            "status": "configured" if configured else "not_configured",
            "details": {
                "facebook": bool(os.getenv("META_PAGE_ID")),
                "instagram": bool(os.getenv("META_IG_USER_ID")),
                "whatsapp": bool(os.getenv("META_WA_PHONE_NUMBER_ID")),
            },
        }

    def get_account_status(self) -> dict[str, Any]:
        health = self.health_check()
        if health["status"] == "not_configured":
            return {"status": "not_started", "details": health["details"]}
        return {"status": "connected", "details": health["details"]}

    def connect_account(
        self,
        worker_id: str,
        auth_data: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        # Store only non-secret account identifiers in the returned state.
        return {
            "success": True,
            "platform": self.platform,
            "worker_id": worker_id,
            "status": "connected",
            "account": {
                "page_id": auth_data.get("page_id"),
                "instagram_user_id": auth_data.get("instagram_user_id"),
                "whatsapp_phone_number_id": auth_data.get("whatsapp_phone_number_id"),
            },
            "idempotency_key": idempotency_key,
        }

    def disconnect_account(self, worker_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {"success": True, "platform": self.platform, "worker_id": worker_id, "status": "disconnected"}

    def publish_content(
        self,
        worker_id: str,
        content: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        channel = str(content.get("channel") or "").lower()
        if channel not in {"facebook", "instagram", "whatsapp"}:
            return {"success": False, "error": "channel must be facebook, instagram, or whatsapp"}
        if not os.getenv("META_ACCESS_TOKEN"):
            return {
                "success": False,
                "status": "not_configured",
                "requires_human_intervention": True,
                "checkpoint_type": "oauth_authorization_required",
                "error": "META_ACCESS_TOKEN is not configured",
            }

        if self._transport is None:
            return {
                "success": False,
                "status": "ready_for_execution",
                "platform": self.platform,
                "channel": channel,
                "message": "Meta credentials are configured; network transport is not attached.",
            }

        return self._transport(
            channel=channel,
            worker_id=worker_id,
            content=dict(content),
            base_url=self.config.base_url,
            idempotency_key=idempotency_key,
        )

    def get_analytics(self, worker_id: str) -> dict[str, Any]:
        return {
            "success": True,
            "platform": self.platform,
            "worker_id": worker_id,
            "status": "adapter_ready",
            "message": "Analytics transport is ready for Meta Graph integration.",
        }
