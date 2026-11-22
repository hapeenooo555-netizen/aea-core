import pytest

from app.services.plan_validator import PlanValidator
from app.services.tool_registry import ToolRegistry


def _registry():
    registry = ToolRegistry()
    registry.register_tool(
        "content.create",
        "Create a content draft",
        schema={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False},
        capability="content.generate",
        risk_level="WRITE_INTERNAL",
        requires_approval=False,
    )
    registry.register_tool(
        "pinterest.publish",
        "Publish a Pin",
        tool_type="external",
        schema={"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"], "additionalProperties": False},
        capability="publish",
        platform="pinterest",
        risk_level="WRITE_EXTERNAL",
        requires_connection=True,
        requires_approval=True,
    )
    return registry


def test_plan_validator_rejects_external_tool_without_owner_scoped_discovery():
    validator = PlanValidator(_registry())
    result = validator.validate([{"tool_name": "pinterest.publish", "input": {"board_id": "b1"}}])
    assert result["success"] is False
    assert "owner-scoped capability discovery" in " ".join(result["errors"])


def test_plan_validator_rejects_discovery_for_different_owner():
    validator = PlanValidator(_registry())
    result = validator.validate(
        [{"tool_name": "pinterest.publish", "input": {"board_id": "b1"}}],
        owner_id="owner-a",
        discovered={"success": True, "owner_id": "owner-b", "tools": [{"tool_name": "pinterest.publish"}]},
    )
    assert result["success"] is False
    assert any("does not match" in error for error in result["errors"])


def test_plan_validator_marks_external_write_as_approval_required():
    validator = PlanValidator(_registry())
    result = validator.validate(
        [{"tool_name": "pinterest.publish", "input": {"board_id": "b1"}}],
        owner_id="owner-a",
        discovered={"success": True, "owner_id": "owner-a", "tools": [{"tool_name": "pinterest.publish"}]},
    )
    assert result["success"] is True
    step = result["steps"][0]
    assert step["requires_approval"] is True
    assert step["approval_class"] == "REQUIRES_APPROVAL"
    assert step["risk_level"] == "WRITE_EXTERNAL"


def test_plan_validator_does_not_allow_approval_bypass():
    validator = PlanValidator(_registry())
    result = validator.validate(
        [{"tool_name": "pinterest.publish", "input": {"board_id": "b1"}, "requires_approval": False}],
        owner_id="owner-a",
        discovered={"success": True, "owner_id": "owner-a", "tools": [{"tool_name": "pinterest.publish"}]},
    )
    assert result["success"] is False
    assert any("cannot be bypassed" in error for error in result["errors"])


def test_plan_validator_enforces_input_schema_and_step_bound():
    validator = PlanValidator(_registry())
    invalid = validator.validate([{"tool_name": "content.create", "input": {"unexpected": "x"}}])
    assert invalid["success"] is False
    assert any("Required field" in error for error in invalid["errors"])
    oversized = validator.validate([{"tool_name": "content.create", "input": {"title": "x"}}] * 51)
    assert oversized["success"] is False
    assert any("50-step" in error for error in oversized["errors"])


def test_plan_validator_annotates_internal_read_or_write_steps():
    validator = PlanValidator(_registry())
    result = validator.validate([{"tool_name": "content.create", "input": {"title": "Campaign"}}])
    assert result["success"] is True
    assert result["steps"][0]["tool_version"] == "1.0"
    assert result["steps"][0]["approval_class"] == "READ_ONLY"
