from __future__ import annotations

from app.services.approval_resume_service import ApprovalResumeService


class Connector:
    platform = "pinterest"

    def __init__(self):
        self.called = False

    def publish_content(self, *_args, **_kwargs):
        self.called = True
        return {"success": True, "status": "published", "pin_id": "pin-123"}


def test_marketplace_publish_is_blocked_when_referral_validation_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.approval_resume_service.validate_aea_marketplace_referral",
        lambda **_kwargs: False,
    )
    connector = Connector()
    service = ApprovalResumeService(mission_client=object())
    result = service._handle_publish_content(
        approval_request_id="approval-1",
        mission_id="mission-1",
        worker_id="worker-1",
        payload={
            "source": "aea_marketplace_pinterest_promotion",
            "marketplace_product_id": "product-1",
            "content": {"link_url": "https://aea-core.onrender.com/store/affiliate/r/abcdefghijklmnopqrstuvwx123456"},
        },
        connector=connector,
        current_user_id="promoter-1",
    )
    assert result["status"] == "resume_failed"
    assert connector.called is False


def test_non_marketplace_pinterest_publish_keeps_existing_path(monkeypatch):
    monkeypatch.setattr(
        "app.services.approval_resume_service.validate_aea_marketplace_referral",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("not a Marketplace Pin")),
    )
    connector = Connector()
    service = ApprovalResumeService(mission_client=object())
    result = service._handle_publish_content(
        approval_request_id="approval-2",
        mission_id=None,
        worker_id="worker-1",
        payload={
            "source": "affiliate_pinterest_draft",
            "content": {"link_url": "https://example.com/product"},
        },
        connector=connector,
        current_user_id="owner-1",
    )
    assert connector.called is True
    assert result["status"] == "completed"
