from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers import health
from app.services import agent_orchestrator, llm_provider


class FakeGroqProvider:
    model = "openai/gpt-oss-120b"
    api_key = "test-key"


def _stub_dependencies(monkeypatch, provider):
    monkeypatch.setattr(agent_orchestrator, "AgentOrchestrator", type(
        "HealthyOrchestrator",
        (),
        {"health_check": lambda self: {"status": "healthy", "services": {"decision_engine": True}}},
    ))
    monkeypatch.setattr(health, "is_supabase_configured", lambda: True)
    monkeypatch.setattr(health, "check_supabase_connection", lambda: True)
    monkeypatch.setattr(llm_provider, "get_llm_provider", lambda: provider)


def test_health_does_not_report_mock_as_configured_or_ready(monkeypatch):
    _stub_dependencies(monkeypatch, llm_provider.MockLLMProvider())

    result = health.health_check()

    assert result["llm"]["provider"] == "mock"
    assert result["llm"]["is_mock"] is True
    assert result["llm"]["configured"] is False
    assert result["llm"]["ready"] is False
    assert result["llm"]["readiness_check"] == "not_performed"
    assert result["status"] == "degraded"


def test_health_reports_groq_credentials_but_does_not_claim_inference_ready(monkeypatch):
    _stub_dependencies(monkeypatch, FakeGroqProvider())

    result = health.health_check()

    assert result["llm"]["provider"] == "groq"
    assert result["llm"]["configured"] is True
    assert result["llm"]["credential_present"] is True
    assert result["llm"]["ready"] is False
    assert result["llm"]["readiness_check"] == "not_performed"
    assert result["status"] == "healthy"


def test_health_reports_groq_unconfigured_when_key_missing(monkeypatch):
    provider = FakeGroqProvider()
    provider.api_key = ""
    _stub_dependencies(monkeypatch, provider)

    result = health.health_check()

    assert result["llm"]["configured"] is False
    assert result["llm"]["credential_present"] is False
    assert result["llm"]["ready"] is False
    assert result["status"] == "degraded"
