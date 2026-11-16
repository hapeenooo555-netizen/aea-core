"""ClickPesa Hosted Checkout API adapter.

Credentials are read only from server environment. This adapter does not mark
orders paid; payment status must be reconciled against ClickPesa separately.
"""
from __future__ import annotations

import os
from urllib.parse import quote
from typing import Any

import httpx


class ClickPesaConfigurationError(RuntimeError):
    pass


class ClickPesaAPIError(RuntimeError):
    pass


class ClickPesaClient:
    def __init__(
        self,
        *,
        client_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.client_id = (client_id or os.getenv("CLICKPESA_CLIENT_ID", "")).strip()
        self.api_key = (api_key or os.getenv("CLICKPESA_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv(
            "CLICKPESA_API_BASE_URL", "https://api.clickpesa.com"
        )).rstrip("/")
        self.timeout = timeout
        self.transport = transport
        if not self.client_id or not self.api_key:
            raise ClickPesaConfigurationError(
                "ClickPesa credentials are not configured on the server."
            )
        if not self.base_url.startswith("https://"):
            raise ClickPesaConfigurationError("ClickPesa API base URL must use HTTPS.")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.request(method, self.base_url + path, **kwargs)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ClickPesaAPIError("ClickPesa API request failed.") from exc

    def generate_token(self) -> str:
        payload = self._request(
            "POST",
            "/third-parties/generate-token",
            headers={"api-key": self.api_key, "client-id": self.client_id},
        )
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token.startswith("Bearer "):
            raise ClickPesaAPIError("ClickPesa did not return a valid authorization token.")
        return token

    def generate_checkout_link(
        self,
        *,
        amount_tzs: int,
        order_reference: str,
        description: str,
        customer_name: str,
        customer_email: str,
        customer_phone: str,
        callback_url: str | None = None,
    ) -> str:
        if amount_tzs <= 0:
            raise ValueError("Checkout amount must be positive.")
        if not order_reference or len(order_reference) > 64:
            raise ValueError("Invalid order reference.")
        if callback_url and not callback_url.startswith("https://"):
            raise ValueError("ClickPesa callback URL must use HTTPS.")
        token = self.generate_token()
        body = {
            "totalPrice": str(amount_tzs),
            "orderCurrency": "TZS",
            "orderReference": order_reference,
            "description": description[:250],
            "customerName": customer_name[:120],
            "customerEmail": customer_email[:254],
            "customerPhone": customer_phone,
        }
        if callback_url:
            body["callbackUrl"] = callback_url
        payload = self._request(
            "POST",
            "/third-parties/checkout-link/generate-checkout-url",
            headers={"Authorization": token, "Content-Type": "application/json"},
            json=body,
        )
        link = payload.get("checkoutLink") if isinstance(payload, dict) else None
        if not isinstance(link, str) or not link.startswith("https://"):
            raise ClickPesaAPIError("ClickPesa did not return a valid hosted checkout URL.")
        return link

    def query_payment(self, order_reference: str) -> list[dict[str, Any]]:
        if not order_reference:
            raise ValueError("Order reference is required.")
        token = self.generate_token()
        payload = self._request(
            "GET",
            "/third-parties/payments/" + quote(order_reference, safe=""),
            headers={"Authorization": token},
        )
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            return payload
        raise ClickPesaAPIError("ClickPesa returned an unexpected payment status response.")
