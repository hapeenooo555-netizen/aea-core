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

    assert details["success"] is True
    assert details["provider"] == "mock"
    assert "Mock response" in details["action"]


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
