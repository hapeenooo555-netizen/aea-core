from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.connectors.meta_marketing import MetaMarketingConnector, MetaConfig
from app.services.meta_oauth import graph_version


def test_meta_graph_api_defaults_to_v27(monkeypatch) -> None:
    monkeypatch.delenv("META_GRAPH_API_VERSION", raising=False)
    assert graph_version() == "v27.0"
    assert MetaConfig().api_version == "v27.0"
    assert MetaMarketingConnector().config.api_version == "v27.0"


def test_meta_graph_api_version_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("META_GRAPH_API_VERSION", "v26.0")
    assert graph_version() == "v26.0"
    assert MetaMarketingConnector().config.api_version == "v26.0"


def test_meta_connector_has_three_channel_capabilities() -> None:
    caps = MetaMarketingConnector().capabilities.to_dict()
    assert caps["platform"] == "meta"
    assert caps["publish_content"] is True
    assert caps["connect_account"] is False
    assert caps["disconnect_account"] is False


def test_meta_health_check_never_claims_a_shared_token_is_connected(monkeypatch) -> None:
    monkeypatch.setenv("META_ACCESS_TOKEN", "legacy-shared-secret")
    result = MetaMarketingConnector().health_check()
    assert result["status"] == "owner_scoped_oauth_required"
    assert result["connection_type"] == "per_user_encrypted_vault"
    assert "legacy-shared-secret" not in str(result)


def test_meta_publish_requires_authenticated_owner(monkeypatch) -> None:
    monkeypatch.setenv("META_ACCESS_TOKEN", "legacy-shared-secret")
    result = MetaMarketingConnector().publish_content(
        "worker-a",
        {"channel": "facebook", "message": "Habari"},
    )
    assert result["success"] is False
    assert result["status"] == "owner_required"


def test_meta_publish_never_falls_back_to_shared_environment_token(monkeypatch) -> None:
    monkeypatch.setenv("META_ACCESS_TOKEN", "legacy-shared-secret")
    result = MetaMarketingConnector().publish_content(
        "worker-a",
        {"channel": "whatsapp", "message": "Habari"},
        owner_id="owner-1",
    )
    assert result["success"] is False
    assert result["status"] == "not_connected"
    assert "legacy-shared-secret" not in str(result)


def test_meta_publish_transport_receives_owner_and_channel_not_raw_credentials() -> None:
    captured = {}

    def transport(**kwargs):
        captured.update(kwargs)
        return {"success": True, "id": "remote-1"}

    result = MetaMarketingConnector(transport=transport).publish_content(
        "worker-a",
        {"channel": "facebook", "message": "New mabati stock"},
        idempotency_key="post-1",
        owner_id="owner-1",
    )
    assert result == {"success": True, "id": "remote-1"}
    assert captured["channel"] == "facebook"
    assert captured["owner_id"] == "owner-1"
    assert captured["credential_purpose"] == "facebook_login"
    assert "access_token" not in captured
    assert "token" not in captured


def test_meta_whatsapp_transport_uses_embedded_signup_credential_scope() -> None:
    captured = {}

    def transport(**kwargs):
        captured.update(kwargs)
        return {"success": True, "id": "remote-wa-1"}

    result = MetaMarketingConnector(transport=transport).publish_content(
        "worker-a",
        {"channel": "whatsapp", "message": "Habari", "recipient": "255700000000"},
        owner_id="owner-1",
    )
    assert result["success"] is True
    assert captured["credential_purpose"] == "whatsapp_embedded_signup"



def test_facebook_image_publish_uses_page_photos_endpoint(monkeypatch) -> None:
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "page-photo-123"}

    class Client:
        def post(self, url, *, headers, data):
            calls.append({"url": url, "headers": headers, "data": data})
            return Response()

    connector = MetaMarketingConnector(config=MetaConfig())
    monkeypatch.setattr(connector, "_page_access_token", lambda client, token, page_id: "page-token")
    result = connector._publish_page_channel(
        Client(),
        "user-token",
        {"facebook_pages": [{"id": "page-1"}]},
        "facebook",
        {"page_id": "page-1", "message": "Habari", "image_url": "https://cdn.example/image.jpg"},
    )

    assert result["success"] is True
    assert result["status"] == "published"
    assert calls[0]["url"].endswith("/page-1/photos")
    assert calls[0]["data"] == {"url": "https://cdn.example/image.jpg", "caption": "Habari"}


def test_facebook_image_publish_rejects_non_https_media(monkeypatch) -> None:
    connector = MetaMarketingConnector(config=MetaConfig())

    class Client:
        def post(self, *args, **kwargs):
            raise AssertionError("No provider request should be made for an unsafe media URL.")

    monkeypatch.setattr(connector, "_page_access_token", lambda client, token, page_id: "page-token")
    try:
        connector._publish_page_channel(
            Client(),
            "user-token",
            {"facebook_pages": [{"id": "page-1"}]},
            "facebook",
            {"page_id": "page-1", "message": "Habari", "image_url": "http://unsafe.example/image.jpg"},
        )
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("Expected unsafe image URL to be rejected.")
