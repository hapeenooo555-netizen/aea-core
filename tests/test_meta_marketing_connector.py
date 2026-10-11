from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.connectors.meta_marketing import MetaMarketingConnector


def test_meta_connector_has_three_channel_capabilities() -> None:
    caps = MetaMarketingConnector().capabilities.to_dict()
    assert caps["platform"] == "meta"
    assert caps["publish_content"] is True


def test_meta_connector_does_not_claim_connection_from_environment_token(monkeypatch) -> None:
    monkeypatch.setenv("META_ACCESS_TOKEN", "secret")
    monkeypatch.setenv("META_PAGE_ID", "page-123")
    result = MetaMarketingConnector().get_account_status()
    assert "secret" not in str(result)
    assert result["status"] == "awaiting_authorization"
    assert result["details"]["oauth_verified"] is False
    assert result["details"]["token_configured"] is True


def test_meta_connect_account_rejects_unverified_asset_ids() -> None:
    result = MetaMarketingConnector().connect_account(
        "worker-a",
        {"page_id": "user-supplied-page", "instagram_user_id": "user-supplied-ig"},
    )
    assert result["success"] is False
    assert result["status"] == "awaiting_human"
    assert result["checkpoint_type"] == "oauth_authorization_required"


def test_meta_onboarding_never_claims_success_without_oauth() -> None:
    connector = MetaMarketingConnector()
    started = connector.start_onboarding("worker-a")
    assert started["status"] == "awaiting_human"
    resumed = connector.resume_onboarding(started["workflow_id"], {"page_id": "fake-page"})
    assert resumed["status"] == "awaiting_human"
    assert resumed["requires_human_intervention"] is True


def test_meta_disconnect_does_not_claim_revocation_without_token_store() -> None:
    result = MetaMarketingConnector().disconnect_account("worker-a")
    assert result["success"] is False
    assert result["status"] == "awaiting_human"


def test_meta_publish_requires_configuration(monkeypatch) -> None:
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    result = MetaMarketingConnector().publish_content(
        "worker-a",
        {"channel": "whatsapp", "message": "Habari"},
    )
    assert result["success"] is False
    assert result["status"] == "not_configured"
    assert result["checkpoint_type"] == "oauth_authorization_required"


def test_meta_publish_uses_transport_without_leaking_token(monkeypatch) -> None:
    monkeypatch.setenv("META_ACCESS_TOKEN", "secret")
    captured = {}

    def transport(**kwargs):
        captured.update(kwargs)
        return {"success": True, "id": "remote-1"}

    result = MetaMarketingConnector(transport=transport).publish_content(
        "worker-a",
        {"channel": "facebook", "message": "New mabati stock"},
        idempotency_key="post-1",
    )
    assert result == {"success": True, "id": "remote-1"}
    assert captured["channel"] == "facebook"
    assert "access_token" not in captured
