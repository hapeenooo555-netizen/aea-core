"""Small, provider-neutral contracts for the P1-7A employee foundation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


SENSITIVE_FIELDS = frozenset({
    "access_token", "refresh_token", "authorization_code", "oauth_code",
    "client_secret", "api_key", "token", "password", "id_token", "session",
    "code", "redirect_uri", "secret", "private_key", "cookie",
})


def sanitize_payload(value: Any) -> Any:
    """Return a copy without credential-bearing fields."""
    if isinstance(value, dict):
        return {
            key: sanitize_payload(item)
            for key, item in value.items()
            if isinstance(key, str) and key.lower() not in SENSITIVE_FIELDS
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return deepcopy(value)


def validate_input_schema(payload: Any, schema: dict[str, Any] | None) -> str | None:
    """Validate the deliberately small JSON-schema subset used by tools."""
    if not isinstance(payload, dict):
        return "Payload must be a dictionary"
    if not schema:
        return None
    properties = schema.get("properties", {})
    for field_name in schema.get("required", []):
        if field_name not in payload:
            return f"Required field '{field_name}' is missing"
    if schema.get("additionalProperties", True) is False:
        unknown = set(payload) - set(properties)
        if unknown:
            return f"Unexpected field '{sorted(unknown)[0]}' not allowed by schema"
    for field_name, definition in properties.items():
        if field_name not in payload:
            continue
        expected = definition.get("type") if isinstance(definition, dict) else None
        value = payload[field_name]
        valid = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }.get(expected, True)
        if not valid:
            return f"Field '{field_name}': expected type '{expected}', got '{type(value).__name__}'"
    return None


@dataclass(frozen=True)
class ToolContract:
    """Canonical metadata for a registered executable tool."""

    tool_name: str
    version: str = "1.0"
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    capability: str | None = None
    platform: str | None = None
    operation: str | None = None
    risk_level: str = "READ_ONLY"
    requires_connection: bool = False
    requires_approval: bool = False
    supports_dry_run: bool = True
    idempotency_behavior: str = "not_applicable"
    timeout: float = 30.0
    retry_policy: str = "bounded"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["name"] = result["tool_name"]
        result["schema"] = deepcopy(result["input_schema"])
        result["tool_type"] = "external" if self.platform else "internal"
        return result


@dataclass(frozen=True)
class Objective:
    """Structured interpretation of a user's mission while preserving its goal."""

    goal: str
    desired_outcome: str | None = None
    platform_constraints: list[str] = field(default_factory=list)
    content_inputs: dict[str, Any] = field(default_factory=dict)
    requested_actions: list[str] = field(default_factory=list)
    deadline: str | None = None
    approval_preferences: dict[str, Any] = field(default_factory=dict)
    risk_tolerance: str | None = None
    success_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObjectiveParser:
    """Conservative parser that never replaces the original user goal."""

    def parse(self, goal: str, metadata: dict[str, Any] | None = None) -> Objective:
        metadata = metadata if isinstance(metadata, dict) else {}
        actions = metadata.get("requested_actions", [])
        criteria = metadata.get("success_criteria", [])
        content_inputs = dict(metadata.get("content_inputs", {}))
        if metadata.get("platform") and "platform" not in content_inputs:
            content_inputs["platform"] = metadata["platform"]
        if metadata.get("vertical") and "vertical" not in content_inputs:
            content_inputs["vertical"] = metadata["vertical"]
        for affiliate_key in ("country", "language", "niche", "daily_limit", "human_approval_required"):
            if affiliate_key in metadata and affiliate_key not in content_inputs:
                content_inputs[affiliate_key] = metadata[affiliate_key]
        platform_constraints = list(metadata.get("platform_constraints", []))
        if metadata.get("platform") and metadata["platform"] not in platform_constraints:
            platform_constraints.append(metadata["platform"])
        return Objective(
            goal=goal or "",
            desired_outcome=metadata.get("desired_outcome"),
            platform_constraints=platform_constraints,
            content_inputs=content_inputs,
            requested_actions=list(actions) if isinstance(actions, list) else [],
            deadline=metadata.get("deadline"),
            approval_preferences=dict(metadata.get("approval_preferences", {})),
            risk_tolerance=metadata.get("risk_tolerance"),
            success_criteria=list(criteria) if isinstance(criteria, list) else [],
        )


@dataclass(frozen=True)
class Observation:
    action: str
    result: Any
    success: bool
    state_change: str | None = None
    new_information: list[str] = field(default_factory=list)
    next_possible_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["result"] = sanitize_payload(result["result"])
        return result


class BoundedDecisionLoop:
    """Bound next-decision production without owning durable execution state."""

    def __init__(self, max_decisions: int = 10) -> None:
        if max_decisions < 1:
            raise ValueError("max_decisions must be positive")
        self.max_decisions = max_decisions
        self.decisions = 0

    def allow_next(self) -> bool:
        if self.decisions >= self.max_decisions:
            return False
        self.decisions += 1
        return True


def classify_approval(contract: ToolContract) -> str:
    """Return the centralized approval class expected by the runtime."""
    if contract.risk_level.upper() == "DESTRUCTIVE":
        return "ALWAYS_APPROVE"
    if contract.risk_level.upper() == "WRITE_EXTERNAL" or contract.requires_approval:
        return "REQUIRES_APPROVAL"
    return "READ_ONLY"


__all__ = [
    "BoundedDecisionLoop", "Objective", "ObjectiveParser", "Observation",
    "SENSITIVE_FIELDS", "ToolContract", "classify_approval", "sanitize_payload",
    "validate_input_schema",
]