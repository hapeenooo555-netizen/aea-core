from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from app.services.partnerize import PartnerizeClient, PartnerizeError


def response(status_code: int = 200, payload: dict | None = None) -> Mock:
    item = Mock()
    item.status_code = status_code
    item.content = b'{"advertisers":[]}' if payload is None else b'{}'
    item.json.return_value = {"advertisers": []} if payload is None else payload
    item.raise_for_status.return_value = None
    return item


def test_partnerize_client_reports_configured_only_when_all_values_exist(monkeypatch):
    monkeypatch.setenv("PARTNERIZE_APPLICATION_KEY", "app-key")
    monkeypatch.setenv("PARTNERIZE_API_KEY", "api-key")
    monkeypatch.setenv("PARTNERIZE_PUBLISHER_ID", "publisher-id")
    assert PartnerizeClient().configured is True
    monkeypatch.delenv("PARTNERIZE_PUBLISHER_ID")
    assert PartnerizeClient().configured is False


def test_discovery_uses_publisher_endpoint_and_basic_auth(monkeypatch):
    monkeypatch.setenv("PARTNERIZE_APPLICATION_KEY", "app-key")
    monkeypatch.setenv("PARTNERIZE_API_KEY", "api-key")
    monkeypatch.setenv("PARTNERIZE_PUBLISHER_ID", "publisher-id")
    fake = Mock()
    fake.request.return_value = response(payload={"advertisers": []})
    result = PartnerizeClient(client=fake).discover_campaigns()
    args, kwargs = fake.request.call_args
    assert args == ("GET", "https://api.partnerize.com/v2/publishers/publisher-id/discovery/advertisers")
    assert kwargs["auth"] == ("app-key", "api-key")
    assert result["advertisers"] == []
    assert result["source"] == "Partnerize publisher API"


def test_missing_credentials_fail_without_network_call(monkeypatch):
    monkeypatch.delenv("PARTNERIZE_APPLICATION_KEY", raising=False)
    monkeypatch.delenv("PARTNERIZE_API_KEY", raising=False)
    monkeypatch.setenv("PARTNERIZE_PUBLISHER_ID", "publisher-id")
    fake = Mock()
    with pytest.raises(PartnerizeError, match="credentials are not configured"):
        PartnerizeClient(client=fake).discover_campaigns()
    fake.request.assert_not_called()


def test_http_401_is_reported_without_echoing_credentials(monkeypatch):
    monkeypatch.setenv("PARTNERIZE_APPLICATION_KEY", "secret-app")
    monkeypatch.setenv("PARTNERIZE_API_KEY", "secret-api")
    monkeypatch.setenv("PARTNERIZE_PUBLISHER_ID", "publisher-id")
    fake = Mock()
    bad = response()
    bad.raise_for_status.side_effect = httpx.HTTPStatusError(
        "unauthorized", request=httpx.Request("GET", "https://api.partnerize.com"),
        response=httpx.Response(401, request=httpx.Request("GET", "https://api.partnerize.com"))
    )
    fake.request.return_value = bad
    with pytest.raises(PartnerizeError, match="rejected the API credentials") as err:
        PartnerizeClient(client=fake).discover_campaigns()
    assert "secret-app" not in str(err.value)
    assert "secret-api" not in str(err.value)
