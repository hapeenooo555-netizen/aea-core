from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.memory_engine import AtlasMemoryEngine


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name: str):
        self._client = client
        self._table_name = table_name
        self._filters: list[tuple[str, object]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None
        self._operation: str | None = None
        self._payload: dict | None = None

    def insert(self, payload: dict):
        self._operation = "insert"
        self._payload = payload
        return self

    def select(self, *_args, **_kwargs):
        self._operation = "select"
        return self

    def eq(self, column: str, value: object):
        self._filters.append((column, value))
        return self

    def order(self, column: str, desc: bool = False):
        self._order = (column, desc)
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def delete(self):
        self._operation = "delete"
        return self

    def execute(self):
        if self._operation == "insert":
            assert self._table_name == "agent_memory_entries"
            row = {
                "id": "entry-1",
                "worker_id": self._payload["worker_id"],
                "owner_id": self._payload.get("owner_id"),
                "mission_id": self._payload.get("mission_id"),
                "memory_type": self._payload["memory_type"],
                "content": self._payload.get("content") or {},
                "metadata": self._payload.get("metadata", {}),
                "importance_score": self._payload.get("importance_score", 0.5),
                "created_at": "2024-01-01T00:00:00Z",
            }
            self._client.rows.append(row)
            return FakeResponse([row])

        if self._operation == "select":
            rows = list(self._client.rows)
            for column, value in self._filters:
                rows = [row for row in rows if row.get(column) == value]
            if self._order:
                rows = sorted(rows, key=lambda item: item.get(self._order[0], ""), reverse=self._order[1])
            if self._limit is not None:
                rows = rows[: self._limit]
            return FakeResponse(rows)

        if self._operation == "delete":
            filters = dict(self._filters)
            before = len(self._client.rows)
            self._client.rows = [row for row in self._client.rows if not all(row.get(column) == value for column, value in filters.items())]
            deleted = len(self._client.rows) < before
            return FakeResponse([{"id": filters.get("id")}] if deleted else [])

        return FakeResponse([])


class FakeSupabaseClient:
    def __init__(self):
        self.rows: list[dict] = []
        self.table_name: str | None = None

    def table(self, table_name: str):
        self.table_name = table_name
        return FakeQuery(self, table_name)


def test_store_memory_uses_agent_memory_entries_and_defaults():
    client = FakeSupabaseClient()
    engine = AtlasMemoryEngine(client=client, owner_id="owner-1")

    response = engine.store_memory(
        "worker-1",
        "note",
        {"mission_id": "mission-7", "text": "hello"},
    )

    assert response["success"] is True
    assert client.table_name == "agent_memory_entries"
    assert client.rows[0]["mission_id"] == "mission-7"
    assert client.rows[0]["metadata"] == {}
    assert client.rows[0]["importance_score"] == 0.5
    assert response["memory"]["content"]["text"] == "hello"
    assert response["memory"]["worker_id"] == "worker-1"


def test_get_recent_memories_returns_backward_compatible_shape():
    client = FakeSupabaseClient()
    client.rows = [
        {
            "id": "entry-2",
            "worker_id": "worker-1",
            "owner_id": "owner-1",
            "mission_id": "mission-9",
            "memory_type": "summary",
            "content": {"text": "hello"},
            "metadata": {"source": "unit-test"},
            "importance_score": 0.8,
            "created_at": "2024-01-01T00:00:00Z",
        }
    ]
    engine = AtlasMemoryEngine()
    engine._client = client

    memories = engine.get_recent_memories("worker-1", limit=5)

    assert len(memories) == 1
    assert memories[0]["id"] == "entry-2"
    assert memories[0]["content"]["text"] == "hello"
    assert memories[0]["mission_id"] == "mission-9"
    assert memories[0]["metadata"] == {"source": "unit-test"}
    assert memories[0]["importance_score"] == 0.8


def test_delete_memory_uses_agent_memory_entries_table():
    client = FakeSupabaseClient()
    client.rows = [{"id": "entry-3", "worker_id": "worker-1", "owner_id": "owner-1"}]
    engine = AtlasMemoryEngine(client=client, owner_id="owner-1")

    result = engine.delete_memory("entry-3")

    assert result["success"] is True
    assert client.table_name == "agent_memory_entries"
    assert client.rows == []


def test_memory_reads_are_owner_scoped():
    client = FakeSupabaseClient()
    client.rows = [
        {"id": "a", "worker_id": "worker-1", "owner_id": "owner-a", "memory_type": "note", "content": {"text": "shared keyword"}, "metadata": {}},
        {"id": "b", "worker_id": "worker-1", "owner_id": "owner-b", "memory_type": "note", "content": {"text": "shared keyword"}, "metadata": {}},
    ]
    engine = AtlasMemoryEngine(client=client, owner_id="owner-a")
    assert [row["id"] for row in engine.get_recent_memories("worker-1")] == ["a"]
    assert [row["id"] for row in engine.search_memories("shared keyword")] == ["a"]


def test_memory_reads_fail_closed_without_owner_scope():
    client = FakeSupabaseClient()
    client.rows = [{"id": "a", "worker_id": "worker-1", "owner_id": "owner-a", "memory_type": "note", "content": {"text": "secret"}, "metadata": {}}]
    engine = AtlasMemoryEngine(client=client)
    assert engine.get_recent_memories("worker-1") == []
    assert engine.search_memories("secret") == []
    assert engine.store_memory("worker-1", "note", {"text": "no owner"})["success"] is False
    assert engine.delete_memory("a")["success"] is False


def test_memory_delete_cannot_delete_another_owners_entry():
    client = FakeSupabaseClient()
    client.rows = [{"id": "a", "worker_id": "worker-1", "owner_id": "owner-a"}]
    engine = AtlasMemoryEngine(client=client, owner_id="owner-b")
    result = engine.delete_memory("a")
    assert result["success"] is True
    assert result["deleted"] is False
    assert client.rows[0]["owner_id"] == "owner-a"