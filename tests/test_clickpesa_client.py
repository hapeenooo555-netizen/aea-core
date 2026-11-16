from __future__ import annotations

import json

import httpx
import pytest

from app.services.clickpesa_client import (
    ClickPesaAPIError,
    ClickPesaClient,
    ClickPesaConfigurationError,
)


def test_clickpesa_requires_server_credentials():
    with pytest.raises(ClickPesaConfigurationError):
        ClickPesaClient(client_id="", api_key="")


def test_clickpesa_rejects_non_https_api_url():
    with pytest.raises(ClickPesaConfigurationError):
        ClickPesaClient(client_id="client", api_key="secret", base_url="http://localhost")


def test_clickpesa_generates_hosted_checkout_link():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/generate-token"):
            return httpx.Response(200, json={"token": "Bearer test-token"})
        return httpx.Response(200, json={"checkoutLink": "https://checkout.clickpesa.com/session/abc"})

    client = ClickPesaClient(
        client_id="test-client",
        api_key="test-api-key",
        base_url="https://api.clickpesa.com",
        transport=httpx.MockTransport(handler),
    )
    link = client.generate_checkout_link(
        amount_tzs=25000,
        order_reference="AEA-ORDER-001",
        description="AEA digital guide",
        customer_name="Buyer",
        customer_email="buyer@example.com",
        customer_phone="+255700000000",
        callback_url="https://aea-core.example.com/commerce/clickpesa/callback",
    )
    assert link == "https://checkout.clickpesa.com/session/abc"
    assert calls[0].headers["client-id"] == "test-client"
    assert calls[0].headers["api-key"] == "test-api-key"
    payload = json.loads(calls[1].content)
    assert payload["totalPrice"] == "25000"
    assert payload["orderCurrency"] == "TZS"
    assert payload["orderReference"] == "AEA-ORDER-001"
    assert calls[1].headers["Authorization"] == "Bearer test-token"


def test_clickpesa_rejects_invalid_checkout_amount_before_network():
    client = ClickPesaClient(client_id="client", api_key="key")
    with pytest.raises(ValueError):
        client.generate_checkout_link(
            amount_tzs=0,
            order_reference="AEA-ORDER-002",
            description="Guide",
            customer_name="Buyer",
            customer_email="buyer@example.com",
            customer_phone="+255700000000",
        )


def test_clickpesa_rejects_http_callback_url():
    client = ClickPesaClient(client_id="client", api_key="key")
    with pytest.raises(ValueError):
        client.generate_checkout_link(
            amount_tzs=1000,
            order_reference="AEA-ORDER-003",
            description="Guide",
            customer_name="Buyer",
            customer_email="buyer@example.com",
            customer_phone="+255700000000",
            callback_url="http://insecure.example.com/callback",
        )


def test_clickpesa_query_payment_encodes_order_reference():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/generate-token"):
            return httpx.Response(200, json={"token": "Bearer test-token"})
        return httpx.Response(200, json=[{"status": "PENDING", "orderReference": "ORDER/1"}])

    client = ClickPesaClient(
        client_id="client",
        api_key="key",
        base_url="https://api.clickpesa.com",
        transport=httpx.MockTransport(handler),
    )
    result = client.query_payment("ORDER/1")
    assert result[0]["status"] == "PENDING"
    assert calls[1].url.path.endswith("/ORDER%2F1")
