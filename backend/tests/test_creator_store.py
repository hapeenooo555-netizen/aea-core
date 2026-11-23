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
    app.include_router(creator_store.orders_router)
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


def test_owner_cannot_list_unfinished_product():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "status": "draft",
        "completion_status": "draft",
    })
    response = client(db).patch(
        "/store/products/22222222-2222-4222-8222-222222222222/status",
        json={"status": "listed"},
    )
    assert response.status_code == 409
    assert db.tables["creator_products"][0]["status"] == "draft"


def test_owner_can_finish_approved_product_with_substantive_chapters():
    db = Database()
    db.tables["content_asset_versions"][0].update({
        "status": "approved",
        "content": {"chapters": [{"title": "Chapter 1", "body": "A" * 250}]},
    })
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "product_type": "ebook",
        "status": "draft",
        "completion_status": "draft",
    })
    response = client(db).post("/store/products/22222222-2222-4222-8222-222222222222/finish")
    assert response.status_code == 200
    assert response.json()["completion_status"] == "finished"
    assert response.json()["checkout_enabled"] is False
    assert db.tables["creator_products"][0]["completion_status"] == "finished"


def test_owner_cannot_finish_unapproved_or_empty_product():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "product_type": "ebook",
        "status": "draft",
        "completion_status": "draft",
    })
    response = client(db).post("/store/products/22222222-2222-4222-8222-222222222222/finish")
    assert response.status_code == 409


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

def test_owner_can_read_approved_ebook_in_private_reader():
    db = Database()
    db.tables["content_asset_versions"][0].update({
        "status": "approved",
        "content": {"chapters": [{"title": "Utangulizi", "body": "Maudhui ya kitabu."}]},
    })
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "title": "Mwongozo wa AEA",
        "product_type": "ebook",
        "completion_status": "finished",
    })
    response = client(db).get("/store/products/22222222-2222-4222-8222-222222222222/reader")
    assert response.status_code == 200
    assert response.json()["access"] == "owner_preview_only"
    assert response.json()["chapters"][0]["body"] == "Maudhui ya kitabu."


def test_reader_rejects_unapproved_asset():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "product_type": "ebook",
    })
    response = client(db).get("/store/products/22222222-2222-4222-8222-222222222222/reader")
    assert response.status_code == 409


def test_reader_does_not_expose_another_owners_product():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "different-user",
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "product_type": "ebook",
    })
    response = client(db).get("/store/products/22222222-2222-4222-8222-222222222222/reader")
    assert response.status_code == 404


class FakeBucket:
    def __init__(self, objects=None):
        self.objects = objects or []

    def create_signed_upload_url(self, path):
        return {"signed_url": "https://storage.example/upload/" + path, "token": "upload-token"}

    def list(self, _folder, _options=None):
        return self.objects

    def create_signed_url(self, path, _expires, _options=None):
        return {"signed_url": "https://storage.example/download/" + path}


class FakeStorage:
    def __init__(self, objects=None):
        self.bucket = FakeBucket(objects)

    def from_(self, _bucket):
        return self.bucket


def test_private_product_upload_url_is_owner_scoped_and_pending():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "status": "draft",
    })
    db.storage = FakeStorage()
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/files/upload-url",
        json={"filename": "guide.pdf", "mime_type": "application/pdf", "size_bytes": 1200},
    )
    assert response.status_code == 201
    assert response.json()["file"]["mime_type"] == "application/pdf"
    assert response.json()["upload"]["token"] == "upload-token"
    row = db.tables["creator_product_files"][0]
    assert row["owner_id"] == "owner-123"
    assert row["status"] == "pending"
    assert row["object_path"].startswith("owner-123/22222222-2222-4222-8222-222222222222/")


def test_private_product_upload_rejects_mime_extension_mismatch():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "status": "draft",
    })
    db.storage = FakeStorage()
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/files/upload-url",
        json={"filename": "guide.exe", "mime_type": "application/pdf", "size_bytes": 1200},
    )
    assert response.status_code == 422


def test_product_file_completion_requires_verified_storage_and_privileged_write(monkeypatch):
    db = Database()
    db.tables["creator_product_files"] = [{
        "id": "33333333-3333-4333-8333-333333333333",
        "product_id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "bucket_id": "aea-product-files",
        "object_path": "owner-123/22222222-2222-4222-8222-222222222222/file.pdf",
        "original_filename": "file.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 1200,
        "status": "pending",
    }]
    db.storage = FakeStorage([{"name": "file.pdf", "metadata": {"size": 1200}}])
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/files/complete",
        json={"file_id": "33333333-3333-4333-8333-333333333333"},
    )
    assert response.status_code == 200
    assert db.tables["creator_product_files"][0]["status"] == "ready"
    assert response.json()["buyer_delivery_enabled"] is False


def test_product_file_completion_rejects_wrong_storage_size(monkeypatch):
    db = Database()
    db.tables["creator_product_files"] = [{
        "id": "33333333-3333-4333-8333-333333333333",
        "product_id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "bucket_id": "aea-product-files",
        "object_path": "owner-123/22222222-2222-4222-8222-222222222222/file.pdf",
        "original_filename": "file.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 1200,
        "status": "pending",
    }]
    db.storage = FakeStorage([{"name": "file.pdf", "metadata": {"size": 1000}}])
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/files/complete",
        json={"file_id": "33333333-3333-4333-8333-333333333333"},
    )
    assert response.status_code == 409
    assert db.tables["creator_product_files"][0]["status"] == "pending"


def test_purchase_intent_requires_listed_finished_product_and_ready_file(monkeypatch):
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "seller-456",
        "title": "Digital Guide",
        "product_type": "guide",
        "price_amount": 15000,
        "currency": "TZS",
        "status": "listed",
        "completion_status": "finished",
    })
    db.tables["creator_product_files"] = [{
        "id": "33333333-3333-4333-8333-333333333333",
        "product_id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "seller-456",
        "status": "ready",
    }]
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/purchase-intent",
        json={"idempotency_key": "buyer-guide-2026-001"},
    )
    assert response.status_code == 201
    assert response.json()["order"]["status"] == "pending"
    assert response.json()["payment_initiation_enabled"] is False
    assert response.json()["delivery_enabled"] is False
    assert db.tables["creator_order_items"][0]["product_file_id"] == "33333333-3333-4333-8333-333333333333"


def test_purchase_intent_rejects_seller_buying_own_product():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "owner-123",
        "title": "My Product",
        "product_type": "guide",
        "price_amount": 15000,
        "currency": "TZS",
        "status": "listed",
        "completion_status": "finished",
    })
    response = client(db).post(
        "/store/products/22222222-2222-4222-8222-222222222222/purchase-intent",
        json={"idempotency_key": "self-buy-2026-001"},
    )
    assert response.status_code == 409


def test_pending_order_cannot_download_paid_product(monkeypatch):
    db = Database()
    db.tables["creator_orders"] = [{
        "id": "44444444-4444-4444-8444-444444444444",
        "buyer_id": "owner-123",
        "status": "pending",
    }]
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    response = client(db).get(
        "/store/orders/44444444-4444-4444-8444-444444444444/items/55555555-5555-4555-8555-555555555555/download"
    )
    assert response.status_code == 403


def test_paid_order_without_entitlement_cannot_download(monkeypatch):
    db = Database()
    db.tables["creator_orders"] = [{
        "id": "44444444-4444-4444-8444-444444444444",
        "buyer_id": "owner-123",
        "status": "paid",
    }]
    db.tables["creator_product_entitlements"] = []
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    response = client(db).get(
        "/store/orders/44444444-4444-4444-8444-444444444444/items/55555555-5555-4555-8555-555555555555/download"
    )
    assert response.status_code == 403


def test_active_entitlement_on_paid_order_gets_short_lived_download(monkeypatch):
    db = Database()
    db.tables["creator_orders"] = [{
        "id": "44444444-4444-4444-8444-444444444444",
        "buyer_id": "owner-123",
        "status": "paid",
    }]
    db.tables["creator_product_entitlements"] = [{
        "id": "66666666-6666-4666-8666-666666666666",
        "order_id": "44444444-4444-4444-8444-444444444444",
        "order_item_id": "55555555-5555-4555-8555-555555555555",
        "buyer_id": "owner-123",
        "product_id": "22222222-2222-4222-8222-222222222222",
        "product_file_id": "33333333-3333-4333-8333-333333333333",
        "revoked_at": None,
    }]
    db.tables["creator_product_files"] = [{
        "id": "33333333-3333-4333-8333-333333333333",
        "product_id": "22222222-2222-4222-8222-222222222222",
        "object_path": "seller-456/22222222-2222-4222-8222-222222222222/guide.pdf",
        "original_filename": "guide.pdf",
        "status": "ready",
    }]
    db.storage = FakeStorage()
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    response = client(db).get(
        "/store/orders/44444444-4444-4444-8444-444444444444/items/55555555-5555-4555-8555-555555555555/download"
    )
    assert response.status_code == 200
    assert response.json()["expires_in_seconds"] == 300
    assert response.json()["download_url"].endswith("/seller-456/22222222-2222-4222-8222-222222222222/guide.pdf")
