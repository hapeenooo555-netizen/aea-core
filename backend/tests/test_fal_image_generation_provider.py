from decimal import Decimal

import pytest

from app.services.fal_image_generation_provider import (
    FalImageGenerationProvider,
    MODEL_RESERVATIONS_USD,
)
from app.services.image_generation_budget import ImageBudgetExceeded


class Budget:
    def __init__(self, allow=True):
        self.allow = allow
        self.reserved = []
        self.released = []

    def reserve(self, job_id, amount):
        self.reserved.append((job_id, amount))
        if not self.allow:
            raise ImageBudgetExceeded("budget exhausted")

    def release(self, job_id):
        self.released.append(job_id)


def test_model_reservations_are_within_trial_cap():
    assert MODEL_RESERVATIONS_USD["z_image_turbo"] == Decimal("0.02")
    assert MODEL_RESERVATIONS_USD["nano_banana_pro"] == Decimal("0.15")
    assert sum(MODEL_RESERVATIONS_USD.values()) < Decimal("10.00")


def test_fal_provider_requires_server_side_api_key():
    with pytest.raises(Exception, match="FAL_KEY"):
        FalImageGenerationProvider(Budget(), api_key="")


def test_dimensions_supports_aea_aspect_ratios():
    assert FalImageGenerationProvider._dimensions("1:1") == (1024, 1024)
    assert FalImageGenerationProvider._dimensions("9:16") == (1024, 1792)
    assert FalImageGenerationProvider._dimensions("2:3") == (1024, 1536)
    with pytest.raises(ValueError):
        FalImageGenerationProvider._dimensions("7:5")


def test_unknown_model_is_rejected_before_provider_call():
    with pytest.raises(ValueError, match="Unsupported fal.ai"):
        FalImageGenerationProvider._model_key("unknown")


def test_budget_exhaustion_is_a_hard_preflight_failure():
    budget = Budget(allow=False)
    with pytest.raises(ImageBudgetExceeded):
        budget.reserve("job-1", Decimal("0.15"))
    assert budget.reserved


import asyncio

import httpx


class SettlingBudget(Budget):
    def __init__(self, allow=True):
        super().__init__(allow=allow)
        self.settled = []

    def settle(self, job_id, amount):
        self.settled.append((job_id, amount))


def test_generate_uses_injected_http_client_and_settles_budget():
    requests = []
    image_bytes = b"\x89PNG\r\n\x1a\nmock-image"

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "request_id": "request-123",
                    "status_url": "https://queue.fal.run/tasks/123/status",
                    "response_url": "https://queue.fal.run/tasks/123",
                },
            )
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.host == "queue.fal.run":
            return httpx.Response(
                200,
                json={
                    "images": [{
                        "url": "https://cdn.fal.media/mock.png",
                        "content_type": "image/png",
                        "width": 1024,
                        "height": 1024,
                    }]
                },
            )
        if request.url.host == "cdn.fal.media":
            return httpx.Response(200, content=image_bytes, headers={"content-type": "image/png"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def run():
        budget = SettlingBudget()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = FalImageGenerationProvider(
                budget,
                api_key="test-fal-key",
                client=client,
                poll_interval_seconds=0,
            )
            generated = await provider.generate(
                prompt="Create a clean AEA promotional graphic",
                aspect_ratio="1:1",
                model="z_image_turbo",
            )
            assert not client.is_closed

        assert generated.content == image_bytes
        assert generated.mime_type == "image/png"
        assert (generated.width, generated.height) == (1024, 1024)
        assert generated.provider == "fal.ai"
        assert generated.model == "fal-ai/z-image/turbo"
        assert generated.provider_metadata["request_id"] == "request-123"
        assert len(budget.reserved) == 1
        assert budget.settled == [(budget.reserved[0][0], Decimal("0.02"))]
        assert budget.released == []
        assert [request.method for request in requests] == ["POST", "GET", "GET", "GET"]
        assert requests[0].headers["authorization"] == "Key test-fal-key"

    asyncio.run(run())


def test_generate_releases_budget_when_provider_rejects_submission():
    def handler(request):
        return httpx.Response(503, json={"error": "provider unavailable"})

    async def run():
        budget = SettlingBudget()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = FalImageGenerationProvider(
                budget, api_key="test-fal-key", client=client
            )
            with pytest.raises(RuntimeError, match="HTTP 503"):
                await provider.generate(
                    prompt="Create a clean AEA promotional graphic",
                    aspect_ratio="1:1",
                )
        assert len(budget.reserved) == 1
        assert budget.released == [budget.reserved[0][0]]
        assert budget.settled == []

    asyncio.run(run())
