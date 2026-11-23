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


def test_promoter_earnings_returns_only_rpc_scoped_commission_fields():
    rpc = RpcClient([
        {
            "order_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "product_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "product_title": "AI Toolkit",
            "product_type": "guide",
            "unit_amount": 15000,
            "currency": "TZS",
            "commission_rate_bps": 1500,
            "commission_amount": 2250,
            "commission_status": "payable",
            "created_at": "2026-11-23T10:00:00Z",
        },
        {
            "order_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "product_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "product_title": "Business Workbook",
            "product_type": "workbook",
            "unit_amount": 10000,
            "currency": "TZS",
            "commission_rate_bps": 1000,
            "commission_amount": 1000,
            "commission_status": "pending",
            "created_at": "2026-11-22T10:00:00Z",
        },
        {
            "order_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            "product_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
            "product_title": "Paid course",
            "product_type": "course",
            "unit_amount": 20000,
            "currency": "TZS",
            "commission_rate_bps": 500,
            "commission_amount": 1000,
            "commission_status": "paid",
            "created_at": "2026-11-21T10:00:00Z",
        },
    ])
    response = make_client(rpc).get("/store/affiliate/earnings")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "referral_order_count": 3,
        "payable_amount": 2250,
        "paid_amount": 1000,
        "pending_amount": 1000,
        "currency": "TZS",
    }
    assert body["earnings"][0]["commission_rate_bps"] == 1500
    assert "buyer_id" not in body["earnings"][0]
    assert "seller_id" not in body["earnings"][0]
    assert rpc.calls == [("get_my_creator_affiliate_earnings", {})]


def test_promoter_earnings_fails_closed_when_database_rpc_is_unavailable():
    class BrokenRpc:
        def rpc(self, *_args):
            raise RuntimeError("sensitive database error")
    response = make_client(BrokenRpc()).get("/store/affiliate/earnings")
    assert response.status_code == 503
    assert "sensitive database error" not in response.text


def test_promoter_can_request_payout_using_only_payable_commissions():
    rpc = RpcClient([{
        "id": "77777777-7777-4777-8777-777777777777",
        "amount": 12500,
        "currency": "TZS",
        "payment_method": "mobile_money",
        "status": "pending",
        "created_at": "2026-11-23T12:00:00Z",
    }])
    response = make_client(rpc).post(
        "/store/affiliate/payout-requests",
        json={"payment_method": "mobile_money"},
    )
    assert response.status_code == 201
    assert response.json()["payout_request"]["amount"] == 12500
    assert rpc.calls == [("create_my_creator_payout_request", {"p_payment_method": "mobile_money"})]


def test_promoter_payout_request_rejects_unknown_payment_method():
    response = make_client(RpcClient([])).post(
        "/store/affiliate/payout-requests",
        json={"payment_method": "cash"},
    )
    assert response.status_code == 422


def test_promoter_payout_request_fails_closed_when_no_payable_commission_exists():
    response = make_client(RpcClient([])).post(
        "/store/affiliate/payout-requests",
        json={"payment_method": "bank_transfer"},
    )
    assert response.status_code == 409


def test_promoter_can_read_only_their_own_payout_history_rpc():
    rpc = RpcClient([{
        "id": "77777777-7777-4777-8777-777777777777",
        "amount": 12500,
        "currency": "TZS",
        "payment_method": "bank_transfer",
        "status": "paid",
        "created_at": "2026-11-23T12:00:00Z",
    }])
    response = make_client(rpc).get("/store/affiliate/payout-requests")
    assert response.status_code == 200
    assert response.json()["payout_requests"][0]["status"] == "paid"
    assert rpc.calls == [("get_my_creator_payout_requests", {})]


def test_promoter_payout_history_fails_closed_when_rpc_is_unavailable():
    class BrokenRpc:
        def rpc(self, *_args):
            raise RuntimeError("private payout database details")
    response = make_client(BrokenRpc()).get("/store/affiliate/payout-requests")
    assert response.status_code == 503
    assert "private payout database details" not in response.text
