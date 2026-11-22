import pytest

from app.services.agent_runtime_contract import (
    RuntimeRequest,
    RuntimeResult,
    runtime_status_from_mission,
)


def test_runtime_request_requires_owner_and_objective():
    request = RuntimeRequest(owner_id="", objective=" ")
    assert "owner_id is required" in request.validate()
    assert "objective is required" in request.validate()


def test_runtime_request_serialization_redacts_credentials_recursively():
    request = RuntimeRequest(
        owner_id="owner-1",
        objective="Prepare a draft",
        constraints={"api_key": "must-not-leak", "market": "TZ"},
    )
    serialized = request.to_dict()
    assert "api_key" not in serialized["constraints"]
    assert serialized["constraints"]["market"] == "TZ"


def test_runtime_result_rejects_success_with_errors():
    result = RuntimeResult(
        request_id="req-1",
        owner_id="owner-1",
        status="SUCCEEDED",
        errors=("provider failed",),
    )
    assert "SUCCEEDED result cannot contain errors" in result.validate()


def test_waiting_approval_requires_a_reference():
    result = RuntimeResult(
        request_id="req-1",
        owner_id="owner-1",
        status="WAITING_APPROVAL",
    )
    assert "WAITING_APPROVAL requires at least one approval reference" in result.validate()


def test_existing_mission_states_map_to_one_runtime_vocabulary():
    assert runtime_status_from_mission("completed") == "SUCCEEDED"
    assert runtime_status_from_mission("waiting_approval") == "WAITING_APPROVAL"
    assert runtime_status_from_mission("retrying") == "RUNNING"
    with pytest.raises(ValueError):
        runtime_status_from_mission("unknown-state")
