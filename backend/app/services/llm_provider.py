"""LLM provider abstractions for AEA Core.

Groq requests can fail when an individual model reaches its organization-level
rate limit. The provider therefore supports model failover for HTTP 429 while
keeping credentials and raw provider errors out of returned diagnostics.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

PROVIDER_REGISTRY: dict[str, type["BaseLLMProvider"]] = {}
DEFAULT_GROQ_FALLBACK_MODELS = ("openai/gpt-oss-20b", "qwen/qwen3.8-27b")


class BaseLLMProvider:
    """Abstract interface for LLM integrations."""

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        """Generate a response for the provided prompt."""
        raise NotImplementedError


class MockLLMProvider(BaseLLMProvider):
    """Deterministic provider for tests and local environments."""

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        return {
            "success": True,
            "content": f"Mock response: {prompt}",
            "model": "mock",
            "provider": "mock",
            "usage": {},
        }


class UnavailableLLMProvider(BaseLLMProvider):
    """Fail-closed provider used when configuration names an unknown backend."""

    def __init__(self, configured_name: str) -> None:
        self.configured_name = configured_name

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        logger.error("Unknown LLM provider configured (%s)", self.configured_name)
        return {
            "success": False,
            "content": "",
            "model": "unavailable",
            "provider": "unavailable",
            "usage": {},
            "error": "Configured LLM provider is not registered. Configure a supported provider.",
        }


class GroqLLMProvider(BaseLLMProvider):
    """Groq-backed provider with rate-limit-aware model failover."""

    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.model = (
            os.getenv("GROQ_MODEL", "")
            or getattr(settings, "groq_model", "")
            or "openai/gpt-oss-120b"
        )
        configured_fallbacks = os.getenv("GROQ_FALLBACK_MODELS", "")
        if configured_fallbacks.strip():
            fallbacks = tuple(
                item.strip() for item in configured_fallbacks.split(",") if item.strip()
            )
        else:
            fallbacks = DEFAULT_GROQ_FALLBACK_MODELS
        self.fallback_models = tuple(dict.fromkeys(
            model for model in fallbacks if model and model != self.model
        ))

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        status = getattr(exc, "status_code", None)
        if status is None:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
        try:
            return int(status) if status is not None else None
        except (TypeError, ValueError):
            return None

    def generate(
        self, prompt: str, system_prompt: str | None = None,
        *, response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            return self._error_response("Missing GROQ_API_KEY")

        try:
            import groq  # type: ignore
        except Exception:
            return self._error_response("Groq SDK not available")

        try:
            client = groq.Groq(api_key=self.api_key)
        except Exception as exc:
            logger.error("Groq client initialization failed (%s)", type(exc).__name__)
            return self._error_response("Groq client initialization failed")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        models = (self.model, *self.fallback_models)
        last_rate_limited_model: str | None = None
        for model in models:
            response_format: dict[str, Any] = {"type": "json_object"}
            if response_schema is not None and model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
                response_format = {
                    "type": "json_schema",
                    "json_schema": {"name": "studio_book", "strict": True, "schema": response_schema},
                }
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                )
            except Exception as exc:
                status_code = self._status_code(exc)
                if status_code == 429:
                    last_rate_limited_model = model
                    logger.warning(
                        "Groq model rate-limited; trying configured fallback (model=%s)",
                        model,
                    )
                    continue
                logger.error(
                    "Groq generation failed (model=%s status=%s error_type=%s)",
                    model,
                    status_code or "unknown",
                    type(exc).__name__,
                )
                return self._error_response(
                    "Groq request failed. Check provider availability and model configuration.",
                    model=model,
                )

            choices = getattr(response, "choices", None) or []
            if not choices:
                return self._error_response(
                    "Groq returned no completion choices", model=model
                )

            first_choice = choices[0]
            message = getattr(first_choice, "message", None)
            content = getattr(message, "content", None) if message is not None else None
            if isinstance(content, list):
                content = "".join(
                    str(block.get("text", ""))
                    if isinstance(block, dict)
                    else str(getattr(block, "text", "") or "")
                    for block in content
                )
            if not isinstance(content, str) or not content.strip():
                return self._error_response(
                    "Groq returned empty content", model=model
                )

            usage = {}
            if getattr(response, "usage", None) is not None:
                usage = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                    "total_tokens": getattr(response.usage, "total_tokens", 0),
                }

            if model != self.model:
                logger.info(
                    "Groq generation succeeded using fallback model (primary=%s fallback=%s)",
                    self.model,
                    model,
                )
            return {
                "success": True,
                "content": content,
                "model": model,
                "provider": "groq",
                "usage": usage,
            }

        if last_rate_limited_model:
            logger.error(
                "Groq rate limits exhausted for primary and fallback models (last_model=%s)",
                last_rate_limited_model,
            )
            return self._error_response(
                "Groq rate limit reached for all configured models. Please retry after the provider quota resets or configure another available model.",
                model=last_rate_limited_model,
            )
        return self._error_response("Groq generation failed for all configured models.")

    def _error_response(self, error: str, model: str | None = None) -> dict[str, Any]:
        """Build a bounded, credential-free error response."""
        return {
            "success": False,
            "content": "",
            "model": model or self.model,
            "provider": "groq",
            "usage": {},
            "error": error,
        }


def register_llm_provider(name: str, provider_cls: type[BaseLLMProvider]) -> None:
    """Register a provider implementation for configuration-based selection."""
    PROVIDER_REGISTRY[name.strip().lower()] = provider_cls


register_llm_provider("mock", MockLLMProvider)
register_llm_provider("groq", GroqLLMProvider)


def get_llm_provider(provider_name: str | None = None) -> BaseLLMProvider:
    """Return the configured LLM provider instance."""
    runtime_environment = (
        os.getenv("AEA_ENVIRONMENT")
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or ("production" if os.getenv("RENDER") or os.getenv("RAILWAY_ENVIRONMENT") else "")
    ).strip().lower()
    is_production = runtime_environment in {"prod", "production"}

    explicit_provider = provider_name or os.getenv("LLM_PROVIDER", "").strip()
    if is_production and not explicit_provider:
        # Production must opt into a real provider; Settings' local/test
        # default of "mock" must never silently become live AI behavior.
        return UnavailableLLMProvider("unset")

    configured_name = (
        explicit_provider
        or getattr(settings, "llm_provider", "")
        or "mock"
    )
    normalized_name = configured_name.strip().lower()
    if is_production and normalized_name == "mock":
        # An explicit mock selection is still unsafe in production because
        # callers could mistake deterministic fixture text for AI output.
        return UnavailableLLMProvider("mock-disabled-in-production")
    provider_cls = PROVIDER_REGISTRY.get(normalized_name)
    if provider_cls is None:
        # Never disguise a typo or unsupported provider as a successful mock.
        return UnavailableLLMProvider(normalized_name or "unset")
    return provider_cls()


__all__ = [
    "BaseLLMProvider",
    "MockLLMProvider",
    "GroqLLMProvider",
    "UnavailableLLMProvider",
    "get_llm_provider",
]
