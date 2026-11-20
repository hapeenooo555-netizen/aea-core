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
            "creator_products": [{
                "id": "22222222-2222-4222-8222-222222222222",
                "owner_id": "owner-123",
                "asset_id": "11111111-1111-4111-8111-111111111111",
                "status": "draft",
            }],
            "creator_distribution_jobs": [],
        }

    def table(self, name):
        return Table(self, name)


def client(db=None, owner_id="owner-123"):
    app = FastAPI()
    app.include_router(creator_store.router)
    app.dependency_overrides[get_current_user_id] = lambda: owner_id
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
    response = client(db).patch(
        "/store/products/22222222-2222-4222-8222-222222222222/status",
        json={"status": "listed"},
    )
    assert response.status_code == 200
    assert response.json()["product"]["status"] == "listed"
    assert response.json()["checkout_enabled"] is False


def test_owner_cannot_change_another_users_product():
    db = Database()
    db.tables["creator_products"][0]["owner_id"] = "someone-else"
    response = client(db).patch(
        "/store/products/22222222-2222-4222-8222-222222222222/status",
        json={"status": "listed"},
    )
    assert response.status_code == 404


def test_distribution_request_is_saved_for_supported_destination_without_publishing():
    db = Database()
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/distribution",
        json={"destination": "pinterest"},
    )
    assert response.status_code == 202
    assert response.json()["job"]["status"] == "pending_approval"
    assert response.json()["published"] is False
    assert db.tables["creator_distribution_jobs"][0]["owner_id"] == "owner-123"


def test_unsupported_destination_is_persisted_as_blocked_not_published():
    db = Database()
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/distribution",
        json={"destination": "youtube"},
    )
    assert response.status_code == 202
    assert response.json()["job"]["status"] == "draft"
    assert "OAuth" in response.json()["job"]["error"]
    assert response.json()["published"] is False


def test_distribution_request_cannot_target_another_users_product():
    db = Database()
    db.tables["creator_products"][0]["owner_id"] = "someone-else"
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/distribution",
        json={"destination": "facebook"},
    )
    assert response.status_code == 404
    assert db.tables["creator_distribution_jobs"] == []


def test_duplicate_distribution_request_returns_existing_job():
    db = Database()
    api = client(db)
    url = "/store/products/22222222-2222-4222-8222-222222222222/distribution"
    first = api.post(url, json={"destination": "instagram"})
    second = api.post(url, json={"destination": "instagram"})
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["created"] is False
    assert len(db.tables["creator_distribution_jobs"]) == 1


def test_archived_product_cannot_be_distributed():
    db = Database()
    db.tables["creator_products"][0]["status"] = "archived"
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/distribution",
        json={"destination": "pinterest"},
    )
    assert response.status_code == 409



def test_marketplace_admin_queue_rejects_non_admin_users():
    db = Database()
    db.tables["aea_marketplace_admins"] = []
    db.tables["creator_marketplace_listings"] = []
    response = client(db, owner_id="ordinary-user").get(
        "/store/products/marketplace/admin/listings"
    )
    assert response.status_code == 403


def test_marketplace_admin_can_approve_pending_listing():
    db = Database()
    db.tables["aea_marketplace_admins"] = [{"user_id": "aea-admin", "active": True}]
    db.tables["creator_marketplace_listings"] = [{
        "id": "33333333-3333-4333-8333-333333333333",
        "product_id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "vendor-123",
        "status": "pending",
        "price_amount": 15000,
        "currency": "TZS",
        "created_at": "2026-11-21T00:00:00Z",
    }]
    db.tables["creator_products"][0].update({
        "title": "Vendor Starter Guide",
        "description": "A product submitted by an AEA vendor.",
        "product_type": "guide",
        "age_rating": "general",
        "is_sensitive": False,
        "marketplace_enabled": True,
    })
    db.tables["creator_profiles"] = [{
        "user_id": "vendor-123",
        "display_name": "Vendor One",
    }]

    api = client(db, owner_id="aea-admin")
    queue = api.get("/store/products/marketplace/admin/listings")
    assert queue.status_code == 200
    assert len(queue.json()["listings"]) == 1
    assert queue.json()["listings"][0]["vendor"]["display_name"] == "Vendor One"

    decision = api.patch(
        "/store/products/marketplace/admin/listings/33333333-3333-4333-8333-333333333333",
        json={"status": "approved"},
    )
    assert decision.status_code == 200
    assert decision.json()["listing"]["status"] == "approved"
    assert db.tables["creator_marketplace_listings"][0]["status"] == "approved"


def test_marketplace_admin_cannot_review_without_active_role():
    db = Database()
    db.tables["aea_marketplace_admins"] = [{"user_id": "aea-admin", "active": False}]
    db.tables["creator_marketplace_listings"] = [{
        "id": "33333333-3333-4333-8333-333333333333",
        "product_id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "vendor-123",
        "status": "pending",
    }]
    response = client(db, owner_id="aea-admin").patch(
        "/store/products/marketplace/admin/listings/33333333-3333-4333-8333-333333333333",
        json={"status": "approved"},
    )
    assert response.status_code == 403
    assert db.tables["creator_marketplace_listings"][0]["status"] == "pending"
