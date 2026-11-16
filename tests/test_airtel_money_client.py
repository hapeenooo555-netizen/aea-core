from __future__ import annotations

import json
import httpx
import pytest

from app.services.airtel_money_client import (
    AirtelAPIError,
    AirtelConfigurationError,
    AirtelMoneyClient,
)


def _transport(handler):
    return httpx.MockTransport(handler)


def test_oauth_and_ussd_push_use_documented_airtel_tanzania_contract():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/auth/oauth2/token":
            assert request.method == "POST"
            assert json.loads(request.content) == {
                "client_id": "public-id",
                "client_secret": "private-secret",
                "grant_type": "client_credentials",
            }
            return httpx.Response(200, json={
                "access_token": "test-access-token",
                "expires_in": "180",
                "token_type": "bearer",
            })
        assert request.url.path == "/merchant/v1/payments/"
        assert request.headers["Authorization"] == "Bearer test-access-token"
        assert request.headers["X-Country"] == "TZ"
        assert request.headers["X-Currency"] == "TZS"
        assert json.loads(request.content) == {
            "reference": "AEA order 123",
            "subscriber": {"country": "TZ", "currency": "TZS", "msisdn": "712345678"},
            "transaction": {
                "amount": 25000,
                "country": "TZ",
                "currency": "TZS",
                "id": "aea-tx-123",
            },
        }
        return httpx.Response(200, json={
            "data": {"transaction": {"id": "airtel-ref", "status": "TIP"}},
            "status": {"code": "200", "success": True},
        })

    client = AirtelMoneyClient(
        client_id="public-id",
        client_secret="private-secret",
        transport=_transport(handler),
        clock=lambda: 1000,
    )
    result = client.initiate_payment(
        msisdn="+255712345678",
        amount_tzs=25000,
        reference="AEA order 123",
        transaction_id="aea-tx-123",
    )
    assert result["request_transaction_id"] == "aea-tx-123"
    assert result["provider_response"]["data"]["transaction"]["status"] == "TIP"
    assert len(calls) == 2


def test_transaction_enquiry_uses_standard_payments_id_endpoint():
    def handler(request):
        assert request.url.path == "/auth/oauth2/token"
        return httpx.Response(200, json={
            "access_token": "token", "expires_in": 180, "token_type": "bearer"
        })

    def enquiry_handler(request):
        if request.url.path == "/auth/oauth2/token":
            return httpx.Response(200, json={
                "access_token": "token", "expires_in": 180, "token_type": "bearer"
            })
        assert request.method == "GET"
        assert request.url.raw_path == b"/standard/v1/payments/aea%2Ftx%201"
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(200, json={
            "data": {"transaction": {"id": "airtel-id", "status": "TS"}},
            "status": {"code": "200", "success": True},
        })

    client = AirtelMoneyClient(
        client_id="id", client_secret="secret",
        transport=_transport(enquiry_handler),
    )
    result = client.transaction_enquiry("aea/tx 1")
    assert result["data"]["transaction"]["status"] == "TS"


def test_token_is_cached_until_near_expiry():
    token_calls = 0

    def handler(request):
        nonlocal token_calls
        token_calls += 1
        return httpx.Response(200, json={
            "access_token": "token", "expires_in": 180, "token_type": "bearer"
        })

    now = [1000]
    client = AirtelMoneyClient(
        client_id="id", client_secret="secret",
        transport=_transport(handler), clock=lambda: now[0],
    )
    assert client.get_access_token() == "token"
    assert client.get_access_token() == "token"
    assert token_calls == 1
    now[0] = 1170
    assert client.get_access_token() == "token"
    assert token_calls == 2


@pytest.mark.parametrize("msisdn", ["+255712345678", "255712345678", "0712345678", "712345678"])
def test_tanzanian_msisdn_normalization(msisdn):
    assert AirtelMoneyClient._validate_msisdn(msisdn) == "712345678"


@pytest.mark.parametrize("msisdn", ["+254712345678", "123", "255123456789"])
def test_invalid_msisdn_is_rejected(msisdn):
    with pytest.raises(ValueError):
        AirtelMoneyClient._validate_msisdn(msisdn)


def test_missing_credentials_fail_closed():
    with pytest.raises(AirtelConfigurationError):
        AirtelMoneyClient(client_id="", client_secret="")


def test_non_airtel_or_non_https_base_url_is_rejected():
    with pytest.raises(AirtelConfigurationError):
        AirtelMoneyClient(client_id="id", client_secret="secret", base_url="http://localhost")


def test_invalid_token_response_is_rejected():
    def handler(request):
        return httpx.Response(200, json={"access_token": "", "expires_in": 180, "token_type": "bearer"})

    client = AirtelMoneyClient(
        client_id="id", client_secret="secret", transport=_transport(handler)
    )
    with pytest.raises(AirtelAPIError):
        client.get_access_token()
