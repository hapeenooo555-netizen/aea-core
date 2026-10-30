from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import workers as workers_router


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def select(self, *_: Any) -> "_Query":
        return self

    def eq(self, *_: Any) -> "_Query":
        return self

    def limit(self, *_: Any) -> "_Query":
        return self

    def execute(self) -> Any:
        return type("Response", (), {"data": self.rows})()


class _DB:
    def table(self, _name: str) -> _Query:
        return _Query([{"id": "worker-1", "owner_id": "user-1"}])


def _client(monkeypatch: Any, report: dict[str, Any]) -> TestClient:
    app = FastAPI()
    app.include_router(workers_router.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_user_scoped_client] = lambda: _DB()

    class _Orchestrator:
        def __init__(self, **_: Any) -> None:
            pass

        def run_worker(self, worker_id: str) -> dict[str, Any]:
            assert worker_id == "worker-1"
            return report

    monkeypatch.setattr(workers_router, "AgentOrchestrator", _Orchestrator)
    return TestClient(app)


def test_worker_run_returns_partial_batch_report(monkeypatch: Any) -> None:
    report = {
        "success": False,
        "status": "partial",
        "worker_id": "worker-1",
        "succeeded": 1,
        "failed": 1,
        "results": [{"mission_id": "m1", "success": True}, {"mission_id": "m2", "success": False}],
    }
    response = _client(monkeypatch, report).post("/workers/worker-1/run")
    assert response.status_code == 200
    assert response.json() == report


def test_worker_run_returns_failed_batch_report(monkeypatch: Any) -> None:
    report = {
        "success": False,
        "status": "failed",
        "worker_id": "worker-1",
        "succeeded": 0,
        "failed": 2,
        "results": [{"mission_id": "m1", "success": False}, {"mission_id": "m2", "success": False}],
    }
    response = _client(monkeypatch, report).post("/workers/worker-1/run")
    assert response.status_code == 200
    assert response.json() == report
