"""Airtel Money Tanzania Collection API client.

Implements only the documented OAuth2, USSD Push, and Transaction Enquiry
contracts. It never treats an initiation response or callback as proof of
payment; callers must reconcile the provider transaction before fulfilment.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx


class AirtelConfigurationError(RuntimeError):
    """Airtel credentials or configuration are missing or unsafe."""


class AirtelAPIError(RuntimeError):
    """Airtel rejected a request or returned an invalid response."""


class AirtelMoneyClient:
    SANDBOX_BASE_URL = "https://openapiuat.airtel.co.tz"
    PRODUCTION_BASE_URL = "https://openapi.airtel.co.tz"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        clock: Any = time.time,
    ) -> None:
        self.client_id = (client_id or os.getenv("AIRTEL_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret or os.getenv("AIRTEL_CLIENT_SECRET", "")).strip()
        configured_url = (base_url or os.getenv("AIRTEL_API_BASE_URL") or self.SANDBOX_BASE_URL).rstrip("/")
        if not self.client_id or not self.client_secret:
            raise AirtelConfigurationError("Airtel API credentials are not configured on the server.")
        if configured_url not in {self.SANDBOX_BASE_URL, self.PRODUCTION_BASE_URL}:
            raise AirtelConfigurationError("Airtel API base URL must be an approved Airtel Tanzania HTTPS URL.")
        self.base_url = configured_url
        self.timeout = timeout
        self.transport = transport
        self.clock = clock
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.request(method, self.base_url + path, **kwargs)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AirtelAPIError("Airtel API request failed.") from exc
        if not isinstance(payload, dict):
            raise AirtelAPIError("Airtel returned an unexpected response.")
        return payload

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        now = float(self.clock())
        if not force_refresh and self._access_token and now < self._token_expires_at:
            return self._access_token

        payload = self._request(
            "POST",
            "/auth/oauth2/token",
            headers={"Content-Type": "application/json", "Accept": "*/*"},
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
        )
        token = payload.get("access_token")
        token_type = str(payload.get("token_type", "Bearer")).lower()
        try:
            expires_in = int(payload.get("expires_in", 0))
        except (TypeError, ValueError) as exc:
            raise AirtelAPIError("Airtel returned an invalid token expiry.") from exc
        if not isinstance(token, str) or not token.strip() or token_type != "bearer" or expires_in <= 0:
            raise AirtelAPIError("Airtel did not return a valid OAuth2 bearer token.")
        self._access_token = token.strip()
        # Refresh slightly before expiry to avoid using an expired token in flight.
        self._token_expires_at = now + max(1, expires_in - min(15, expires_in // 4))
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "X-Country": "TZ",
            "X-Currency": "TZS",
            "Authorization": "Bearer " + self.get_access_token(),
        }

    @staticmethod
    def _validate_msisdn(msisdn: str) -> str:
        # Airtel documentation explicitly says to omit the country code.
        normalized = re.sub(r"[\s-]", "", str(msisdn))
        if normalized.startswith("+255"):
            normalized = normalized[4:]
        elif normalized.startswith("255"):
            normalized = normalized[3:]
        if not re.fullmatch(r"0?[67][0-9]{8}", normalized):
            raise ValueError("Tanzanian MSISDN must be supplied without country code.")
        return normalized[-9:]

    def initiate_payment(
        self,
        *,
        msisdn: str,
        amount_tzs: int,
        reference: str,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(amount_tzs, bool) or not isinstance(amount_tzs, int) or amount_tzs <= 0:
            raise ValueError("Airtel payment amount must be a positive whole TZS amount.")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 80:
            raise ValueError("A valid payment reference is required.")
        tx_id = transaction_id or str(uuid4())
        if not isinstance(tx_id, str) or not tx_id.strip() or len(tx_id) > 64:
            raise ValueError("A valid unique transaction ID is required.")

        body = {
            "reference": reference.strip(),
            "subscriber": {
                "country": "TZ",
                "currency": "TZS",
                "msisdn": self._validate_msisdn(msisdn),
            },
            "transaction": {
                "amount": amount_tzs,
                "country": "TZ",
                "currency": "TZS",
                "id": tx_id,
            },
        }
        payload = self._request(
            "POST", "/merchant/v1/payments/", headers=self._headers(), json=body
        )
        return {"request_transaction_id": tx_id, "provider_response": payload}

    def transaction_enquiry(self, transaction_id: str) -> dict[str, Any]:
        if not isinstance(transaction_id, str) or not transaction_id.strip() or len(transaction_id) > 128:
            raise ValueError("A valid Airtel transaction ID is required.")
        return self._request(
            "GET",
            "/standard/v1/payments/" + quote(transaction_id.strip(), safe=""),
            headers=self._headers(),
        )


__all__ = ["AirtelMoneyClient", "AirtelAPIError", "AirtelConfigurationError"]
