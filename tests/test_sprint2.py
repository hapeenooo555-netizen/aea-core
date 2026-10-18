import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.main as main_module
from app.routers import health, missions, workers, connectors, approvals, atlas
from app.dependencies import get_current_user_id, get_user_scoped_client, get_current_user


def _override_get_current_user_id():
    return "test-user"


def _override_get_current_user():
    return {"id": "test-user", "email": "test@example.com", "role": "authenticated"}


def _override_get_user_scoped_client(request: Request, current_user: dict[str, Any] = None):
    return main_module.supabase_client


# Create a test app instead of modifying global state
test_app = FastAPI()

# Root endpoint (copied from main.py)
@test_app.get("/")
def root() -> dict[str, str]:
    return {"message": "AEA Core API is running"}

test_app.include_router(health.router)
test_app.include_router(missions.router)
test_app.include_router(workers.router)
test_app.include_router(connectors.router)
test_app.include_router(approvals.router)
test_app.include_router(atlas.router)

test_app.dependency_overrides[get_current_user_id] = _override_get_current_user_id
test_app.dependency_overrides[get_current_user] = _override_get_current_user
test_app.dependency_overrides[get_user_scoped_client] = _override_get_user_scoped_client

client = TestClient(test_app)


def test_root_endpoint() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "AEA Core API is running"}


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "aea-core-backend"
    assert "supabase_configured" in payload
    assert "supabase_connected" in payload


def test_workers_endpoint_reports_unavailable_supabase_instead_of_mock_data() -> None:
    response = client.get("/workers", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 503
    assert "Workers were not loaded" in response.json()["detail"]


def test_create_worker_reports_supabase_errors(monkeypatch) -> None:
    class FailingTable:
        def insert(self, payload):
            raise RuntimeError("new row violates row-level security policy")

    class FailingClient:
        def table(self, _name):
            return FailingTable()

    monkeypatch.setattr(main_module, "supabase_client", FailingClient())

    response = client.post(
        "/workers",
        json={"name": "Test Bot", "role": "affiliate", "status": "active"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 500
    assert "row-level security policy" in response.json()["detail"]
