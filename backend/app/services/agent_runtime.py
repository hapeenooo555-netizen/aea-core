"""Compatibility adapter from the canonical runtime contract to P1-8 missions.

The adapter delegates persistence, claims, approvals and execution to the
existing MissionOrchestrationService. It deliberately does not execute tools
or create a parallel mission store.
"""
from __future__ import annotations

from typing import Any

from .agent_runtime_contract import RuntimeRequest, RuntimeResult, runtime_status_from_mission
from .mission_orchestration import MissionOrchestrationService


class AEAAgentRuntime:
    """Canonical entry point for submitting and running an owner-scoped objective."""

    def __init__(self, owner_id: str, *, client: Any | None = None,
                 orchestration: MissionOrchestrationService | None = None) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._orchestration = orchestration or MissionOrchestrationService(owner_id, client=client)

    def submit(self, request: RuntimeRequest, *, run_now: bool = False, mission_options: dict[str, Any] | None = None) -> dict[str, Any]:
        errors = request.validate()
        if errors:
            return {"success": False, "status": "REJECTED", "errors": errors}
        if request.owner_id != self.owner_id:
            return {"success": False, "status": "REJECTED",
                    "errors": ["Runtime request owner does not match authenticated owner"]}

        metadata = {
            "runtime_request_id": request.request_id,
            "trace_id": request.request_id,
            "runtime_contract_version": "1.0",
            "locale": request.locale,
            "timezone": request.timezone,
            "success_criteria": list(request.success_criteria),
            "constraints": request.constraints,
            "requested_capabilities": list(request.requested_capabilities),
        }
        options = dict(mission_options or {})
        supplied_metadata = options.pop("metadata", {})
        if not isinstance(supplied_metadata, dict):
            return {"success": False, "status": "REJECTED",
                    "errors": ["mission_options.metadata must be an object"]}
        metadata.update(supplied_metadata)
        created = self._orchestration.create_mission(
            request.objective,
            metadata=metadata,
            idempotency_key=request.request_id,
            **options,
        )
        if not created.get("success"):
            return {"success": False, "status": "FAILED",
                    "errors": [str(created.get("error") or "Mission creation failed")]}
        mission = created.get("mission") or {}
        mission_id = mission.get("id")
        if not mission_id:
            return {"success": False, "status": "FAILED",
                    "errors": ["Mission service returned no mission identifier"]}
        if run_now:
            outcome = self._orchestration.run_mission(mission_id)
            mission = outcome.get("mission") or self._orchestration.get_mission(mission_id) or mission
            if not outcome.get("success") and not outcome.get("status"):
                return {"success": False, "status": "FAILED", "mission_id": mission_id,
                        "errors": [str(outcome.get("error") or "Mission execution failed")]}
        try:
            runtime_status = runtime_status_from_mission(mission.get("status") or "pending")
        except ValueError as exc:
            return {"success": False, "status": "FAILED", "mission_id": mission_id,
                    "errors": [str(exc)]}
        result = RuntimeResult(
            request_id=request.request_id,
            owner_id=self.owner_id,
            status=runtime_status,
            mission_id=mission_id,
            output=mission.get("result") if isinstance(mission.get("result"), dict) else {},
            pending_approvals=tuple(
                str(item) for item in (mission.get("pending_approvals") or [])
            ),
            errors=tuple(
                [str(mission["error"])] if mission.get("error") and runtime_status == "FAILED" else []
            ),
            evidence={
                "runtime_contract_version": "1.0",
                "trace_id": request.request_id,
                "mission_status": mission.get("status"),
                "idempotent": bool(created.get("idempotent", False)),
            },
        )
        validation_errors = result.validate()
        if validation_errors:
            return {"success": False, "status": "FAILED", "mission_id": mission_id,
                    "errors": validation_errors}
        return {"success": True, **result.to_dict()}


__all__ = ["AEAAgentRuntime"]
