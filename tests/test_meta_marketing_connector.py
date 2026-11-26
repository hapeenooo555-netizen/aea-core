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
    assert result["post_id"] == "page-photo-123"
    assert result["provider_reference"] == "page-photo-123"
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

def test_instagram_image_publish_creates_and_publishes_media_container(monkeypatch) -> None:
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def post(self, url, *, headers, data):
            calls.append({"url": url, "headers": headers, "data": data})
            if url.endswith("/media"):
                return Response({"id": "container-123"})
            return Response({"id": "ig-media-456"})

    connector = MetaMarketingConnector(config=MetaConfig())
    monkeypatch.setattr(connector, "_page_access_token", lambda client, token, page_id: "page-token")
    result = connector._publish_page_channel(
        Client(),
        "user-token",
        {"instagram_accounts": [{"id": "ig-1", "page_id": "page-1"}]},
        "instagram",
        {"instagram_account_id": "ig-1", "message": "Habari", "image_url": "https://cdn.example/image.jpg"},
    )

    assert result["success"] is True
    assert result["post_id"] == "ig-media-456"
    assert calls[0]["url"].endswith("/ig-1/media")
    assert calls[0]["data"] == {"image_url": "https://cdn.example/image.jpg", "caption": "Habari"}
    assert calls[1]["url"].endswith("/ig-1/media_publish")
    assert calls[1]["data"] == {"creation_id": "container-123"}



def test_meta_connector_mints_fresh_signed_url_from_owner_scoped_asset(monkeypatch) -> None:
    from types import SimpleNamespace
    import app.services.connectors.meta_marketing as module

    calls = []

    class Query:
        def select(self, fields):
            calls.append(("select", fields))
            return self

        def eq(self, field, value):
            calls.append(("eq", field, value))
            return self

        def limit(self, count):
            calls.append(("limit", count))
            return self

        def execute(self):
            return SimpleNamespace(data=[{
                "id": "asset-1",
                "status": "completed",
                "asset_path": "owner-1/asset-1/image.png",
            }])

    class StorageBucket:
        def create_signed_url(self, path, expires):
            calls.append(("sign", path, expires))
            return {"signedURL": "https://storage.example/fresh-image"}

    class Storage:
        def from_(self, bucket):
            calls.append(("bucket", bucket))
            return StorageBucket()

    class Client:
        storage = Storage()

        def table(self, name):
            calls.append(("table", name))
            return Query()

    monkeypatch.setattr(module, "get_supabase_image_budget_client", lambda: Client())
    url = MetaMarketingConnector._fresh_owner_image_url("owner-1", "asset-1")

    assert url == "https://storage.example/fresh-image"
    assert ("eq", "owner_id", "owner-1") in calls
    assert ("sign", "owner-1/asset-1/image.png", 900) in calls


def test_meta_connector_rejects_asset_owned_by_another_user(monkeypatch) -> None:
    from types import SimpleNamespace
    import app.services.connectors.meta_marketing as module

    class Query:
        def select(self, fields): return self
        def eq(self, field, value): return self
        def limit(self, count): return self
        def execute(self):
            return SimpleNamespace(data=[])

    class Client:
        def table(self, name): return Query()

    monkeypatch.setattr(module, "get_supabase_image_budget_client", lambda: Client())
    try:
        MetaMarketingConnector._fresh_owner_image_url("owner-1", "foreign-asset")
    except RuntimeError as exc:
        assert "not found for this account" in str(exc)
    else:
        raise AssertionError("Expected cross-owner asset lookup to fail closed.")
