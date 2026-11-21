"""LLM provider abstractions for AEA Core.

This module introduces a small provider layer so services can depend on an
abstract interface instead of a vendor-specific SDK. The default provider is a
simple deterministic mock that is safe for tests and local development.
"""

from __future__ import annotations

import os
import time
from typing import Any

from app.config import settings


PROVIDER_REGISTRY: dict[str, type["BaseLLMProvider"]] = {}
_TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
_MAX_PROVIDER_ATTEMPTS = 3


class BaseLLMProvider:
    """Abstract interface for LLM integrations."""

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        """Generate a response for the provided prompt."""
        raise NotImplementedError


class MockLLMProvider(BaseLLMProvider):
    """Deterministic provider for tests and local environments."""

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        """Return a predictable response without external API calls."""
        return {
            "success": True,
            "content": f"Mock response: {prompt}",
            "model": "mock",
            "provider": "mock",
            "usage": {},
        }


class GroqLLMProvider(BaseLLMProvider):
    """Groq-backed provider with defensive handling of transient outages."""

    def __init__(self) -> None:
        """Initialize the Groq provider from environment configuration."""
        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.model = os.getenv("GROQ_MODEL", "") or getattr(settings, "groq_model", "") or "llama-3.1-8b-instant"

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        """Retry only rate limits, temporary upstream failures and network timeouts."""
        status_code = getattr(exc, "status_code", None)
        if status_code in _TRANSIENT_HTTP_STATUSES:
            return True
        name = exc.__class__.__name__.lower()
        return any(token in name for token in ("timeout", "connection", "temporarilyunavailable"))

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        """Generate a response, retrying short-lived provider outages safely."""
        if not self.api_key:
            return self._error_response("Missing GROQ_API_KEY")

        try:
            import groq  # type: ignore
        except Exception:
            return self._error_response("Groq SDK not available")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(_MAX_PROVIDER_ATTEMPTS):
            try:
                client = groq.Groq(api_key=self.api_key)
                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
                choices = getattr(response, "choices", None) or []
                if not choices:
                    return self._error_response("Groq returned no completion choices")

                first_choice = choices[0]
                message = getattr(first_choice, "message", None)
                content = getattr(message, "content", None) if message is not None else None
                if isinstance(content, list):
                    content = "".join(
                        str(block.get("text", "")) if isinstance(block, dict) else str(getattr(block, "text", "") or "")
                        for block in content
                    )
                if not isinstance(content, str) or not content.strip():
                    finish_reason = getattr(first_choice, "finish_reason", None)
                    return self._error_response(f"Groq returned empty content (finish_reason={finish_reason})")

                usage = {}
                if getattr(response, "usage", None) is not None:
                    usage = {
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                        "total_tokens": getattr(response.usage, "total_tokens", 0),
                    }
                return {
                    "success": True,
                    "content": str(content),
                    "model": self.model,
                    "provider": "groq",
                    "usage": usage,
                }
            except Exception as exc:  # pragma: no cover - defensive runtime handling
                if attempt + 1 < _MAX_PROVIDER_ATTEMPTS and self._is_transient_error(exc):
                    # Bounded exponential backoff: 1s, then 2s.
                    time.sleep(2 ** attempt)
                    continue
                return self._error_response(str(exc))

        return self._error_response("Groq provider failed after bounded retries")

    def _error_response(self, error: str) -> dict[str, Any]:
        """Build a bounded provider error response."""
        return {
            "success": False,
            "content": "",
            "model": self.model,
            "provider": "groq",
            "usage": {},
            "error": error[:500],
        }


def register_llm_provider(name: str, provider_cls: type[BaseLLMProvider]) -> None:
    """Register a provider implementation for configuration-based selection."""
    PROVIDER_REGISTRY[name.strip().lower()] = provider_cls


register_llm_provider("mock", MockLLMProvider)
register_llm_provider("groq", GroqLLMProvider)


def get_llm_provider(provider_name: str | None = None) -> BaseLLMProvider:
    """Return the configured LLM provider instance."""
    configured_name = provider_name or os.getenv("LLM_PROVIDER", "") or getattr(settings, "llm_provider", "") or "mock"
    name = configured_name.strip().lower()
    provider_cls = PROVIDER_REGISTRY.get(name)
    if provider_cls is None:
        return MockLLMProvider()
    return provider_cls()


__all__ = ["BaseLLMProvider", "MockLLMProvider", "GroqLLMProvider", "get_llm_provider"]
