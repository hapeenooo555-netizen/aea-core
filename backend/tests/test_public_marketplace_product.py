from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.routers import creator_affiliate


class Rpc:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.rows))


def client():
    app = FastAPI()
    app.include_router(creator_affiliate.products_router)
    return TestClient(app)


def test_public_product_detail_returns_only_rpc_payload(monkeypatch):
    rpc = Rpc([{
        "slug": "sample-product",
        "title": "Sample",
        "description": "Public description",
        "product_type": "ebook",
        "price_amount": 12000,
        "currency": "TZS",
        "age_rating": "general",
        "vendor_display_name": "Public Vendor",
    }])
    monkeypatch.setattr(database, "supabase_client", rpc)
    response = client().get("/store/products/public/sample-product")
    assert response.status_code == 200
    assert response.json()["product"]["title"] == "Sample"
    assert "owner_id" not in response.json()["product"]
    assert rpc.calls == [("get_public_creator_product_by_slug", {"p_slug": "sample-product"})]


def test_public_product_detail_hides_missing_or_unlisted_products(monkeypatch):
    monkeypatch.setattr(database, "supabase_client", Rpc([]))
    response = client().get("/store/products/public/hidden-product")
    assert response.status_code == 404


def test_public_product_detail_rejects_invalid_slug_before_database(monkeypatch):
    rpc = Rpc([])
    monkeypatch.setattr(database, "supabase_client", rpc)
    response = client().get("/store/products/public/../private")
    assert response.status_code in {404, 307, 308}
    assert rpc.calls == []
