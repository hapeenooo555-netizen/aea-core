"""ChatPlus state polling regression tests for queued missions."""

import pytest

from app.services.chatplus import ChatPlusService


@pytest.mark.parametrize("mission_status", ["pending", "scheduled"])
@pytest.mark.parametrize(
    "execution_status",
    ["PENDING", "RUNNING", "RETRY", "RETRYING", "UNKNOWN", ""],
)
def test_queued_chatplus_mission_remains_working(mission_status, execution_status):
    state = ChatPlusService.state_for_mission(
        {"status": mission_status},
        {"status": execution_status},
    )

    assert state["state"] == "working"
    assert "queued" in state["detail"].lower()


def test_active_chatplus_mission_remains_working():
    state = ChatPlusService.state_for_mission(
        {"status": "active"},
        {"status": "RUNNING"},
    )

    assert state["state"] == "working"
