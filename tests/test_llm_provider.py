from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings as app_settings
from app.services.decision_engine import AtlasDecisionEngine
from app.services.llm_provider import (
    BaseLLMProvider,
    GroqLLMProvider,
    MockLLMProvider,
    get_llm_provider,
)
from app.services.worker_runtime import WorkerRuntime


class StubLLMProvider(BaseLLMProvider):
    def generate(self, prompt: str, system_prompt: str | None = None) -> dict:
        return {
            "success": True,
            "content": "stubbed decision",
            "model": "stub-model",
            "provider": "stub",
            "usage": {},
        }


def test_mock_provider_returns_deterministic_response() -> None:
    provider = MockLLMProvider()

    response = provider.generate("plan the next step")

    assert response["success"] is True
    assert response["content"] == "Mock response: plan the next step"
    assert response["provider"] == "mock"
    assert response["model"] == "mock"


def test_factory_returns_mock_provider_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    provider = get_llm_provider()

    assert isinstance(provider, MockLLMProvider)


def test_factory_returns_groq_provider_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")

    provider = get_llm_provider()

    assert isinstance(provider, GroqLLMProvider)


def test_missing_api_key_returns_structured_error(monkeypatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    provider = GroqLLMProvider()

    response = provider.generate("hello")

    assert response["success"] is False
    assert response["provider"] == "groq"
    assert response["content"] == ""
    assert response["error"] == "Missing GROQ_API_KEY"


def test_decision_engine_uses_provided_provider() -> None:
    engine = AtlasDecisionEngine(provider=StubLLMProvider())

    assert engine.next_action() == "stubbed decision"


def test_decision_engine_returns_structured_contract() -> None:
    engine = AtlasDecisionEngine(provider=StubLLMProvider())

    decision = engine.next_action_details()

    assert decision["action"] == "stubbed decision"
    assert decision["success"] is True
    assert decision["provider"] == "stub"


def test_factory_selects_provider_by_name(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    provider = get_llm_provider("groq")

    assert isinstance(provider, GroqLLMProvider)


def test_worker_runtime_uses_default_provider(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    runtime = WorkerRuntime()

    assert isinstance(runtime._decision_engine._provider, MockLLMProvider)


def test_worker_runtime_uses_configured_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    runtime = WorkerRuntime()

    assert isinstance(runtime._decision_engine._provider, GroqLLMProvider)


def test_provider_failure_falls_back_to_mock(monkeypatch) -> None:
    class FailingProvider(BaseLLMProvider):
        def generate(self, prompt: str, system_prompt: str | None = None) -> dict:
            raise RuntimeError("boom")

    engine = AtlasDecisionEngine(provider=FailingProvider())
    details = engine.next_action_details()

    assert details["success"] is False
    assert details["provider"] == "fallback"
    assert details["status"] == "FALLBACK"
    assert "unavailable" in details["error"].lower()
    assert "Mock response" not in details["action"]


def test_get_llm_provider_uses_settings_configuration(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(app_settings, "llm_provider", "groq")
    monkeypatch.setattr(app_settings, "groq_model", "test-model")

    provider = get_llm_provider()

    assert isinstance(provider, GroqLLMProvider)
    assert provider.model == "test-model"


def test_worker_runtime_execution_flow(monkeypatch) -> None:
    runtime = WorkerRuntime()
    runtime._mission_engine = type("MissionStub", (), {"get_mission": lambda self, mission_id, owner_id=None, **kwargs: {"id": mission_id, "title": "Test", "worker_id": "w-1"}, "update_status": lambda *args, **kwargs: None, "complete_mission": lambda *args, **kwargs: None, "fail_mission": lambda *args, **kwargs: None})()
    runtime._memory_engine = type("MemoryStub", (), {"get_recent_memories": lambda self, worker_id, limit=5: [], "store_memory": lambda *args, **kwargs: None})()
    runtime._employee_engine = type("EmployeeEngineStub", (), {"run_mission": lambda self, mission_id: {"success": True, "mission_id": mission_id, "worker_id": "w-1"}})()

    result = runtime.execute_mission("mission-1")

    assert result["success"] is True
    assert result["mission_id"] == "mission-1"
    assert result["worker_id"] == "w-1"



def test_groq_provider_fails_over_to_next_model_after_rate_limit(monkeypatch) -> None:
    import types

    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.delenv("GROQ_FALLBACK_MODELS", raising=False)
    attempted: list[str] = []

    class RateLimitError(Exception):
        status_code = 429

    class Completion:
        def create(self, *, model, **_kwargs):
            attempted.append(model)
            if model == "openai/gpt-oss-120b":
                raise RateLimitError("quota detail must not escape")
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content='{"translations": []}')
                )],
                usage=None,
            )

    fake_groq = types.SimpleNamespace(
        Groq=lambda **_kwargs: types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=Completion()
            )
        )
    )
    monkeypatch.setitem(sys.modules, "groq", fake_groq)

    response = GroqLLMProvider().generate("translate scenes", "return JSON")

    assert response["success"] is True
    assert response["model"] == "openai/gpt-oss-20b"
    assert response["provider"] == "groq"
    assert attempted == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_groq_provider_returns_safe_error_when_all_models_are_rate_limited(monkeypatch) -> None:
    import types

    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    monkeypatch.setenv("GROQ_MODEL", "primary-model")
    monkeypatch.setenv("GROQ_FALLBACK_MODELS", "fallback-a,fallback-b")
    attempted: list[str] = []

    class RateLimitError(Exception):
        status_code = 429

    class Completion:
        def create(self, *, model, **_kwargs):
            attempted.append(model)
            raise RateLimitError("private provider quota details")

    fake_groq = types.SimpleNamespace(
        Groq=lambda **_kwargs: types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=Completion())
        )
    )
    monkeypatch.setitem(sys.modules, "groq", fake_groq)

    response = GroqLLMProvider().generate("translate")

    assert response["success"] is False
    assert attempted == ["primary-model", "fallback-a", "fallback-b"]
    assert "rate limit" in response["error"].lower()
    assert "private provider quota details" not in response["error"]


def test_groq_provider_does_not_fall_back_for_non_rate_limit_errors(monkeypatch) -> None:
    import types

    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    monkeypatch.setenv("GROQ_MODEL", "primary-model")
    monkeypatch.setenv("GROQ_FALLBACK_MODELS", "fallback-model")
    attempted: list[str] = []

    class AuthenticationError(Exception):
        status_code = 401

    class Completion:
        def create(self, *, model, **_kwargs):
            attempted.append(model)
            raise AuthenticationError("invalid credentials")

    fake_groq = types.SimpleNamespace(
        Groq=lambda **_kwargs: types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=Completion())
        )
    )
    monkeypatch.setitem(sys.modules, "groq", fake_groq)

    response = GroqLLMProvider().generate("translate")

    assert response["success"] is False
    assert attempted == ["primary-model"]
    assert "invalid credentials" not in response["error"]



def test_production_requires_explicit_real_provider(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(app_settings, "llm_provider", "mock")

    response = get_llm_provider().generate("must not fabricate AI output")

    assert response["success"] is False
    assert response["provider"] == "unavailable"
    assert response["content"] == ""


def test_production_rejects_explicit_mock_provider(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    response = get_llm_provider().generate("must not fabricate AI output")

    assert response["success"] is False
    assert response["provider"] == "unavailable"
    assert response["content"] == ""
    assert "not registered" in response["error"].lower()


def test_development_can_still_use_default_mock(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(app_settings, "llm_provider", "mock")

    assert isinstance(get_llm_provider(), MockLLMProvider)


def test_unknown_provider_fails_closed_instead_of_returning_mock(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "typo-provider")

    provider = get_llm_provider()
    response = provider.generate("must not be presented as AI output")

    assert response["success"] is False
    assert response["provider"] == "unavailable"
    assert response["content"] == ""
    assert "not registered" in response["error"].lower()


def test_unknown_explicit_provider_fails_closed() -> None:
    provider = get_llm_provider("unsupported-provider")

    assert provider.generate("hello")["success"] is False


def test_groq_uses_strict_json_schema_only_for_supported_models(monkeypatch):
    import types

    monkeypatch.setenv("GROQ_API_KEY", "unit-test")
    monkeypatch.setenv("GROQ_FALLBACK_MODELS", "llama-3.3-70b-versatile")
    attempted = []

    class RateLimitError(Exception):
        status_code = 429

    def create(**request):
        attempted.append(request["response_format"])
        if request["model"] == "openai/gpt-oss-120b":
            raise RateLimitError()
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"title": "Example"}'))],
            usage=None,
        )

    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    fake_client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=lambda **kwargs: fake_client))
    schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}

    result = GroqLLMProvider().generate("Write a book", "Return JSON", response_schema=schema)

    assert result["success"] is True
    assert attempted == [
        {"type": "json_schema", "json_schema": {"name": "studio_book", "strict": True, "schema": schema}},
        {"type": "json_object"},
    ]


def test_groq_fails_over_only_on_explicit_json_validation_error(monkeypatch, caplog):
    import types

    monkeypatch.setenv("GROQ_API_KEY", "unit-test")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_FALLBACK_MODELS", "openai/gpt-oss-20b")
    calls = []

    class BadRequest(Exception):
        status_code = 400
        response = types.SimpleNamespace(headers={"x-request-id": "req_unit_test"})
        body = {"error": {"code": "json_validate_failed", "failed_generation": "private draft content"}}

    def create(**request):
        calls.append(request["model"])
        if request["model"] == "openai/gpt-oss-120b":
            raise BadRequest()
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"title": "Example"}'))],
            usage=None,
        )

    fake_client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=lambda **kwargs: fake_client))
    provider = GroqLLMProvider()

    assert provider.generate("Write a book", response_schema={"type": "object"})["success"] is True
    assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert "req_unit_test" in caplog.text
    assert "private draft content" not in caplog.text

    calls.clear()
    BadRequest.body = {"error": {"code": "content_filter", "message": "private provider detail"}}
    blocked = provider.generate("Write a book", response_schema={"type": "object"})
    assert blocked["success"] is False
    assert blocked["provider_policy_block"] is True
    assert calls == ["openai/gpt-oss-120b"]
    assert "private provider detail" not in caplog.text


def test_groq_structured_failover_preserves_contract_on_qwen(monkeypatch):
    import types

    monkeypatch.setenv("GROQ_API_KEY", "unit-test")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.delenv("GROQ_FALLBACK_MODELS", raising=False)
    requests = []

    class RejectedOutput(Exception):
        status_code = 400
        body = {"error": {"code": "json_validate_failed"}}

    def create(**request):
        requests.append(request)
        if request["model"] != "qwen/qwen3.8-27b":
            raise RejectedOutput()
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"title": "Ukaguzi wa bidhaa"}'))],
            usage=None,
        )

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=lambda **_: client))
    schema = {
        "type": "object", "properties": {"title": {"type": "string"}},
        "required": ["title"], "additionalProperties": False,
    }

    response = GroqLLMProvider().generate("Andika kitabu kwa Kiswahili", response_schema=schema)

    assert response["success"] and response["model"] == "qwen/qwen3.8-27b"
    assert [request["model"] for request in requests] == [
        "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b",
    ]
    assert all(
        request["response_format"] == {
            "type": "json_schema", "json_schema": {"name": "studio_book", "strict": True, "schema": schema},
        }
        for request in requests
    )
