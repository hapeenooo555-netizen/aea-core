"""Tests for human intervention checkpoints."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.human_intervention import HumanInterventionCheckpoint, HumanInterventionManager


def test_human_intervention_checkpoint_creation():
    """Test creating a human intervention checkpoint."""
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-123",
        mission_id="mission-123",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        instructions="Authorize the app",
    )

    assert checkpoint.checkpoint_id == "cp-123"
    assert checkpoint.platform == "pinterest"
    assert checkpoint.status == "awaiting_human"
    assert not checkpoint.is_expired()


def test_human_intervention_checkpoint_to_dict():
    """Test converting checkpoint to dictionary."""
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-123",
        mission_id="mission-123",
        platform="pinterest",
        checkpoint_type="email_verification_required",
        instructions="Verify your email",
    )

    result = checkpoint.to_dict()

    assert result["id"] == "cp-123"
    assert result["mission_id"] == "mission-123"
    assert result["platform"] == "pinterest"
    assert result["checkpoint_type"] == "email_verification_required"
    assert result["status"] == "awaiting_human"


def test_human_intervention_manager_create_checkpoint():
    """Test creating a checkpoint through the manager."""
    manager = HumanInterventionManager()

    result = manager.create_checkpoint(
        mission_id="mission-123",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        instructions="Authorize the app",
    )

    assert result["success"]
    assert "checkpoint" in result
    checkpoint = result["checkpoint"]
    assert checkpoint["platform"] == "pinterest"
    assert checkpoint["status"] == "awaiting_human"


def test_human_intervention_manager_invalid_checkpoint_type():
    """Test creating checkpoint with invalid type."""
    manager = HumanInterventionManager()

    result = manager.create_checkpoint(
        mission_id="mission-123",
        platform="pinterest",
        checkpoint_type="invalid_type",
        instructions="Do something",
    )

    assert not result["success"]
    assert "Unsupported checkpoint type" in result["error"]


def test_human_intervention_manager_get_checkpoint():
    """Test retrieving a checkpoint."""
    manager = HumanInterventionManager()

    create_result = manager.create_checkpoint(
        mission_id="mission-123",
        platform="pinterest",
        checkpoint_type="email_verification_required",
        instructions="Verify email",
    )

    checkpoint_id = create_result["checkpoint"]["id"]
    retrieved = manager.get_checkpoint(checkpoint_id)

    assert retrieved is not None
    assert retrieved["id"] == checkpoint_id
    assert retrieved["platform"] == "pinterest"


def test_human_intervention_manager_get_nonexistent():
    """Test retrieving a nonexistent checkpoint."""
    manager = HumanInterventionManager()

    result = manager.get_checkpoint("nonexistent-id")

    assert result is None


def test_human_intervention_manager_list_pending():
    """Test listing pending checkpoints."""
    manager = HumanInterventionManager()

    # Create two checkpoints
    manager.create_checkpoint(
        mission_id="mission-1",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        instructions="Authorize",
    )

    manager.create_checkpoint(
        mission_id="mission-2",
        platform="pinterest",
        checkpoint_type="email_verification_required",
        instructions="Verify email",
    )

    pending = manager.list_pending_checkpoints()

    assert len(pending) >= 2
    for cp in pending:
        assert cp["status"] == "awaiting_human"


def test_human_intervention_manager_list_pending_by_mission():
    """Test filtering pending checkpoints by mission."""
    manager = HumanInterventionManager()

    create_result = manager.create_checkpoint(
        mission_id="mission-123",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        instructions="Authorize",
    )

    manager.create_checkpoint(
        mission_id="mission-456",
        platform="pinterest",
        checkpoint_type="email_verification_required",
        instructions="Verify",
    )

    pending = manager.list_pending_checkpoints(mission_id="mission-123")

    assert len(pending) >= 1
    assert all(cp["mission_id"] == "mission-123" for cp in pending)


def test_human_intervention_manager_complete_checkpoint():
    """Test completing a checkpoint."""
    manager = HumanInterventionManager()

    create_result = manager.create_checkpoint(
        mission_id="mission-123",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        instructions="Authorize",
    )

    checkpoint_id = create_result["checkpoint"]["id"]

    complete_result = manager.complete_checkpoint(checkpoint_id, {"oauth_code": "test-code"})

    assert complete_result["success"]
    assert complete_result["checkpoint"]["status"] == "completed"
    assert complete_result["checkpoint"]["completed_at"] is not None


def test_human_intervention_manager_fail_checkpoint():
    """Test failing a checkpoint."""
    manager = HumanInterventionManager()

    create_result = manager.create_checkpoint(
        mission_id="mission-123",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        instructions="Authorize",
    )

    checkpoint_id = create_result["checkpoint"]["id"]

    fail_result = manager.fail_checkpoint(checkpoint_id, reason="User declined authorization")

    assert fail_result["success"]
    assert fail_result["checkpoint"]["status"] == "failed"
    assert "failure_reason" in fail_result["checkpoint"]["metadata"]


class TestHumanInterventionManagerClientInjection:
    """Verify HumanInterventionManager accepts and uses an injected client."""

    def test_explicit_client_is_used(self) -> None:
        """An injected client takes precedence over the global client."""
        mock_client = MagicMock()
        manager = HumanInterventionManager(client=mock_client)
        assert manager._client is mock_client

    def test_no_client_falls_back_to_global(self) -> None:
        """Without an injected client, falls back to the global supabase_client."""
        manager = HumanInterventionManager()
        import app.database as database_module
        global_client = getattr(database_module, "supabase_client", None)
        assert manager._client is global_client

    def test_create_checkpoint_routes_to_explicit_client(self) -> None:
        """create_checkpoint writes to the injected client, not the global."""
        mock_client = MagicMock()
        mock_client.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "cp-test", "status": "awaiting_human", "metadata": {}}
        ]
        manager = HumanInterventionManager(client=mock_client)
        result = manager.create_checkpoint(
            mission_id="m-1",
            platform="pinterest",
            checkpoint_type="otp_required",
            instructions="Enter code",
        )
        assert result["success"]
        assert mock_client.table.call_count == 1
        assert mock_client.table.call_args[0][0] == "human_intervention_checkpoints"

    def test_get_checkpoint_routes_to_explicit_client(self) -> None:
        """get_checkpoint reads from the injected client."""
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": "cp-1", "status": "completed", "metadata": {}}
        ]
        manager = HumanInterventionManager(client=mock_client)
        result = manager.get_checkpoint("cp-1")
        assert result is not None
        assert result["id"] == "cp-1"
        assert mock_client.table.call_count == 1

    def test_explicit_client_none_falls_back(self) -> None:
        """Passing client=None explicitly still falls back to global."""
        manager = HumanInterventionManager(client=None)
        import app.database as database_module
        global_client = getattr(database_module, "supabase_client", None)
        assert manager._client is global_client
