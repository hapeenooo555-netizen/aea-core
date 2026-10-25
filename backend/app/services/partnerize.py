"""Partnerize publisher API client.

Credentials are read only from environment variables. Never log keys or authorization headers.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

API_BASE = "https://api.partnerize.com/v2"


class PartnerizeError(RuntimeError):
    """Safe Partnerize integration error."""


class PartnerizeClient:
    def __init__(self, *, client: Any | None = None, timeout: float = 15.0):
        self.application_key = os.getenv("PARTNERIZE_APPLICATION_KEY", "").strip()
        self.api_key = os.getenv("PARTNERIZE_API_KEY", "").strip()
        self.publisher_id = os.getenv("PARTNERIZE_PUBLISHER_ID", "").strip()
        self.timeout = timeout
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.application_key and self.api_key and self.publisher_id)

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> Any:
        if not self.application_key or not self.api_key:
            raise PartnerizeError("Partnerize API credentials are not configured on the server.")
        if not self.publisher_id:
            raise PartnerizeError("Partnerize Publisher ID is not configured on the server.")
        url = f"{API_BASE}{path}"
        try:
            if self._client is not None:
                response = self._client.request(
                    method, url, params=params, json=json,
                    auth=(self.application_key, self.api_key), timeout=self.timeout,
                    headers={"Accept": "application/json"},
                )
            else:
                with httpx.Client(timeout=self.timeout, auth=(self.application_key, self.api_key),
                                  headers={"Accept": "application/json"}) as client:
                    response = client.request(method, url, params=params, json=json)
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return {"success": True}
            return response.json()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 401:
                message = "Partnerize rejected the API credentials (401). Check the application key and API key."
            elif code == 403:
                message = "Partnerize authenticated the request but denied access (403). Check account permissions."
            elif code == 404:
                message = "Partnerize endpoint or Publisher ID was not found (404). Verify the Publisher ID."
            else:
                message = f"Partnerize API returned HTTP {code}."
            raise PartnerizeError(message) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise PartnerizeError("Partnerize API request failed. Check connectivity and API availability.") from exc

    def discover_campaigns(self) -> dict[str, Any]:
        result = self._request("GET", f"/publishers/{self.publisher_id}/discovery/advertisers")
        if not isinstance(result, dict):
            raise PartnerizeError("Partnerize returned an unexpected campaign discovery response.")
        result["source"] = "Partnerize publisher API"
        return result

    def request_campaigns(self, campaign_requests: list[dict[str, str]]) -> dict[str, Any]:
        payload = {"campaign_requests": campaign_requests}
        result = self._request("POST", f"/publishers/{self.publisher_id}/campaign-requests", json=payload)
        if not isinstance(result, dict):
            return {"success": True, "response": result}
        return result


__all__ = ["PartnerizeClient", "PartnerizeError"]
