"""Validation boundary for deterministic or model-generated plans.

Validation is side-effect free. External tools are only considered available
when they appear in a successful, owner-scoped capability discovery result.
"""
from __future__ import annotations

from typing import Any

from .capability_discovery import CapabilityDiscovery
from .p1_7_contracts import classify_approval, validate_input_schema
from .tool_registry import ToolRegistry


_ALLOWED_RISK_LEVELS = {"READ_ONLY", "WRITE_INTERNAL", "WRITE_EXTERNAL", "DESTRUCTIVE"}


class PlanValidator:
    """Validate schemas, capability availability and approval boundaries."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        capability_discovery: CapabilityDiscovery | None = None,
    ) -> None:
        self._tools = tool_registry
        self._capabilities = capability_discovery

    def validate(
        self,
        steps: list[dict[str, Any]],
        *,
        owner_id: str | None = None,
        available_tools: set[str] | None = None,
        discovered: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        errors: list[str] = []
        if not isinstance(steps, list) or not steps:
            return {"success": False, "errors": ["Plan must contain at least one step"], "steps": []}
        if len(steps) > 50:
            return {"success": False, "errors": ["Plan exceeds the 50-step execution bound"], "steps": []}

        allowed = available_tools
        if self._capabilities is not None:
            if not owner_id:
                errors.append("Authenticated owner is required for capability discovery")
            if discovered is None:
                errors.append("User-scoped capability discovery must be performed before validating this plan")
            elif discovered.get("success") is not True:
                errors.append("Capability discovery failed; plan validation is fail-closed")
            elif discovered.get("owner_id") != owner_id:
                errors.append("Capability discovery owner does not match authenticated owner")
            else:
                allowed = {
                    tool.get("tool_name", tool.get("name"))
                    for tool in discovered.get("tools", [])
                    if isinstance(tool, dict)
                }
        elif discovered is not None:
            if not owner_id or discovered.get("owner_id") != owner_id:
                errors.append("Capability discovery owner does not match authenticated owner")
            elif discovered.get("success") is not True:
                errors.append("Capability discovery failed; plan validation is fail-closed")
            else:
                allowed = {
                    tool.get("tool_name", tool.get("name"))
                    for tool in discovered.get("tools", [])
                    if isinstance(tool, dict)
                }

        validated_steps: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"Step {index} must be an object")
                continue
            tool_name = step.get("tool_name") or step.get("action_type")
            result = self._tools.get_tool(tool_name or "")
            if not result.get("success"):
                errors.append(f"Step {index}: unknown tool '{tool_name}'")
                continue
            tool = result["tool"]
            if allowed is not None and tool_name not in allowed:
                errors.append(f"Step {index}: tool '{tool_name}' is not available for this user")
            elif tool.get("platform") and allowed is None:
                errors.append(f"Step {index}: external tool requires owner-scoped capability discovery")

            risk_level = str(tool.get("risk_level", "READ_ONLY")).upper()
            if risk_level not in _ALLOWED_RISK_LEVELS:
                errors.append(f"Step {index}: unsupported tool risk level '{risk_level}'")
            payload = step.get("input", step.get("action_payload", {}))
            schema_error = validate_input_schema(payload, tool.get("input_schema") or tool.get("schema"))
            if schema_error:
                errors.append(f"Step {index}: {schema_error}")

            approval_class = classify_approval_from_metadata(tool)
            approval_required = approval_class != "READ_ONLY"
            if step.get("requires_approval") is False and approval_required:
                errors.append(f"Step {index}: approval requirement cannot be bypassed")

            normalized = dict(step)
            normalized["tool_name"] = tool_name
            normalized["risk_level"] = risk_level
            normalized["approval_class"] = approval_class
            normalized["requires_approval"] = approval_required
            normalized["tool_version"] = tool.get("version", "1.0")
            validated_steps.append(normalized)

        return {
            "success": not errors,
            "errors": errors,
            "steps": validated_steps if not errors else [],
            "owner_id": owner_id,
            "validation_only": True,
        }


def classify_approval_from_metadata(tool: dict[str, Any]) -> str:
    """Classify a registry dictionary without rebuilding the full contract."""
    class _Contract:
        risk_level = str(tool.get("risk_level", "READ_ONLY"))
        requires_approval = bool(tool.get("requires_approval", False))

    return classify_approval(_Contract())


__all__ = ["PlanValidator", "classify_approval_from_metadata"]
