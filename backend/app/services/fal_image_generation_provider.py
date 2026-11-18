"""fal.ai queue API adapter for AEA image generation.

All fal credentials stay server-side. Queue IO retention is disabled and
results are downloaded into memory so callers can persist them to private
Supabase Storage rather than relying on fal CDN URLs.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.image_generation_budget import (
    ImageBudgetExceeded,
    SupabaseImageBudget,
)
from app.services.image_generation_provider import (
    GeneratedImage,
    ImageProviderNotConfigured,
)

FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_MODEL_IDS = {
    "z_image_turbo": "fal-ai/z-image/turbo",
    "nano_banana_pro": "fal-ai/nano-banana-pro",
}
# Conservative reservations: Z-Image Turbo assumes up to 4 MP at $0.005/MP;
# Nano Banana Pro reserves the published 1K/2K unit price.
MODEL_RESERVATIONS_USD = {
    "z_image_turbo": Decimal("0.02"),
    "nano_banana_pro": Decimal("0.15"),
}
MODEL_ESTIMATED_COST_USD = {
    "z_image_turbo": Decimal("0.02"),
    "nano_banana_pro": Decimal("0.15"),
}


class FalImageGenerationProvider:
    name = "fal.ai"

    def __init__(
        self,
        budget: SupabaseImageBudget,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 180.0,
        poll_interval_seconds: float = 1.5,
        max_polls: int = 100,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("FAL_KEY", "")
        if not self.api_key:
            raise ImageProviderNotConfigured("FAL_KEY is not configured on the server")
        self.budget = budget
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_polls = max_polls
        self.client = client

    @staticmethod
    def _validated_json_response(
        response: httpx.Response, *, operation: str, secret: str | None = None
    ) -> dict[str, Any]:
        """Require a successful HTTP response containing a JSON object.

        Error diagnostics intentionally include only status, content type and a
        short redacted body excerpt; request headers and signed URLs are omitted.
        """
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = (response.text or "")[:500]
            if secret:
                body = body.replace(secret, "[REDACTED]")
            body = re.sub(r"(?i)(authorization\\s*[:=]\\s*)(?:key|bearer)\\s+[^\\s,;]+", r"\\1[REDACTED]", body)
            content_type = response.headers.get("content-type", "unknown")
            raise RuntimeError(
                f"fal.ai {operation} returned HTTP {response.status_code} "
                f"(content-type={content_type}): {body}"
            ) from exc

        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            content_type = response.headers.get("content-type", "unknown")
            raise RuntimeError(
                f"fal.ai {operation} returned invalid JSON "
                f"(HTTP {response.status_code}, content-type={content_type})"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"fal.ai {operation} returned a non-object JSON payload "
                f"(HTTP {response.status_code})"
            )
        return payload

    @staticmethod
    def _trusted_url(value: Any, *, hosts: tuple[str, ...], label: str) -> str:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"fal.ai returned a missing or invalid {label}")
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        trusted = any(hostname == host or hostname.endswith("." + host) for host in hosts)
        if parsed.scheme != "https" or not trusted or parsed.username or parsed.password:
            raise RuntimeError(f"fal.ai returned an untrusted {label}")
        return value

    @staticmethod
    def _dimensions(aspect_ratio: str) -> tuple[int, int]:
        dimensions = {
            "1:1": (1024, 1024),
            "4:5": (1024, 1280),
            "9:16": (1024, 1792),
            "16:9": (1792, 1024),
            "3:2": (1536, 1024),
        }
        if aspect_ratio not in dimensions:
            raise ValueError("Unsupported image aspect ratio")
        return dimensions[aspect_ratio]

    @staticmethod
    def _model_key(model: str | None) -> str:
        key = model or "z_image_turbo"
        if key not in FAL_MODEL_IDS:
            raise ValueError(f"Unsupported fal.ai image model: {key}")
        return key

    async def generate(
        self, *, prompt: str, aspect_ratio: str, model: str | None = None
    ) -> GeneratedImage:
        if not prompt or len(prompt) > 12000:
            raise ValueError("Prompt must contain 1–12000 characters")
        model_key = self._model_key(model)
        width, height = self._dimensions(aspect_ratio)
        job_id = str(uuid.uuid4())
        reservation = MODEL_RESERVATIONS_USD[model_key]
        # Reservation is atomic and occurs before the first billable request.
        self.budget.reserve(job_id, reservation)

        own_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout_seconds)
        headers = {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
            "X-Fal-Store-IO": "0",
        }
        endpoint = f"{FAL_QUEUE_BASE}/{FAL_MODEL_IDS[model_key]}"
        submitted_to_provider = False
        try:
            response = await client.post(
                endpoint,
                headers=headers,
                json={
                    "prompt": prompt,
                    **(
                        {"aspect_ratio": aspect_ratio, "resolution": "1K"}
                        if model_key == "nano_banana_pro"
                        else {"image_size": {"width": width, "height": height}}
                    ),
                    "num_images": 1,
                    "output_format": "png",
                },
            )
            submitted = self._validated_json_response(
                response, operation="queue submission", secret=self.api_key
            )
            submitted_to_provider = True
            status_url = self._trusted_url(
                submitted.get("status_url"),
                hosts=("queue.fal.run",),
                label="queue status URL",
            )
            response_url = self._trusted_url(
                submitted.get("response_url"),
                hosts=("queue.fal.run",),
                label="queue response URL",
            )

            payload: dict[str, Any] | None = None
            for _ in range(self.max_polls):
                status = await client.get(
                    status_url, headers={"Authorization": f"Key {self.api_key}"}
                )
                status_payload = self._validated_json_response(
                    status, operation="queue status", secret=self.api_key
                )
                state = status_payload.get("status")
                if not isinstance(state, str):
                    raise RuntimeError("fal.ai queue status response omitted a valid status")
                if state == "COMPLETED":
                    result = await client.get(
                        response_url, headers={"Authorization": f"Key {self.api_key}"}
                    )
                    payload = self._validated_json_response(
                        result, operation="generation result", secret=self.api_key
                    )
                    break
                if state in {"FAILED", "CANCELLED"}:
                    raise RuntimeError(f"fal.ai generation ended with status {state}")
                await asyncio.sleep(self.poll_interval_seconds)
            if payload is None:
                raise TimeoutError("fal.ai generation did not complete before timeout")

            images = payload.get("images")
            if not isinstance(images, list) or not images or not isinstance(images[0], dict):
                raise RuntimeError("fal.ai response did not contain a valid images array")
            image_url = self._trusted_url(
                images[0].get("url"),
                hosts=("storage.googleapis.com", "fal.media", "fal.ai"),
                label="generated image URL",
            )
            media = await client.get(image_url)
            try:
                media.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"fal.ai generated-image download returned HTTP {media.status_code}"
                ) from exc
            content = media.content
            if not content or len(content) > 20 * 1024 * 1024:
                raise RuntimeError("Generated image is empty or exceeds the 20 MB limit")
            mime_type = (images[0].get("content_type") or media.headers.get("content-type", "")).split(";")[0].lower()
            if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise RuntimeError(f"Unsupported fal.ai image MIME type: {mime_type}")
            actual = MODEL_ESTIMATED_COST_USD[model_key]
            image_width = images[0].get("width") or width
            image_height = images[0].get("height") or height
            if (
                isinstance(image_width, bool)
                or isinstance(image_height, bool)
                or not isinstance(image_width, int)
                or not isinstance(image_height, int)
                or image_width <= 0
                or image_height <= 0
            ):
                raise RuntimeError("fal.ai returned invalid image dimensions")
            self.budget.settle(job_id, actual)
            return GeneratedImage(
                content=content,
                mime_type=mime_type,
                width=image_width,
                height=image_height,
                provider=self.name,
                model=FAL_MODEL_IDS[model_key],
                provider_metadata={
                    "request_id": submitted.get("request_id"),
                    "model_key": model_key,
                    "estimated_cost_usd": str(actual),
                },
            )
        except Exception:
            # Once fal accepts a request, its charge may be incurred even if
            # polling, download, or persistence fails. Keep the reservation in
            # that uncertain case; release only when submission never succeeded.
            if not submitted_to_provider:
                try:
                    self.budget.release(job_id)
                except Exception:
                    pass
            raise
        finally:
            if own_client:
                await client.aclose()
