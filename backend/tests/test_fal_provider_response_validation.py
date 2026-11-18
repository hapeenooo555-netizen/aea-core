import httpx
import pytest

from app.services.fal_image_generation_provider import FalImageGenerationProvider


def _response(status_code: int, body: bytes, content_type: str = "application/json"):
    request = httpx.Request("GET", "https://queue.fal.run/test")
    return httpx.Response(
        status_code,
        content=body,
        headers={"content-type": content_type},
        request=request,
    )


def test_provider_response_validation_accepts_json_object():
    response = _response(200, b'{"status":"COMPLETED"}')
    assert FalImageGenerationProvider._validated_json_response(
        response, operation="test"
    ) == {"status": "COMPLETED"}


def test_provider_response_validation_rejects_invalid_json():
    response = _response(200, b"<html>not json</html>", "text/html")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        FalImageGenerationProvider._validated_json_response(
            response, operation="test"
        )


def test_provider_response_validation_rejects_non_object_json():
    response = _response(200, b'["unexpected"]')
    with pytest.raises(RuntimeError, match="non-object JSON"):
        FalImageGenerationProvider._validated_json_response(
            response, operation="test"
        )


def test_provider_response_validation_reports_http_status_without_leaking_key():
    response = _response(
        429,
        b'{"error":"rate limited","authorization":"Key secret-token-value"}',
    )
    with pytest.raises(RuntimeError) as caught:
        FalImageGenerationProvider._validated_json_response(
            response, operation="queue submission", secret="secret-token-value"
        )
    message = str(caught.value)
    assert "HTTP 429" in message
    assert "secret-token-value" not in message


def test_trusted_url_rejects_non_https_and_untrusted_hosts():
    with pytest.raises(RuntimeError, match="untrusted"):
        FalImageGenerationProvider._trusted_url(
            "http://queue.fal.run/task",
            hosts=("queue.fal.run",),
            label="queue URL",
        )
    with pytest.raises(RuntimeError, match="untrusted"):
        FalImageGenerationProvider._trusted_url(
            "https://attacker.example/task",
            hosts=("queue.fal.run",),
            label="queue URL",
        )


def test_trusted_url_accepts_fal_queue_subdomains():
    assert FalImageGenerationProvider._trusted_url(
        "https://queue-region.queue.fal.run/task",
        hosts=("queue.fal.run",),
        label="queue URL",
    ) == "https://queue-region.queue.fal.run/task"
