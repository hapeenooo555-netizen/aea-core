from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import creator_store_marketplace


USER = "11111111-1111-4111-8111-111111111111"


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = {}
        self.operation = "select"
        self.payload = None

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        rows = self.db.tables.get(self.table, [])
        return Response([row for row in rows if all(row.get(k) == v for k, v in self.filters.items())])


class Database:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return Query(self, name)


def client_for(db=None, user_id=USER):
    app = FastAPI()
    app.include_router(creator_store_marketplace.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_user_scoped_client] = lambda: db if db is not None else Database()
    return TestClient(app)


def test_marketplace_catalogue_endpoint_exists_and_returns_empty_list():
    response = client_for().get("/store/products/marketplace")
    assert response.status_code == 200
    assert response.json() == {"success": True, "listings": []}


def test_marketplace_admin_queue_denies_non_admin():
    response = client_for().get("/store/products/marketplace/admin/listings")
    assert response.status_code == 403
    assert response.json()["detail"] == "Marketplace Admin permission required."


def test_distribution_job_list_endpoint_exists():
    response = client_for().get("/store/products/mine/distribution")
    assert response.status_code == 200
    assert response.json() == {"success": True, "jobs": []}


def test_invalid_distribution_destination_is_rejected():
    response = client_for().post(
        "/store/products/11111111-1111-4111-8111-111111111111/distribution",
        json={"destination": "digistore24"},
    )
    assert response.status_code == 422
