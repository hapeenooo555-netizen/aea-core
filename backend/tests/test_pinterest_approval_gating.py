"""Approval-gating regression tests for Pinterest publishing."""

import pytest

from app.services.approval_resume_service import ApprovalResumeService


class FakeApprovalGateway:
    def __init__(self, status):
        self.status = status

    def get_request(self, _approval_request_id):
        return {
            "id": "approval-123",
            "owner_id": "owner-123",
            "status": self.status,
            "action_type": "publish_content",
            "mission_id": "mission-123",
            "payload": {
                "platform": "pinterest",
                "worker_id": "worker-123",
                "content": {
                    "board_name": "My Board",
                    "pin_text": "Previewed pin",
                    "image_url": "https://example.com/image.jpg",
                    "link_url": "https://example.com/",
                },
            },
        }


@pytest.mark.parametrize(
    ("approval_status", "expected_status"),
    [
        ("pending", "approval_pending"),
        ("rejected", "approval_rejected"),
        ("expired", "approval_expired"),
    ],
)
def test_publishing_resume_is_blocked_without_approved_status(
    approval_status, expected_status
):
    service = ApprovalResumeService(
        approval_gateway=FakeApprovalGateway(approval_status)
    )

    result = service.resume(
        "approval-123",
        current_user_id="owner-123",
    )

    assert result["status"] == expected_status


def test_publishing_resume_rejects_wrong_owner_before_dispatch():
    service = ApprovalResumeService(
        approval_gateway=FakeApprovalGateway("approved")
    )

    result = service.resume(
        "approval-123",
        current_user_id="different-owner",
    )

    assert result["status"] == "approval_unauthorized"
