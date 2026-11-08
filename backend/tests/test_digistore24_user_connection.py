from unittest.mock import Mock, patch

import httpx
import pytest

from app.services.digistore24_user_connection import (
    Digistore24ConnectionError,
    _call,
)


@pytest.fixture(autouse=True)
def developer_key(monkeypatch):
    monkeypatch.setenv("DIGISTORE24_DEVELOPER_API_KEY", "test-developer-key")


_SUCCESS = {"result": "success"}
_UNSET = object()


def _response(status_code=200, payload=_UNSET):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = _SUCCESS if payload is _UNSET else payload
    response.raise_for_status.return_value = None
    return response


def test_call_returns_success_payload():
    response = _response(payload={"result": "success", "data": {"ok": True}})
    with patch("app.services.digistore24_user_connection.httpx.get", return_value=response):
        assert _call("requestApiKey", {"permissions": "writable"}) == {
            "result": "success",
            "data": {"ok": True},
        }


def test_call_http_status_error_logs_only_safe_metadata(caplog):
    response = _response(status_code=502)
    request = httpx.Request("GET", "https://www.digistore24.com/api/call/requestApiKey")
    response.request = request
    response.status_code = 502
    response.json.return_value = {"code": "INVALID_RETURN_URL", "message": "secret vendor response"}
    error = httpx.HTTPStatusError("secret vendor response", request=request, response=response)

    with patch("app.services.digistore24_user_connection.httpx.get", side_effect=error):
        with pytest.raises(Digistore24ConnectionError, match="API request failed"):
            _call("requestApiKey", {"token": "secret-token"})

    assert "status=502" in caplog.text
    assert "vendor_code=INVALID_RETURN_URL" in caplog.text
    assert "secret-token" not in caplog.text
    assert "test-developer-key" not in caplog.text
    assert "secret vendor response" not in caplog.text


def test_call_vendor_code_is_not_logged_if_unconstrained(caplog):
    response = _response(status_code=400)
    request = httpx.Request("GET", "https://www.digistore24.com/api/call/requestApiKey")
    response.request = request
    response.status_code = 400
    response.json.return_value = {"code": "unsafe code with PII", "message": "private"}
    error = httpx.HTTPStatusError("private", request=request, response=response)

    with patch("app.services.digistore24_user_connection.httpx.get", side_effect=error):
        with pytest.raises(Digistore24ConnectionError):
            _call("requestApiKey", {})

    assert "vendor_code=unavailable" in caplog.text
    assert "unsafe code with PII" not in caplog.text
    assert "private" not in caplog.text


def test_call_transport_error_is_safe():
    with patch(
        "app.services.digistore24_user_connection.httpx.get",
        side_effect=httpx.ConnectError("secret host details"),
    ):
        with pytest.raises(Digistore24ConnectionError, match="Could not reach Digistore24"):
            _call("requestApiKey", {})


def test_call_invalid_json_is_safe():
    response = _response()
    response.json.side_effect = ValueError("sensitive body")
    with patch("app.services.digistore24_user_connection.httpx.get", return_value=response):
        with pytest.raises(Digistore24ConnectionError, match="invalid response"):
            _call("requestApiKey", {})


@pytest.mark.parametrize("payload", [None, [], {"result": "error", "message": "secret detail"}])
def test_call_vendor_non_success_is_safe(payload, caplog):
    response = _response(payload=payload)
    with patch("app.services.digistore24_user_connection.httpx.get", return_value=response):
        with pytest.raises(Digistore24ConnectionError, match="did not confirm"):
            _call("requestApiKey", {})

    assert "secret detail" not in caplog.text

from app.services.digistore24_user_connection import request_user_api_key


def test_request_user_api_key_sends_documented_parameters():
    payload = {
        "result": "success",
        "data": {
            "request_token": "request-token",
            "request_url": "https://www.digistore24.com/authorize",
        },
    }
    with patch("app.services.digistore24_user_connection._call", return_value=payload) as call:
        result = request_user_api_key("https://core.example.com/callback?state=abc")

    assert result == {
        "request_token": "request-token",
        "request_url": "https://www.digistore24.com/authorize",
    }
    call.assert_called_once_with(
        "requestApiKey",
        {
            "permissions": "readonly",
            "return_url": "https://core.example.com/callback?state=abc",
        },
    )


def test_request_user_api_key_rejects_non_https_callback():
    with pytest.raises(Digistore24ConnectionError, match="HTTPS"):
        request_user_api_key("http://core.example.com/callback")



def test_unregister_user_api_key_uses_user_key_and_delete():
    from app.services.digistore24_user_connection import unregister_user_api_key

    response = _response(payload={"result": "success", "modified": True})
    with patch("app.services.digistore24_user_connection.httpx.delete", return_value=response) as delete:
        unregister_user_api_key("user-api-key")

    delete.assert_called_once_with(
        "https://www.digistore24.com/api/call/unregister",
        headers={"X-DS-API-KEY": "user-api-key", "Accept": "application/json"},
        timeout=15.0,
    )


def test_unregister_user_api_key_rejects_empty_key():
    from app.services.digistore24_user_connection import unregister_user_api_key

    with pytest.raises(Digistore24ConnectionError, match="invalid"):
        unregister_user_api_key("  ")


def test_unregister_user_api_key_failure_is_safe():
    from app.services.digistore24_user_connection import unregister_user_api_key

    response = _response(status_code=401)
    request = httpx.Request("DELETE", "https://www.digistore24.com/api/call/unregister")
    response.request = request
    error = httpx.HTTPStatusError("secret", request=request, response=response)
    with patch("app.services.digistore24_user_connection.httpx.delete", side_effect=error):
        with pytest.raises(Digistore24ConnectionError, match="could not revoke"):
            unregister_user_api_key("secret-user-key")
