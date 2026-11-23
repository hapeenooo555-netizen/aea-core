from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import creator_affiliate

USER = "11111111-1111-4111-8111-111111111111"
REQUEST = "22222222-2222-4222-8222-222222222222"
CODE = "abcdefghijklmnopqrstuvwx123456"


class RpcClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.response))


def make_client(rpc_client=None):
    app = FastAPI()
    app.include_router(creator_affiliate.router)
    app.dependency_overrides[get_current_user_id] = lambda: USER
    app.dependency_overrides[get_user_scoped_client] = lambda: rpc_client
    return TestClient(app)


def test_approved_promoter_can_mint_aea_affiliate_link(monkeypatch):
    rpc = RpcClient([{
        "id": "33333333-3333-4333-8333-333333333333",
        "product_id": "44444444-4444-4444-8444-444444444444",
        "status": "active",
        "referral_code": CODE,
        "created_at": "2026-11-22T10:00:00Z",
    }])
    response = make_client(rpc).post(f"/store/affiliate/requests/{REQUEST}/link")
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["affiliate_link"]["url"].endswith(f"/store/affiliate/r/{CODE}")
    assert rpc.calls[0][0] == "create_creator_affiliate_link"
    assert rpc.calls[0][1]["p_request_id"] == REQUEST


def test_affiliate_link_creation_fails_closed_when_rpc_rejects_request():
    class BrokenRpc:
        def rpc(self, *_args):
            raise RuntimeError("database detail should not leak")
    response = make_client(BrokenRpc()).post(f"/store/affiliate/requests/{REQUEST}/link")
    assert response.status_code == 409
    assert "database detail" not in response.text


def test_public_referral_redirect_records_and_redirects_to_aea_product(monkeypatch):
    rpc = RpcClient([{"product_slug": "small-business-ai-toolkit"}])
    monkeypatch.setattr(database, "supabase_client", rpc)
    monkeypatch.setenv("AEA_HUB_PUBLIC_URL", "https://aea-affiliate-hub-web.onrender.com")
    response = make_client().get(f"/store/affiliate/r/{CODE}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://aea-affiliate-hub-web.onrender.com/products/"
        f"small-business-ai-toolkit?ref={CODE}"
    )
    assert rpc.calls[0][0] == "resolve_creator_affiliate_link"


def test_public_referral_redirect_rejects_unknown_code(monkeypatch):
    monkeypatch.setattr(database, "supabase_client", RpcClient([]))
    response = make_client().get(f"/store/affiliate/r/{CODE}")
    assert response.status_code == 404


def test_public_referral_redirect_requires_https_storefront(monkeypatch):
    monkeypatch.setattr(database, "supabase_client", RpcClient([{"product_slug": "valid-product"}]))
    monkeypatch.setenv("AEA_HUB_PUBLIC_URL", "http://unsafe.example")
    response = make_client().get(f"/store/affiliate/r/{CODE}")
    assert response.status_code == 503
