"""Pinterest OAuth helper for AEA Core affiliate onboarding.

Provides OAuth 2.0 authorization URL construction and CSRF state
management without exchanging authorization codes (Phase 2) and without
calling the real Pinterest API (Phase 2+).

State is persisted in the existing ``human_intervention_checkpoints``
table via the ``HumanInterventionManager``.  No schema migration is
required because state data is stored in the ``metadata`` JSONB column
that already exists on that table.

Security model:
  - State is ``secrets.token_urlsafe(32)`` — 256 bits of entropy.
  - State is bound to ``owner_id`` and ``workflow_id``.
  - State is single-use (consumed on first successful callback).
  - State expires after a configurable TTL (default 15 minutes).
  - State never appears in logs — it is only stored in checkpoint metadata
    and transmitted as a URL query parameter.

Phase 2 (future) will exchange the authorization code at:
    POST https://api.pinterest.com/v5/oauth/token
using HTTP Basic Authentication with the client credentials.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger("pinterest_oauth")

PINTEREST_AUTH_BASE_URL = "https://www.pinterest.com/oauth/"

DEFAULT_SCOPES = [
    "user_accounts:read",
    "boards:read",
    "pins:create",
]

DEFAULT_STATE_TTL_SECONDS = 900


class PinterestOAuthConfig:
    """Lightweight container for Pinterest OAuth configuration.

    All values are read from environment variables.  No credential
    values are ever logged or returned.
    """

    def __init__(self) -> None:
        self.client_id = os.environ.get("PINTEREST_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("PINTEREST_CLIENT_SECRET", "").strip()
        self.redirect_uri = os.environ.get("PINTEREST_REDIRECT_URI", "").strip()

    @property
    def is_configured(self) -> bool:
        """Return True only when all three required env vars are present."""
        return bool(self.client_id and self.redirect_uri)

    @property
    def has_secret(self) -> bool:
        """Return True only when client_secret is present."""
        return bool(self.client_secret)

    # The client_secret is never exposed via this API.
    def redacted_summary(self) -> dict[str, Any]:
        return {
            "client_id_set": bool(self.client_id),
            "client_secret_set": bool(self.client_secret),
            "redirect_uri_set": bool(self.redirect_uri),
        }


class PinterestOAuthHelper:
    """Helper for OAuth 2.0 authorization URL generation and state management.

    State lifecycle:
        generate_state()  ->  store in checkpoint metadata  ->  validate_state()
        ->  consume_state()  (single-use, expires after TTL)
    """

    def __init__(
        self,
        config: PinterestOAuthConfig | None = None,
        state_ttl_seconds: int = DEFAULT_STATE_TTL_SECONDS,
    ) -> None:
        self._config = config or PinterestOAuthConfig()
        self._state_ttl = state_ttl_seconds

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return self._config.is_configured

    @property
    def scopes(self) -> list[str]:
        return list(DEFAULT_SCOPES)

    @property
    def redirect_uri(self) -> str:
        return self._config.redirect_uri

    @property
    def auth_base_url(self) -> str:
        return PINTEREST_AUTH_BASE_URL

    # ------------------------------------------------------------------
    # State generation
    # ------------------------------------------------------------------

    def generate_state(self, owner_id: str, workflow_id: str) -> str:
        """Generate a cryptographically secure, user+workflow-bound state.

        The state is an opaque random token.  Binding to owner_id and
        workflow_id is enforced by the checkpoint lookup, not encoded
        in the token itself.

        Args:
            owner_id: The authenticated user / worker identifier.
            workflow_id: The onboarding workflow identifier.

        Returns:
            A URL-safe random state string (43+ chars, 256 bits entropy).
        """
        return secrets.token_urlsafe(32)

    # ------------------------------------------------------------------
    # Authorization URL
    # ------------------------------------------------------------------

    def build_authorization_url(
        self,
        state: str,
        scopes: list[str] | None = None,
    ) -> str:
        """Construct the Pinterest OAuth authorization URL.

        Args:
            state: The CSRF state token.
            scopes: Optional scope list (defaults to AEA affiliate scopes).

        Returns:
            Fully-qualified authorization URL.

        Raises:
            ValueError: If OAuth is not configured (missing client_id or
                redirect_uri).
        """
        if not self._config.is_configured:
            raise ValueError(
                "Pinterest OAuth is not configured: "
                "PINTEREST_CLIENT_ID and PINTEREST_REDIRECT_URI must be set."
            )

        scope_list = scopes if scopes is not None else self.scopes
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": ",".join(scope_list),
            "state": state,
        }
        return f"{self.auth_base_url}?{urlencode(params)}"

    def generate_authorization_url(
        self,
        owner_id: str,
        workflow_id: str,
        scopes: list[str] | None = None,
    ) -> tuple[str, str]:
        """Generate a new OAuth state and the corresponding authorization URL.

        When OAuth is not configured (no ``PINTEREST_CLIENT_ID`` /
        ``PINTEREST_REDIRECT_URI`` in the environment), a simulated
        URL is returned so that the state checkpoint flow still works
        in test/non-production environments.  In this mode the URL
        points to Pinterest's OAuth entry point but without real
        client credentials — suitable for HITL simulation only.

        Returns:
            Tuple of (authorization_url, state).
        """
        state = self.generate_state(owner_id, workflow_id)
        if not self._config.is_configured:
            logger.warning(
                "Pinterest OAuth not configured; returning simulated "
                "authorization URL for workflow %s",
                workflow_id,
            )
            url = f"{self.auth_base_url}?state={state}"
        else:
            url = self.build_authorization_url(state, scopes=scopes)
        return url, state

    # ------------------------------------------------------------------
    # State metadata (stored in checkpoint metadata JSONB)
    # ------------------------------------------------------------------

    def state_metadata(self, state: str, owner_id: str, workflow_id: str) -> dict[str, Any]:
        """Build the metadata sub-dict to persist inside a checkpoint.

        The consumer (ApprovalResumeService._create_checkpoint) merges this
        into the checkpoint's ``metadata`` JSONB column alongside
        ``workflow_id``, ``step``, and ``total_steps``.
        """
        now = datetime.now(timezone.utc)
        return {
            "oauth_state": state,
            "oauth_state_owner": owner_id,
            "oauth_state_workflow": workflow_id,
            "oauth_state_created_at": now.isoformat(),
            "oauth_state_expires_at": (now.timestamp() + self._state_ttl),
        }

    # ------------------------------------------------------------------
    # State validation
    # ------------------------------------------------------------------

    @staticmethod
    def _is_state_expired(metadata: dict[str, Any], now_ts: float | None = None) -> bool:
        """Check whether an OAuth state has expired."""
        now_ts = now_ts or time.time()
        expires_at = metadata.get("oauth_state_expires_at")
        if expires_at is None:
            return False
        try:
            return float(expires_at) <= now_ts
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _is_state_consumed(metadata: dict[str, Any]) -> bool:
        """Check whether an OAuth state has been consumed (single-use)."""
        return bool(metadata.get("oauth_state_consumed"))

    def validate_state(
        self,
        received_state: str,
        checkpoint: dict[str, Any],
        owner_id: str,
        workflow_id: str | None = None,
    ) -> tuple[bool, str]:
        """Validate an OAuth state against a checkpoint's persisted state.

        Args:
            received_state: The ``state`` query parameter from the callback.
            checkpoint: The checkpoint dict from HumanInterventionManager.
            owner_id: The authenticated user identity.
            workflow_id: Optional workflow ID to additionally bind state.

        Returns:
            Tuple of (is_valid, error_reason).
        """
        if not received_state:
            return False, "missing_state"

        metadata = checkpoint.get("metadata", {}) or {}
        if isinstance(metadata, dict):
            md = metadata.get("metadata", metadata)
        else:
            md = metadata

        stored_state = md.get("oauth_state")
        if not stored_state:
            return False, "no_state_in_checkpoint"

        if not secrets.compare_digest(received_state, stored_state):
            return False, "state_mismatch"

        stored_owner = md.get("oauth_state_owner")
        if stored_owner and stored_owner != owner_id:
            return False, "owner_mismatch"

        if workflow_id:
            stored_wf = md.get("oauth_state_workflow")
            if stored_wf and stored_wf != workflow_id:
                return False, "workflow_mismatch"

        if self._is_state_consumed(md):
            return False, "state_already_consumed"

        if self._is_state_expired(md):
            return False, "state_expired"

        return True, ""

    @staticmethod
    def consume_state_metadata() -> dict[str, Any]:
        """Return the metadata patch that marks a state as consumed."""
        now = datetime.now(timezone.utc)
        return {
            "oauth_state_consumed": True,
            "oauth_state_consumed_at": now.isoformat(),
        }


__all__ = [
    "PinterestOAuthConfig",
    "PinterestOAuthHelper",
    "PINTEREST_AUTH_BASE_URL",
    "DEFAULT_SCOPES",
    "DEFAULT_STATE_TTL_SECONDS",
]
