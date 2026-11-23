from __future__ import annotations

from types import SimpleNamespace

from app.services.aea_marketplace_referral import validate_aea_marketplace_referral


class Rpc:
    def __init__(self, value=True, fail=False):
        self.value = value
        self.fail = fail
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        if self.fail:
            raise RuntimeError("db error")
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.value))


def test_valid_aea_referral_is_checked_without_redirect(monkeypatch):
    monkeypatch.setenv("AEA_CORE_API_URL", "https://aea-core.onrender.com")
    client = Rpc(True)
    assert validate_aea_marketplace_referral(
        client=client,
        destination_url="https://aea-core.onrender.com/store/affiliate/r/abcdefghijklmnopqrstuvwx123456",
        product_id="product-1",
        promoter_id="promoter-1",
    )
    assert client.calls == [(
        "validate_creator_affiliate_link",
        {
            "p_code": "abcdefghijklmnopqrstuvwx123456",
            "p_product_id": "product-1",
            "p_promoter_id": "promoter-1",
        },
    )]


def test_revoked_or_unapproved_referral_fails_closed(monkeypatch):
    monkeypatch.setenv("AEA_CORE_API_URL", "https://aea-core.onrender.com")
    assert not validate_aea_marketplace_referral(
        client=Rpc(False),
        destination_url="https://aea-core.onrender.com/store/affiliate/r/abcdefghijklmnopqrstuvwx123456",
        product_id="product-1",
        promoter_id="promoter-1",
    )


def test_external_or_wrong_product_referral_fails_closed(monkeypatch):
    monkeypatch.setenv("AEA_CORE_API_URL", "https://aea-core.onrender.com")
    client = Rpc(True)
    assert not validate_aea_marketplace_referral(
        client=client,
        destination_url="https://external.example/store/affiliate/r/abcdefghijklmnopqrstuvwx123456",
        product_id="product-1",
        promoter_id="promoter-1",
    )
    assert not client.calls


def test_database_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("AEA_CORE_API_URL", "https://aea-core.onrender.com")
    assert not validate_aea_marketplace_referral(
        client=Rpc(fail=True),
        destination_url="https://aea-core.onrender.com/store/affiliate/r/abcdefghijklmnopqrstuvwx123456",
        product_id="product-1",
        promoter_id="promoter-1",
    )


def test_missing_product_or_promoter_fails_closed(monkeypatch):
    monkeypatch.setenv("AEA_CORE_API_URL", "https://aea-core.onrender.com")
    assert not validate_aea_marketplace_referral(
        client=Rpc(True),
        destination_url="https://aea-core.onrender.com/store/affiliate/r/abcdefghijklmnopqrstuvwx123456",
        product_id="",
        promoter_id="promoter-1",
    )
