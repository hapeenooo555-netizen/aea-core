from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.connectors.meta_marketing import MetaMarketingConnector


def test_meta_connector_has_three_channel_capabilities() -> None:
    caps = MetaMarketingConnector().capabilities.to_dict()
    assert caps["platform"] == "meta"
    assert caps["publish_content"] is True


def test_meta_connector_does_not_expose_token(monkeypatch) -> None:
    monkeypatch.setenv("META_ACCESS_TOKEN", "secret")
    result = MetaMarketingConnector().get_account_status()
    assert "secret" not in str(result)
    assert result["status"] == "connected"


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
