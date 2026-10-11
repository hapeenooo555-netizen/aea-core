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


def test_meta_onboarding_requires_authenticated_owner() -> None:
    connector = MetaMarketingConnector()
    started = connector.start_onboarding("worker-a")
    assert started["status"] == "failed"
    assert "owner_id is required" in started["error"]


def test_meta_onboarding_never_claims_success_without_oauth() -> None:
    connector = MetaMarketingConnector()
    started = connector.start_onboarding("worker-a", owner_id="owner-a")
    assert started["status"] == "awaiting_human"
    assert started["owner_id"] == "owner-a"
    resumed = connector.resume_onboarding(started["workflow_id"], {"page_id": "fake-page"})
    assert resumed["status"] == "awaiting_human"
    assert resumed["requires_human_intervention"] is True


def test_meta_disconnect_does_not_claim_revocation_without_token_store() -> None:
    result = MetaMarketingConnector().disconnect_account("worker-a")
    assert result["success"] is False
    assert result["status"] == "awaiting_human"


def test_meta_publish_waits_for_oauth_when_no_token_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    result = MetaMarketingConnector().publish_content(
        "worker-a",
        {"channel": "whatsapp", "message": "Habari"},
        owner_id="owner-a",
    )
    assert result["success"] is False
    assert result["status"] == "awaiting_authorization"
    assert result["checkpoint_type"] == "oauth_authorization_required"


def test_meta_publish_cannot_be_triggered_by_global_token_or_transport(monkeypatch) -> None:
    monkeypatch.setenv("META_ACCESS_TOKEN", "secret")
    captured = {}

    def transport(**kwargs):
        captured.update(kwargs)
        return {"success": True, "id": "remote-1"}

    result = MetaMarketingConnector(transport=transport).publish_content(
        "worker-a",
        {"channel": "facebook", "message": "New mabati stock"},
        owner_id="owner-a",
        idempotency_key="post-1",
    )
    assert result["success"] is False
    assert result["status"] == "awaiting_authorization"
    assert result["checkpoint_type"] == "oauth_authorization_required"
    assert captured == {}


def test_meta_publishing_requires_owner_scoped_credentials(monkeypatch) -> None:
    from app.services import meta_credentials

    monkeypatch.setattr(meta_credentials, "load_meta_credentials", lambda owner_id: None)
    result = MetaMarketingConnector().publish_content(
        "worker-a",
        {"channel": "facebook", "message": "New mabati stock"},
        owner_id="owner-a",
    )
    assert result["success"] is False
    assert result["status"] == "awaiting_authorization"


def test_meta_asset_selection_only_uses_verified_oauth_assets() -> None:
    from app.services.meta_credentials import resolve_meta_asset

    credentials = {
        "pages": [
            {"id": "page-a", "access_token": "test-credential-a"},
            {"id": "page-b", "access_token": "test-credential-b"},
        ]
    }
    assert resolve_meta_asset(credentials, "facebook") is None
    assert resolve_meta_asset(credentials, "facebook", "page-a")["page_id"] == "page-a"
    assert resolve_meta_asset(credentials, "facebook", "page-foreign") is None
    assert resolve_meta_asset(credentials, "whatsapp") is None


def test_instagram_asset_must_be_linked_to_verified_page() -> None:
    from app.services.meta_credentials import resolve_meta_asset

    credentials = {
        "pages": [
            {"id": "page-a", "access_token": "test-credential-a", "instagram_business_account": {"id": "ig-a"}}
        ]
    }
    assert resolve_meta_asset(credentials, "instagram")["asset_id"] == "ig-a"
    assert resolve_meta_asset(credentials, "instagram", "ig-foreign") is None


def test_legacy_global_token_publisher_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("META_ACCESS_TOKEN", "must-not-be-used")
    monkeypatch.setenv("META_PAGE_ID", "page-123")
    monkeypatch.setenv("META_WA_PHONE_NUMBER_ID", "phone-123")

    def forbidden_client(*args, **kwargs):
        raise AssertionError("legacy helper must not make an external request")

    import app.services.connectors.meta_marketing as module
    monkeypatch.setattr(module.httpx, "Client", forbidden_client)

    connector = MetaMarketingConnector()
    result = connector._publish_via_graph(
        "whatsapp",
        {"recipient": "255700000000", "message": "Must remain blocked"},
    )

    assert result["success"] is False
    assert result["status"] == "approval_pipeline_required"
    assert result["requires_human_intervention"] is True
