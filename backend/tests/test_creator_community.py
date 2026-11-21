from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import creator_community


OWNER = "11111111-1111-4111-8111-111111111111"


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = {}
        self.payload = None
        self.operation = "select"

    def select(self, *_args):
        # Supabase allows select() after insert/update/upsert to return rows.
        if self.operation == "select":
            self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, values):
        self.filters[key] = set(values)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def upsert(self, payload, **_kwargs):
        self.operation, self.payload = "upsert", payload
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.operation in {"insert", "upsert"}:
            row = dict(self.payload)
            if self.operation == "upsert":
                key = "user_id" if self.table in {"creator_profiles", "creator_age_verifications"} else "id"
                existing = next((r for r in rows if r.get(key) == row.get(key)), None)
                if existing:
                    existing.update(row)
                    return Response([existing])
            rows.append(row)
            return Response([row])
        matches = [r for r in rows if all(
            (r.get(k) in v if isinstance(v, set) else r.get(k) == v)
            for k, v in self.filters.items()
        )]
        if self.operation == "update":
            for row in matches:
                row.update(self.payload)
            return Response(matches)
        return Response(matches)


class Database:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return Query(self, name)


def make_client(db=None):
    app = FastAPI()
    app.include_router(creator_community.router)
    app.dependency_overrides[get_current_user_id] = lambda: OWNER
    app.dependency_overrides[get_user_scoped_client] = lambda: db or Database()
    return TestClient(app)


def test_community_feed_returns_empty_items_when_no_public_products():
    response = make_client().get("/community/feed")
    assert response.status_code == 200
    assert response.json() == {"success": True, "items": []}


def test_creator_profile_is_owner_scoped_and_can_be_saved():
    db = Database()
    client = make_client(db)
    saved = client.put("/community/me/profile", json={
        "display_name": "Creator One",
        "bio": "Public creator profile",
        "is_public": True,
    })
    assert saved.status_code == 200
    assert saved.json()["profile"]["display_name"] == "Creator One"
    assert db.tables["creator_profiles"][0]["user_id"] == OWNER

    loaded = client.get("/community/me/profile")
    assert loaded.status_code == 200
    assert loaded.json()["profile"]["is_public"] is True


def test_age_verification_request_only_sets_pending_not_verified():
    db = Database()
    client = make_client(db)
    response = client.post("/community/me/age-verification", json={
        "verification_method": "manual_review",
    })
    assert response.status_code == 200
    assert response.json()["verification"]["status"] == "pending"
    assert db.tables["creator_age_verifications"][0]["status"] == "pending"


def test_profile_rejects_unexpected_fields():
    response = make_client().put("/community/me/profile", json={
        "display_name": "Creator One",
        "is_public": True,
        "user_id": "22222222-2222-4222-8222-222222222222",
    })
    assert response.status_code == 422
