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


class RpcQuery:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


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

    def rpc(self, name, params):
        if name != "review_creator_promotion_request":
            raise AssertionError(f"Unexpected RPC: {name}")
        rows = self.tables.setdefault("creator_promotion_requests", [])
        matches = [row for row in rows if row.get("id") == params["p_request_id"] and row.get("status") == "pending"]
        for row in matches:
            row["status"] = params["p_status"]
            row["commission_rate_bps"] = params["p_commission_rate_bps"]
        return RpcQuery(Response(deepcopy(matches)))


def client_for(db=None, user_id=USER):
    app = FastAPI()
    app.include_router(creator_store_marketplace.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_user_scoped_client] = lambda: db if db is not None else Database()
    return TestClient(app)


def product_row(**overrides):
    return {
        "id": PRODUCT, "owner_id": USER, "status": "listed", "completion_status": "finished",
        "marketplace_enabled": True, "price_amount": 25000,
        "currency": "TZS", "slug": "test-product", "title": "Test product",
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
    assert response.json() == {"success": True, "jobs": [], "published_pins": []}


def test_distribution_history_includes_only_owner_verified_pinterest_pins():
    db = Database({
        "creator_distribution_jobs": [{
            "id": "job-1", "product_id": PRODUCT, "owner_id": USER,
            "destination": "pinterest", "status": "draft", "approval_required": True,
        }],
        "published_pins": [
            {
                "id": "pin-row-1", "owner_id": USER, "platform": "pinterest",
                "status": "published", "pin_id": "123456789",
                "approval_request_id": "approval-1", "board_name": "Ideas",
                "created_at": "2026-11-22T10:00:00Z",
                "content": {"published_at": "2026-11-22T10:00:00Z"},
            },
            {
                "id": "pin-row-2", "owner_id": OTHER, "platform": "pinterest",
                "status": "published", "pin_id": "999999999",
                "created_at": "2026-11-22T10:00:00Z", "content": {},
            },
            {
                "id": "pin-row-3", "owner_id": USER, "platform": "pinterest",
                "status": "publishing", "pin_id": None,
                "created_at": "2026-11-22T10:00:00Z", "content": {},
            },
        ],
    })
    response = client_for(db).get("/store/products/mine/distribution")
    assert response.status_code == 200
    body = response.json()
    assert len(body["jobs"]) == 1
    assert len(body["published_pins"]) == 1
    pin = body["published_pins"][0]
    assert pin["pin_id"] == "123456789"
    assert pin["pin_url"] == "https://www.pinterest.com/pin/123456789/"
    assert pin["approval_request_id"] == "approval-1"
    assert pin["status"] == "published"


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


def test_marketplace_and_distribution_require_finished_products():
    db = Database({"creator_products": [product_row(completion_status="draft")]})
    distribution = client_for(db).post(
        f"/store/products/{PRODUCT}/distribution",
        json={"destination": "pinterest"},
    )
    assert distribution.status_code == 409

    marketplace = client_for(db).patch(
        f"/store/products/{PRODUCT}/marketplace",
        json={"enabled": True},
    )
    assert marketplace.status_code == 409

    listing = client_for(db).post(f"/store/products/{PRODUCT}/marketplace/listing")
    assert listing.status_code == 409


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
        json={"status": "approved", "commission_rate_bps": 1500},
    )
    assert vendor.status_code == 200
    assert vendor.json()["request"]["status"] == "approved"
    assert vendor.json()["request"]["commission_rate_bps"] == 1500


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


def test_repeated_distribution_draft_request_returns_existing_job():
    db = Database({"creator_products": [product_row()]})
    first = client_for(db).post(
        f"/store/products/{PRODUCT}/distribution",
        json={"destination": "pinterest"},
    )
    second = client_for(db).post(
        f"/store/products/{PRODUCT}/distribution",
        json={"destination": "pinterest"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["duplicate"] is True
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    assert len(db.tables["creator_distribution_jobs"]) == 1

def test_distribution_capabilities_fail_closed_until_publishers_are_verified():
    response = client_for().get("/store/products/distribution/capabilities")
    assert response.status_code == 200
    capabilities = {item["destination"]: item for item in response.json()["capabilities"]}
    assert set(capabilities) == {"pinterest", "facebook", "instagram", "tiktok", "youtube"}
    for destination in ("pinterest", "facebook", "instagram", "tiktok", "youtube"):
        assert capabilities[destination]["publishing_supported"] is False
        assert capabilities[destination]["mode"] == "draft_only"
        assert "draft" in capabilities[destination]["message"].lower()


def test_vendor_cannot_approve_promotion_without_commission_terms():
    db = Database({"creator_promotion_requests": [{
        "id": "55555555-5555-4555-8555-555555555555",
        "owner_id": OTHER, "promoter_id": USER, "status": "pending",
    }]})
    response = client_for(db, user_id=OTHER).patch(
        "/store/products/marketplace/requests/55555555-5555-4555-8555-555555555555",
        json={"status": "approved"},
    )
    assert response.status_code == 422
    assert db.tables["creator_promotion_requests"][0]["status"] == "pending"


def test_vendor_commission_rate_is_bounded():
    db = Database({"creator_promotion_requests": [{
        "id": "55555555-5555-4555-8555-555555555555",
        "owner_id": OTHER, "promoter_id": USER, "status": "pending",
    }]})
    response = client_for(db, user_id=OTHER).patch(
        "/store/products/marketplace/requests/55555555-5555-4555-8555-555555555555",
        json={"status": "approved", "commission_rate_bps": 10001},
    )
    assert response.status_code == 422


class PayoutAdminDatabase(Database):
    def __init__(self, tables=None):
        super().__init__(tables)
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name == "list_creator_payout_requests_admin":
            return RpcQuery(Response(self.tables.get("payout_rows", [])))
        if name == "review_creator_payout_request":
            return RpcQuery(Response([{
                "id": params["p_request_id"], "status": params["p_status"],
                "amount": 12500, "currency": "TZS",
            }]))
        if name == "mark_creator_payout_request_paid":
            return RpcQuery(Response([{
                "id": params["p_request_id"], "status": "paid",
                "amount": 12500, "currency": "TZS",
                "payout_reference": params["p_payout_reference"],
            }]))
        raise AssertionError(f"Unexpected RPC: {name}")


def test_marketplace_admin_payout_queue_requires_active_admin():
    response = client_for(PayoutAdminDatabase()).get("/store/products/marketplace/admin/payouts")
    assert response.status_code == 403


def test_active_marketplace_admin_can_list_payout_queue():
    row = {
        "id": "77777777-7777-4777-8777-777777777777",
        "promoter_id": OTHER, "amount": 12500, "currency": "TZS",
        "payment_method": "mobile_money", "status": "pending",
        "created_at": "2026-11-23T12:00:00Z",
    }
    db = PayoutAdminDatabase({
        "aea_marketplace_admins": [{"user_id": USER, "active": True}],
        "payout_rows": [row],
    })
    response = client_for(db).get("/store/products/marketplace/admin/payouts")
    assert response.status_code == 200
    assert response.json()["payout_requests"][0]["promoter_id"] == OTHER
    assert db.rpc_calls == [("list_creator_payout_requests_admin", {})]


def test_admin_can_approve_pending_payout_request():
    request_id = "77777777-7777-4777-8777-777777777777"
    db = PayoutAdminDatabase({"aea_marketplace_admins": [{"user_id": USER, "active": True}]})
    response = client_for(db).patch(
        f"/store/products/marketplace/admin/payouts/{request_id}/review",
        json={"status": "approved"},
    )
    assert response.status_code == 200
    assert response.json()["payout_request"]["status"] == "approved"
    assert db.rpc_calls[0][0] == "review_creator_payout_request"


def test_admin_must_supply_reason_when_rejecting_payout():
    request_id = "77777777-7777-4777-8777-777777777777"
    db = PayoutAdminDatabase({"aea_marketplace_admins": [{"user_id": USER, "active": True}]})
    response = client_for(db).patch(
        f"/store/products/marketplace/admin/payouts/{request_id}/review",
        json={"status": "rejected"},
    )
    assert response.status_code == 422
    assert db.rpc_calls == []


def test_admin_marks_payout_paid_only_with_transaction_reference():
    request_id = "77777777-7777-4777-8777-777777777777"
    db = PayoutAdminDatabase({"aea_marketplace_admins": [{"user_id": USER, "active": True}]})
    response = client_for(db).patch(
        f"/store/products/marketplace/admin/payouts/{request_id}/paid",
        json={"payout_reference": "MNO-123456"},
    )
    assert response.status_code == 200
    assert response.json()["payout_request"]["status"] == "paid"
    assert response.json()["payout_request"]["payout_reference"] == "MNO-123456"
    assert db.rpc_calls[0][0] == "mark_creator_payout_request_paid"
