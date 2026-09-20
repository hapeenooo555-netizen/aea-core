"""P1-8 ownership hardening: ensure another user's mission cannot be read or executed.

These tests verify that ``MissionEngine.get_mission`` and
``AgentOrchestrator.run_mission`` scope lookups by ``owner_id``, so a caller
with a different owner_id cannot retrieve or execute another user's mission.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.agent_orchestrator import AgentOrchestrator
from app.services.mission_engine import MissionEngine
from app.services.mission_orchestration import MissionOrchestrationService


class _FakeResponse:
    def __init__(self, data: list | None = None):
        self.data = data or []


class _FakeQueryBuilder:
    def __init__(self, store: dict[str, dict[str, Any]]):
        self._store = store

    def select(self, _columns: str = "*"):
        return self

    def eq(self, column: str, value: Any):
        self._last_col = column
        self._last_val = value
        return self

    def limit(self, _n: int):
        return self

    def execute(self) -> _FakeResponse:
        for row in self._store.values():
            if row.get("id") == getattr(self, "_last_val", None):
                return _FakeResponse([row])
        return _FakeResponse([])


class _FakeTable:
    def __init__(self, store: dict[str, dict[str, Any]]):
        self._store = store
        self._last_eq: dict[str, Any] = {}

    def select(self, _columns: str = "*"):
        return self

    def eq(self, column: str, value: Any):
        self._last_eq[column] = value
        return self

    def limit(self, _n: int):
        return self

    def execute(self):
        results = []
        for row in self._store.values():
            match = all(row.get(k) == v for k, v in self._last_eq.items())
            if match:
                results.append(row)
        self._last_eq.clear()
        return _FakeResponse(results)

    def insert(self, payload: dict[str, Any]):
        self._last_eq.clear()
        self._store[payload["id"]] = payload
        return _FakeQueryBuilder(self._store)

    def update(self, _payload):
        return self


class _FakeClient:
    """Minimal Supabase client stub that filters by owner_id via eq()."""

    def __init__(self):
        self._table_name: str | None = None
        self._missions: dict[str, dict[str, Any]] = {}

    def table(self, name: str):
        self._table_name = name
        return _FakeTable(self._missions)

    @property
    def missions(self) -> dict[str, dict[str, Any]]:
        return self._missions


def _make_engine_with_mission(owner_id: str) -> MissionEngine:
    """Create an engine with a fake client containing one mission owned by owner_id."""
    client = _FakeClient()
    mission_id = "test-mission-id"
    client._missions[mission_id] = {
        "id": mission_id,
        "owner_id": owner_id,
        "title": "Affiliate task",
        "description": "Affiliate task",
        "objective": "Affiliate task",
        "status": "pending",
        "priority": "normal",
        "urgency": "normal",
        "business_importance": 1,
        "metadata": {},
        "result": {},
        "attempt_count": 0,
        "max_attempts": 3,
        "next_retry_at": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "domain": "affiliate_jobs",
        "orchestration_idempotency_key": "some-key",
        "orchestration_claim_token": None,
        "orchestration_lease_expires_at": None,
        "scheduled_at": None,
        "recurrence": None,
        "dependencies": [],
    }
    return MissionEngine(client=client)


def test_owner_can_retrieve_their_own_mission():
    engine = _make_engine_with_mission("owner-a")
    retrieved = engine.get_mission("test-mission-id", owner_id="owner-a")
    assert retrieved is not None
    assert retrieved["owner_id"] == "owner-a"


def test_another_user_cannot_retrieve_someone_elses_mission():
    engine = _make_engine_with_mission("owner-a")
    retrieved = engine.get_mission("test-mission-id", owner_id="owner-b")
    assert retrieved is None


def test_another_user_cannot_run_someone_elses_mission():
    """AgentOrchestrator.run_mission must scope the lookup to the authenticated owner."""
    client = _FakeClient()
    mission_id = "test-mission-run"
    client._missions[mission_id] = {
        "id": mission_id,
        "owner_id": "owner-a",
        "title": "Affiliate task",
        "description": "Affiliate task",
        "objective": "Affiliate task",
        "status": "pending",
        "priority": "normal",
        "urgency": "normal",
        "business_importance": 1,
        "metadata": {},
        "result": {},
        "attempt_count": 0,
        "max_attempts": 3,
        "next_retry_at": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "domain": "general",
        "orchestration_idempotency_key": None,
        "orchestration_claim_token": None,
        "orchestration_lease_expires_at": None,
        "scheduled_at": None,
        "recurrence": None,
        "dependencies": [],
    }

    orchestrator = AgentOrchestrator(owner_id="owner-b", client=client)

    result = orchestrator.run_mission(mission_id)
    assert result["success"] is False
    assert "not found" in result["error"].lower()
