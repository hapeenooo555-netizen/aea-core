from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import creator_store_marketplace


USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
PRODUCT = "33333333-3333-4333-8333-333333333333"
LISTING = "44444444-4444-4444-8444-444444444444"


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = {}
        self.in_filters = {}
        self.or_filter = None
        self.operation = "select"
        self.payload = None

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, values):
        self.in_filters[key] = set(values)
        return self

    def or_(self, expression):
        self.or_filter = expression
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", deepcopy(payload)
        return self

    def update(self, payload):
        self.operation, self.payload = "update", deepcopy(payload)
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.operation == "insert":
            row = deepcopy(self.payload)
            row.setdefault("id", "66666666-6666-4666-8666-666666666666")
            rows.append(row)
            return Response([deepcopy(row)])

        matched = [
            row for row in rows
            if all(row.get(k) == v for k, v in self.filters.items())
            and all(row.get(k) in values for k, values in self.in_filters.items())
        ]
        if self.or_filter:
            # This fake implements the exact owner/promoter OR expression used by the route.
            clauses = self.or_filter.split(",")
            matched = [
                row for row in matched
                if any(row.get(part.split(".")[0]) == part.split(".")[-1] for part in clauses)
            ]
        if self.operation == "update":
            for row in matched:
                row.update(deepcopy(self.payload))
        return Response(deepcopy(matched))


class Database:
    def __init__(self, tables=None):
        self.tables = deepcopy(tables or {})

    def table(self, name):
        return Query(self, name)


def client_for(db=None, user_id=USER):
    app = FastAPI()
    app.include_router(creator_store_marketplace.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_user_scoped_client] = lambda: db if db is not None else Database()
    return TestClient(app)


def product_row(**overrides):
    return {
        "id": PRODUCT, "owner_id": USER, "status": "listed",
        "marketplace_enabled": True, "price_amount": 25000,
        "currency": "TZS", "title": "Test product",
        "description": "A product", "product_type": "digital",
        "age_rating": "general", "is_sensitive": False,
        **overrides,
    }


def listing_row(**overrides):
    return {
        "id": LISTING, "product_id": PRODUCT, "owner_id": OTHER,
        "status": "approved", "price_amount": 25000, "currency": "TZS",
        **overrides,
    }


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
        f"/store/products/{PRODUCT}/distribution",
        json={"destination": "digistore24"},
    )
    assert response.status_code == 422


def test_distribution_request_is_saved_as_draft_and_not_published():
    db = Database({"creator_products": [product_row()]})
    response = client_for(db).post(
        f"/store/products/{PRODUCT}/distribution",
        json={"destination": "pinterest"},
    )
    assert response.status_code == 201
    assert response.json()["published"] is False
    assert response.json()["job"]["status"] == "draft"
    assert response.json()["job"]["approval_required"] is True


def test_distribution_request_cannot_be_created_for_another_users_product():
    db = Database({"creator_products": [product_row(owner_id=OTHER)]})
    response = client_for(db).post(
        f"/store/products/{PRODUCT}/distribution",
        json={"destination": "facebook"},
    )
    assert response.status_code == 404
    assert db.tables.get("creator_distribution_jobs", []) == []


def test_marketplace_toggle_requires_listed_product_and_updates_owner_row():
    db = Database({"creator_products": [product_row(status="draft", marketplace_enabled=False)]})
    blocked = client_for(db).patch(f"/store/products/{PRODUCT}/marketplace", json={"enabled": True})
    assert blocked.status_code == 409

    db = Database({"creator_products": [product_row(marketplace_enabled=False)]})
    allowed = client_for(db).patch(f"/store/products/{PRODUCT}/marketplace", json={"enabled": True})
    assert allowed.status_code == 200
    assert allowed.json()["product"]["marketplace_enabled"] is True


def test_sensitive_visibility_forces_18_plus_rating():
    db = Database({
        "creator_profiles": [{"user_id": USER, "is_public": True}],
        "creator_products": [product_row()],
    })
    response = client_for(db).patch(
        f"/store/products/{PRODUCT}/visibility",
        json={"visibility": "community_public", "is_sensitive": True, "age_rating": "general"},
    )
    assert response.status_code == 200
    assert response.json()["product"]["is_sensitive"] is True
    assert response.json()["product"]["age_rating"] == "18_plus"


def test_community_visibility_requires_public_creator_profile():
    db = Database({
        "creator_profiles": [{"user_id": USER, "is_public": False}],
        "creator_products": [product_row()],
    })
    response = client_for(db).patch(
        f"/store/products/{PRODUCT}/visibility",
        json={"visibility": "community_public"},
    )
    assert response.status_code == 409


def test_vendor_can_submit_listing_only_after_marketplace_enabled():
    db = Database({"creator_products": [product_row(marketplace_enabled=False)]})
    response = client_for(db).post(f"/store/products/{PRODUCT}/marketplace/listing")
    assert response.status_code == 409

    db = Database({"creator_products": [product_row()]})
    response = client_for(db).post(f"/store/products/{PRODUCT}/marketplace/listing")
    assert response.status_code == 201
    assert response.json()["listing"]["status"] == "pending"
    assert response.json()["listing"]["owner_id"] == USER


def test_promoter_can_request_approved_listing_and_vendor_can_approve():
    db = Database({"creator_marketplace_listings": [listing_row()]})
    promoter = client_for(db).post(
        "/store/products/marketplace/requests",
        json={"listing_id": LISTING, "message": "Please approve me"},
    )
    assert promoter.status_code == 201
    request_id = promoter.json()["request"]["id"]
    assert promoter.json()["request"]["promoter_id"] == USER
    assert promoter.json()["request"]["owner_id"] == OTHER

    vendor = client_for(db, user_id=OTHER).patch(
        f"/store/products/marketplace/requests/{request_id}",
        json={"status": "approved"},
    )
    assert vendor.status_code == 200
    assert vendor.json()["request"]["status"] == "approved"


def test_promoter_cannot_request_own_product():
    db = Database({"creator_marketplace_listings": [listing_row(owner_id=USER)]})
    response = client_for(db).post(
        "/store/products/marketplace/requests",
        json={"listing_id": LISTING},
    )
    assert response.status_code == 400


def test_admin_can_approve_pending_listing_only_with_active_admin_row():
    db = Database({
        "aea_marketplace_admins": [{"user_id": USER, "active": True}],
        "creator_marketplace_listings": [listing_row(status="pending")],
    })
    response = client_for(db).patch(
        f"/store/products/marketplace/admin/listings/{LISTING}",
        json={"status": "approved"},
    )
    assert response.status_code == 200
    assert response.json()["listing"]["status"] == "approved"


def test_marketplace_browse_joins_approved_listing_product_and_public_vendor():
    db = Database({
        "creator_marketplace_listings": [listing_row()],
        "creator_products": [product_row(owner_id=OTHER)],
        "creator_profiles": [{"user_id": OTHER, "display_name": "Vendor", "is_public": True}],
    })
    response = client_for(db).get("/store/products/marketplace")
    assert response.status_code == 200
    items = response.json()["listings"]
    assert len(items) == 1
    assert items[0]["product"]["title"] == "Test product"
    assert items[0]["product"]["slug"] == "test-product"
    assert items[0]["vendor"]["display_name"] == "Vendor"
