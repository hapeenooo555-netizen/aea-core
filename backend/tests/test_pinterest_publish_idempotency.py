"""Duplicate-publish protection for approved Pinterest Pins.

Pinterest is never called: httpx.Client is replaced with a fake that counts
create requests. The publish ledger uses PinPublishStore's in-memory mode,
which mirrors the database's UNIQUE(operation_key) + compare-and-set rules.
"""

from __future__ import annotations

import threading
import time
from uuid import uuid4

import httpx
import pytest

from app.services.connectors.pinterest_connector import PinterestConnector
from app.services.stores.pin_publish_store import PinPublishStore

OWNER = "00000000-0000-4000-8000-0000000000aa"
WORKER = "worker-real-1"
CONTENT = {
    "board_name": "B",
    "board_id": "board-b",
    "pin_text": "Lightweight hiking backpack",
    "link_url": "https://example.com/product",
    "image_url": "https://example.com/image.jpg",
    "title": "Hiking backpack",
}


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _install(monkeypatch, behaviour, delay=0.0):
    calls = {"post": 0}
    lock = threading.Lock()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            return _Resp(200, {"items": [{"id": "board-b", "name": "B"}]})

        def post(self, url, **k):
            with lock:
                calls["post"] += 1
                n = calls["post"]
            if delay:
                time.sleep(delay)
            return behaviour(n)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        "app.services.pinterest_token_vault.get_access_token",
        lambda *, owner_id: {"access_token": "t", "expired": False},
    )
    return calls


def _connector():
    return PinterestConnector(pin_store=PinPublishStore())


def _key():
    return f"p1-11:{uuid4()}"


def _publish(conn, key):
    return conn.publish_content(WORKER, dict(CONTENT), idempotency_key=key, owner_id=OWNER)


def test_concurrent_approvals_send_only_one_pin(monkeypatch):
    calls = _install(monkeypatch, lambda n: _Resp(201, {"id": f"pin-{n}"}), delay=0.2)
    conn, key = _connector(), _key()
    results = []
    threads = [threading.Thread(target=lambda: results.append(_publish(conn, key))) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["post"] == 1
    published = [r for r in results if r.get("status") == "published"]
    assert len(published) == 1 and published[0]["pin_id"] == "pin-1"
    assert all(r.get("status") in {"published", "publish_in_progress"} for r in results)


def test_repeated_approval_returns_existing_pin(monkeypatch):
    calls = _install(monkeypatch, lambda n: _Resp(201, {"id": "pin-42"}))
    conn, key = _connector(), _key()
    first = _publish(conn, key)
    second = _publish(conn, key)
    assert first["status"] == "published"
    assert second["status"] == "duplicate_record"
    assert second["pin_id"] == "pin-42"
    assert second["pin_url"] == "https://www.pinterest.com/pin/pin-42/"
    assert calls["post"] == 1


def test_timeout_after_send_is_uncertain_and_never_retried(monkeypatch):
    def boom(n):
        raise httpx.ReadTimeout("lost response")

    calls = _install(monkeypatch, boom)
    conn, key = _connector(), _key()
    first = _publish(conn, key)
    assert first["status"] == "publish_uncertain"
    assert first["success"] is False and "pin_id" not in first
    again = _publish(conn, key)
    assert again["status"] == "publish_uncertain"
    assert calls["post"] == 1


def test_pinterest_server_error_is_uncertain(monkeypatch):
    calls = _install(monkeypatch, lambda n: _Resp(503, {}))
    conn, key = _connector(), _key()
    assert _publish(conn, key)["status"] == "publish_uncertain"
    assert _publish(conn, key)["status"] == "publish_uncertain"
    assert calls["post"] == 1


def test_success_without_pin_id_is_not_reported_published(monkeypatch):
    _install(monkeypatch, lambda n: _Resp(201, {}))
    result = _publish(_connector(), _key())
    assert result["success"] is False
    assert result["status"] == "publish_uncertain"


def test_definite_rejection_allows_one_retry(monkeypatch):
    calls = _install(monkeypatch, lambda n: _Resp(400, {}) if n == 1 else _Resp(201, {"id": "pin-retry"}))
    conn, key = _connector(), _key()
    assert _publish(conn, key)["status"] == "publish_failed"
    retry = _publish(conn, key)
    assert retry["status"] == "published" and retry["pin_id"] == "pin-retry"
    assert _publish(conn, key)["status"] == "duplicate_record"
    assert calls["post"] == 2


def test_record_failure_after_pinterest_success_blocks_republish(monkeypatch):
    calls = _install(monkeypatch, lambda n: _Resp(201, {"id": "pin-x"}))
    store = PinPublishStore()
    conn = PinterestConnector(pin_store=store)
    key = _key()
    monkeypatch.setattr(store, "transition", lambda *a, **k: None)
    first = _publish(conn, key)
    assert first["status"] == "publish_unrecorded" and first["pin_id"] == "pin-x"
    monkeypatch.undo()
    _install(monkeypatch, lambda n: _Resp(201, {"id": "pin-y"}))
    second = PinterestConnector(pin_store=store).publish_content(
        WORKER, dict(CONTENT), idempotency_key=key, owner_id=OWNER
    )
    assert second["status"] == "publish_in_progress"
    assert calls["post"] == 1


def test_publish_without_approval_key_is_refused(monkeypatch):
    calls = _install(monkeypatch, lambda n: _Resp(201, {"id": "pin"}))
    result = _connector().publish_content(WORKER, dict(CONTENT), idempotency_key="hub-chosen", owner_id=OWNER)
    assert result["success"] is False
    assert calls["post"] == 0


class _DbStub:
    """Minimal postgrest-like stub proving the store uses upsert(ignore_duplicates)."""

    def __init__(self):
        self.rows = {}

    def table(self, name):
        return _Query(self)


class _Query:
    def __init__(self, db):
        self.db, self.op, self.filters = db, None, []

    def upsert(self, payload, *, on_conflict="", ignore_duplicates=False):
        assert on_conflict == "operation_key" and ignore_duplicates is True
        self.op = ("upsert", payload)
        return self

    def select(self, *_a):
        self.op = ("select", None)
        return self

    def update(self, values):
        self.op = ("update", values)
        return self

    def eq(self, k, v):
        self.filters.append((k, [v]))
        return self

    def in_(self, k, v):
        self.filters.append((k, list(v)))
        return self

    def limit(self, _n):
        return self

    def execute(self):
        kind, payload = self.op
        match = lambda r: all(r.get(k) in vs for k, vs in self.filters)  # noqa: E731
        if kind == "upsert":
            if payload["operation_key"] in self.db.rows:
                return type("R", (), {"data": []})
            self.db.rows[payload["operation_key"]] = dict(payload)
            return type("R", (), {"data": [dict(payload)]})
        rows = [r for r in self.db.rows.values() if match(r)]
        if kind == "update":
            for r in rows:
                r.update(payload)
        return type("R", (), {"data": [dict(r) for r in rows]})


def test_database_claim_is_unique_and_compare_and_set():
    store = PinPublishStore(client=_DbStub(), durable_required=True)
    kw = dict(owner_id=OWNER, worker_id=WORKER, platform="pinterest", operation_key="p1-11:a",
              board_name="B", pin_text="t", link_url="https://e.com")
    first, second = store.claim(**kw), store.claim(**kw)
    assert first["created"] is True and second["created"] is False
    assert second["pin"]["status"] == "publishing"
    done = store.transition(OWNER, "p1-11:a", from_statuses=("publishing",), updates={"status": "published", "pin_id": "p"})
    assert done["pin_id"] == "p"
    assert store.transition(OWNER, "p1-11:a", from_statuses=("publishing",), updates={"status": "failed"}) is None
