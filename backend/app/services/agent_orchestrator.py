"""Agent orchestration service for Sprint 5.0.

This module coordinates the end-to-end workflow for lightweight autonomous
agent execution. It wires together the mission, worker, decision, action,
tool, and memory services in a defensive way so tests and local environments
remain stable even when supporting dependencies are unavailable.
"""

from __future__ import annotations

from typing import Any

from .action_engine import ActionEngine
from .decision_engine import AtlasDecisionEngine
from .memory_engine import AtlasMemoryEngine
from .mission_engine import MissionEngine
from .tool_registry import ToolRegistry
from .worker_runtime import WorkerRuntime
from .employee_vertical_slice import EmployeeVerticalSlice


class AgentOrchestrator:
    """Coordinate the autonomous agent workflow across lightweight services."""

    def __init__(self, owner_id: str | None = None, client: Any | None = None) -> None:
        """Initialize the orchestrator and its supporting services."""

        self._owner_id = owner_id
        self._mission_engine = MissionEngine(client=client)
        self._worker_runtime = WorkerRuntime(owner_id=owner_id, client=client)
        self._decision_engine = AtlasDecisionEngine(owner_id=owner_id)
        self._action_engine = ActionEngine(owner_id=owner_id)
        self._tool_registry = ToolRegistry()
        self._memory_engine = AtlasMemoryEngine(owner_id=owner_id)
        self._employee_slice = EmployeeVerticalSlice(
            owner_id=owner_id or "anonymous",
            client=client,
        ) if owner_id else None
        self._wire_shared_services()

    def run_objective(
        self,
        goal: str,
        *,
        mission_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the P1-7B objective-to-report flow for the current user."""
        if self._employee_slice is None:
            return {"success": False, "status": "FAIL", "error": "Authenticated owner is required"}
        return self._employee_slice.run(goal, mission_id=mission_id, metadata=metadata)

    def run_mission(self, mission_id: str) -> dict[str, Any]:
        """Run a single mission through the orchestrated workflow.

        Args:
            mission_id: The identifier of the mission to execute.

        Returns:
            A structured dictionary describing the orchestration outcome.
        """

        if not mission_id or not str(mission_id).strip():
            return {"success": False, "error": "Mission id is required"}

        try:
            mission = self._mission_engine.get_mission(mission_id, owner_id=self._owner_id)
            if not mission:
                return {"success": False, "error": "Mission not found"}

            worker_id = mission.get("worker_id") or mission.get("assigned_worker")
            if not worker_id:
                return {"success": False, "error": "Mission has no assigned worker"}

            decision = self._decision_engine.next_action()
            action_result = self._action_engine.execute_action(
                "log",
                {"message": f"Executing mission {mission_id}"},
            )
            execution_result = self._worker_runtime.execute_mission(mission_id)

            return {
                "success": bool(execution_result.get("success")),
                "mission_id": mission_id,
                "worker_id": worker_id,
                "mission": mission,
                "decision": decision,
                "action_result": action_result,
                "execution": execution_result,
            }
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return {"success": False, "error": str(exc)}

    def run_worker(self, worker_id: str) -> dict[str, Any]:
        """Execute all pending missions for a worker sequentially.

        Args:
            worker_id: The worker identifier whose pending missions should run.

        Returns:
            A structured summary of the execution attempt.
        """

        if not worker_id or not str(worker_id).strip():
            return {"success": False, "error": "Worker id is required"}

        try:
            missions = self._mission_engine.get_worker_missions(worker_id, limit=50)
            pending_missions = [
                mission
                for mission in missions
                if str((mission.get("status") or "pending")).lower() == "pending"
            ]

            results: list[dict[str, Any]] = []
            succeeded = 0
            failed = 0

            for mission in pending_missions:
                mission_id = mission.get("id")
                if not mission_id:
                    continue

                try:
                    result = self._worker_runtime.execute_mission(mission_id)
                except Exception as exc:  # pragma: no cover - defensive runtime handling
                    result = {"success": False, "error": str(exc)}

                results.append({"mission_id": mission_id, "result": result})
                if result.get("success"):
                    succeeded += 1
                else:
                    failed += 1

            return {
                "success": True,
                "worker_id": worker_id,
                "pending_count": len(pending_missions),
                "succeeded": succeeded,
                "failed": failed,
                "results": results,
            }
        except Exception as exc:  # pragma: no cover - defensive runtime handling
            return {"success": False, "error": str(exc)}

    def health_check(self) -> dict[str, Any]:
        """Return a lightweight health report for all orchestrated services."""

        services = {
            "mission_engine": bool(self._mission_engine),
            "worker_runtime": bool(self._worker_runtime),
            "decision_engine": bool(self._decision_engine),
            "action_engine": bool(self._action_engine),
            "tool_registry": bool(self._tool_registry),
            "memory_engine": bool(self._memory_engine),
        }
        return {"status": "healthy", "services": services}

    def _wire_shared_services(self) -> None:
        """Reuse the shared lightweight services where supported."""

        try:
            self._worker_runtime._mission_engine = self._mission_engine
            self._worker_runtime._memory_engine = self._memory_engine
            self._worker_runtime._decision_engine = self._decision_engine
        except Exception:  # pragma: no cover - defensive runtime handling
            pass

        try:
            self._action_engine._memory_engine = self._memory_engine
        except Exception:  # pragma: no cover - defensive runtime handling
            pass


__all__ = ["AgentOrchestrator"]
