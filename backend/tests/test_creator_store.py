from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import creator_store


class Response:
    def __init__(self, data):
        self.data = data


class Table:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters = {}
        self.payload = None
        self.action = "select"

    def select(self, *_args):
        if self.payload is None:
            self.action = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def insert(self, payload):
        self.action, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.action, self.payload = "update", payload
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        matches = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.action == "insert":
            row = dict(self.payload)
            row["id"] = "22222222-2222-4222-8222-222222222222"
            rows.append(row)
            return Response([row])
        if self.action == "update":
            for row in matches:
                row.update(self.payload)
            return Response(matches)
        return Response(matches)


class Database:
    def __init__(self):
        self.tables = {
            "content_asset_versions": [{
                "asset_id": "11111111-1111-4111-8111-111111111111",
                "owner_id": "owner-123",
                "version": 1,
                "status": "draft",
            }],
            "creator_products": [],
        }

    def table(self, name):
        return Table(self, name)


def client(db=None):
    app = FastAPI()
    app.include_router(creator_store.router)
    app.dependency_overrides[get_current_user_id] = lambda: "owner-123"
    app.dependency_overrides[get_user_scoped_client] = lambda: db or Database()
    return TestClient(app)


def test_create_product_requires_an_owned_saved_asset():
    db = Database()
    response = client(db).post("/store/products", json={
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "title": "AEA AI Starter Guide",
        "description": "A practical starter guide to using AI for everyday small business tasks.",
        "product_type": "guide",
        "price_amount": 15000,
        "currency": "TZS",
    })
    assert response.status_code == 201
    assert response.json()["product"]["owner_id"] == "owner-123"
    assert response.json()["product"]["status"] == "draft"
    assert response.json()["checkout_enabled"] is False


def test_create_product_rejects_asset_owned_by_another_user():
    db = Database()
    db.tables["content_asset_versions"][0]["owner_id"] = "someone-else"
    response = client(db).post("/store/products", json={
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "title": "AEA AI Starter Guide",
        "description": "A practical starter guide to using AI for everyday small business tasks.",
        "product_type": "guide",
        "price_amount": 15000,
        "currency": "TZS",
    })
    assert response.status_code == 404


def test_owner_can_list_product_but_checkout_remains_disabled():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "status": "draft",
    })
    response = client(db).patch(
        "/store/products/22222222-2222-4222-8222-222222222222/status",
        json={"status": "listed"},
    )
    assert response.status_code == 200
    assert response.json()["product"]["status"] == "listed"
    assert response.json()["checkout_enabled"] is False


def test_owner_cannot_change_another_users_product():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "someone-else",
        "status": "draft",
    })
    response = client(db).patch(
        "/store/products/22222222-2222-4222-8222-222222222222/status",
        json={"status": "listed"},
    )
    assert response.status_code == 404


def test_owner_can_request_youtube_distribution_pending_approval():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "title": "Creator video",
        "product_type": "video",
        "status": "draft",
    })
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/distribution",
        json={"destination": "youtube"},
    )
    assert response.status_code == 201
    assert response.json()["job"]["destination"] == "youtube"
    assert response.json()["job"]["status"] == "pending_approval"
    assert response.json()["job"]["approval_required"] is True
    assert response.json()["published"] is False


def test_non_video_product_cannot_be_sent_to_youtube_or_tiktok():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "title": "Creator ebook",
        "product_type": "ebook",
        "status": "draft",
    })
    for destination in ("youtube", "tiktok"):
        response = client(db).post(
            "/store/products/22222222-2222-4222-8222-222222222222/distribution",
            json={"destination": destination},
        )
        assert response.status_code == 422


def test_user_cannot_request_distribution_for_another_users_product():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "someone-else",
        "title": "Private video",
        "product_type": "video",
        "status": "draft",
    })
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/distribution",
        json={"destination": "youtube"},
    )
    assert response.status_code == 404
