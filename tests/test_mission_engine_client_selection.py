from unittest.mock import MagicMock

from app.services.mission_engine import MissionEngine


def test_create_mission_uses_explicit_client_instead_of_engine_client():
    """An explicitly supplied session client must be used for mission persistence."""
    engine_client = MagicMock()
    selected_client = MagicMock()
    selected_client.table.return_value.insert.return_value.execute.return_value.data = []

    engine = MissionEngine(client=engine_client)
    result = engine.create_mission(
        title="Session-bound mission",
        description="Verify database client selection",
        worker_id="worker-123",
        owner_id="owner-456",
        client=selected_client,
    )

    assert result["success"] is True
    selected_client.table.assert_called_once_with("missions")
    selected_client.table.return_value.insert.assert_called_once()
    selected_client.table.return_value.insert.return_value.execute.assert_called_once()
    engine_client.table.assert_not_called()
