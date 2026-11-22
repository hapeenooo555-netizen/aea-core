"""Canonical, provider-neutral contracts for the AEA employee runtime.

This is a contract boundary, not a second orchestrator. Mission persistence,
execution, approvals and recovery remain owned by the existing durable services.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from .p1_7_contracts import sanitize_payload


RUNTIME_STATES = frozenset({
    "ACCEPTED", "PLANNING", "VALIDATING", "READY", "RUNNING",
    "WAITING_APPROVAL", "WAITING_INPUT", "PAUSED", "SUCCEEDED",
    "PARTIAL", "FAILED", "CANCELLED",
})

MISSION_TO_RUNTIME = {
    "pending": "ACCEPTED",
    "scheduled": "ACCEPTED",
    "active": "RUNNING",
    "retrying": "RUNNING",
    "waiting_dependency": "WAITING_INPUT",
    "waiting_approval": "WAITING_APPROVAL",
    "waiting_human": "WAITING_INPUT",
    "paused": "PAUSED",
    "completed": "SUCCEEDED",
    "failed": "FAILED",
    "cancelled": "CANCELLED",
}


@dataclass(frozen=True)
class RuntimeRequest:
    owner_id: str
    objective: str
    request_id: str = field(default_factory=lambda: str(uuid4()))
    locale: str = "en"
    timezone: str = "UTC"
    success_criteria: tuple[str, ...] = ()
    constraints: dict[str, Any] = field(default_factory=dict)
    requested_capabilities: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.owner_id.strip():
            errors.append("owner_id is required")
        if not self.objective.strip():
            errors.append("objective is required")
        if not self.request_id.strip():
            errors.append("request_id is required")
        if not self.locale.strip():
            errors.append("locale is required")
        if not self.timezone.strip():
            errors.append("timezone is required")
        if len(self.objective) > 12000:
            errors.append("objective exceeds 12000 characters")
        if not isinstance(self.constraints, dict):
            errors.append("constraints must be an object")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return sanitize_payload(asdict(self))


@dataclass(frozen=True)
class RuntimeResult:
    request_id: str
    owner_id: str
    status: str
    mission_id: str | None = None
    execution_id: str | None = None
    output: dict[str, Any] = field(default_factory=dict)
    pending_approvals: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.request_id.strip():
            errors.append("request_id is required")
        if not self.owner_id.strip():
            errors.append("owner_id is required")
        if self.status not in RUNTIME_STATES:
            errors.append(f"unsupported runtime status: {self.status}")
        if self.status == "SUCCEEDED" and self.errors:
            errors.append("SUCCEEDED result cannot contain errors")
        if self.status == "WAITING_APPROVAL" and not self.pending_approvals:
            errors.append("WAITING_APPROVAL requires at least one approval reference")
        if not isinstance(self.output, dict) or not isinstance(self.evidence, dict):
            errors.append("output and evidence must be objects")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return sanitize_payload(asdict(self))


def runtime_status_from_mission(status: str) -> str:
    """Normalize the existing durable mission state into the shared runtime vocabulary."""
    normalized = str(status or "").strip().lower()
    if normalized not in MISSION_TO_RUNTIME:
        raise ValueError(f"Unsupported mission status: {status}")
    return MISSION_TO_RUNTIME[normalized]


__all__ = [
    "MISSION_TO_RUNTIME", "RUNTIME_STATES", "RuntimeRequest", "RuntimeResult",
    "runtime_status_from_mission",
]
