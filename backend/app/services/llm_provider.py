"""LLM provider abstractions for AEA Core.

This module introduces a small provider layer so services can depend on an
abstract interface instead of a vendor-specific SDK. The default provider is a
simple deterministic mock that is safe for tests and local development.
"""

from __future__ import annotations

import os
from typing import Any

from app.config import settings


PROVIDER_REGISTRY: dict[str, type["BaseLLMProvider"]] = {}


class BaseLLMProvider:
    """Abstract interface for LLM integrations."""

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        """Generate a response for the provided prompt.

        Args:
            prompt: The user prompt to send to the model.
            system_prompt: An optional system prompt.

        Returns:
            A structured dictionary describing the response outcome.
        """

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
    """Groq-backed provider with defensive error handling."""

    def __init__(self) -> None:
        """Initialize the Groq provider from environment configuration."""

        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.model = os.getenv("GROQ_MODEL", "") or getattr(settings, "groq_model", "") or "llama-3.1-8b-instant"

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        """Generate a response from Groq when configuration is available."""

        try:
            if not self.api_key:
                return self._error_response("Missing GROQ_API_KEY")

            try:
                import groq  # type: ignore
            except Exception:
                return self._error_response("Groq SDK not available")

            client = groq.Groq(api_key=self.api_key)
            messages: list[dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
            )

            content = ""
            if getattr(response, "choices", None):
                first_choice = response.choices[0]
                if getattr(first_choice, "message", None) is not None:
                    content = getattr(first_choice.message, "content", "") or ""

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
            return self._error_response(str(exc))

    def _error_response(self, error: str) -> dict[str, Any]:
        """Build a structured error response without raising."""

        return {
            "success": False,
            "content": "",
            "model": self.model,
            "provider": "groq",
            "usage": {},
            "error": error,
        }



class KiloGatewayLLMProvider(BaseLLMProvider):
    """Kilo Gateway provider; supports free model routing for AEA planning tasks."""

    def __init__(self) -> None:
        self.api_key = os.getenv("KILO_API_KEY", "").strip()
        self.model = os.getenv("KILO_MODEL", "kilo-auto/free").strip() or "kilo-auto/free"
        self.base_url = os.getenv("KILO_BASE_URL", "https://ai-gateway.kilo.ai/api/v1").rstrip("/")

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        try:
            # Kilo permits anonymous access to free models; paid models require a key.
            if not self.api_key and self.model != "kilo-auto/free" and not self.model.endswith(":free"):
                return self._error_response("Missing KILO_API_KEY for a non-free Kilo model")
            import httpx

            messages: list[dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={"model": self.model, "messages": messages, "temperature": 0.3},
                timeout=90.0,
            )
            if response.status_code != 200:
                return self._error_response(f"Kilo Gateway request failed (HTTP {response.status_code})")
            data = response.json()
            choices = data.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
            content = message.get("content", "")
            if not isinstance(content, str) or not content.strip():
                return self._error_response("Kilo Gateway returned an empty response")
            usage_data = data.get("usage") or {}
            return {
                "success": True,
                "content": content,
                "model": data.get("model", self.model),
                "provider": "kilo",
                "usage": {
                    "prompt_tokens": usage_data.get("prompt_tokens", 0),
                    "completion_tokens": usage_data.get("completion_tokens", 0),
                    "total_tokens": usage_data.get("total_tokens", 0),
                },
            }
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return self._error_response(str(exc))

    def _error_response(self, error: str) -> dict[str, Any]:
        return {
            "success": False,
            "content": "",
            "model": self.model,
            "provider": "kilo",
            "usage": {},
            "error": error,
        }


def register_llm_provider(name: str, provider_cls: type[BaseLLMProvider]) -> None:
    """Register a provider implementation for configuration-based selection."""

    PROVIDER_REGISTRY[name.strip().lower()] = provider_cls


register_llm_provider("mock", MockLLMProvider)
register_llm_provider("groq", GroqLLMProvider)
register_llm_provider("kilo", KiloGatewayLLMProvider)


def get_llm_provider(provider_name: str | None = None) -> BaseLLMProvider:
    """Return the configured LLM provider instance."""

    configured_name = provider_name or os.getenv("LLM_PROVIDER", "") or getattr(settings, "llm_provider", "") or "mock"
    name = configured_name.strip().lower()
    provider_cls = PROVIDER_REGISTRY.get(name)
    if provider_cls is None:
        return MockLLMProvider()
    return provider_cls()


__all__ = ["BaseLLMProvider", "MockLLMProvider", "GroqLLMProvider", "KiloGatewayLLMProvider", "get_llm_provider"]
