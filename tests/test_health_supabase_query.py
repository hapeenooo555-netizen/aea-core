"""Regression tests for the live Supabase query in /health."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import database
from app.routers import health


class FakeQuery:
    def __init__(self, should_raise=False):
        self.should_raise = should_raise
        self.selected = None
        self.limit_value = None
        self.executed = False

    def select(self, columns):
        self.selected = columns
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        self.executed = True
        if self.should_raise:
            raise RuntimeError("sensitive connection detail")
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, should_raise=False):
        self.query = FakeQuery(should_raise=should_raise)
        self.table_name = None

    def table(self, name):
        self.table_name = name
        return self.query


def test_connection_check_executes_read_only_limited_query(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(database, "_get_supabase_health_client", lambda: client)

    assert database.check_supabase_connection() is True
    assert client.table_name == "missions"
    assert client.query.selected == "id"
    assert client.query.limit_value == 1
    assert client.query.executed is True


def test_connection_check_returns_false_on_query_failure(monkeypatch):
    client = FakeClient(should_raise=True)
    monkeypatch.setattr(database, "_get_supabase_health_client", lambda: client)

    assert database.check_supabase_connection() is False


def test_health_client_uses_service_role_key_only_for_probe(monkeypatch):
    captured = {}
    client = FakeClient()

    def fake_create_client(url, key):
        captured["url"] = url
        captured["key"] = key
        return client

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "private-test-key")
    monkeypatch.setattr(database, "_supabase_health_client", None)
    monkeypatch.setitem(sys.modules, "supabase", SimpleNamespace(create_client=fake_create_client))

    assert database._get_supabase_health_client() is client
    assert captured == {
        "url": "https://example.supabase.co",
        "key": "private-test-key",
    }
    assert database.supabase_client is not client


def test_health_client_requires_service_role_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(database, "_supabase_health_client", None)

    assert database._get_supabase_health_client() is None


def test_health_reports_query_failure_as_degraded(monkeypatch):
    class FakeOrchestrator:
        def health_check(self):
            return {"status": "healthy", "services": {"mission_engine": True}}

    # The production orchestrator currently has an unrelated circular import.
    # Stub its module before the health endpoint performs its lazy import.
    fake_module = SimpleNamespace(AgentOrchestrator=FakeOrchestrator)
    monkeypatch.setitem(sys.modules, "app.services.agent_orchestrator", fake_module)
    monkeypatch.setattr(health, "is_supabase_configured", lambda: True)
    monkeypatch.setattr(health, "check_supabase_connection", lambda: False)

    result = health.health_check()

    assert result["supabase_configured"] is True
    assert result["supabase_connected"] is False
    assert result["supabase_query_ok"] is False
    assert result["status"] == "degraded"
