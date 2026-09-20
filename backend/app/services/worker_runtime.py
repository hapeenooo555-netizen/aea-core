"""Worker runtime service for Sprint 3.2.

This module coordinates the mission, memory, and decision services into a
lightweight execution flow for worker-driven tasks. The implementation stays
fully defensive so tests and local environments continue to work even when
Supabase or the supporting services are unavailable.

Sprint 7.2-B extends this runtime to support platform connector operations
with human intervention checkpoints and pause/resume workflows.
"""

from __future__ import annotations

from typing import Any

from .action_engine import ActionEngine
from .approval_gateway import ApprovalGateway
from .decision_engine import AtlasDecisionEngine
from .employee_engine import EmployeeEngine
from .human_intervention import HumanInterventionManager
from .memory_engine import AtlasMemoryEngine
from .mission_engine import MissionEngine
from .connectors.registry import ConnectorRegistry


class WorkerRuntime:
    """Coordinate mission execution with the existing lightweight services."""

    def __init__(
        self,
        connector_registry: ConnectorRegistry | None = None,
        owner_id: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Initialize the runtime with its supporting engines.

        Args:
            connector_registry: Optional ConnectorRegistry for platform operations.
                              If not provided, platform actions will not be executed.
            owner_id: Canonical owner identifier (auth.users.id). When provided,
                memory operations will be scoped to this owner for RLS enforcement.
        """

        self._owner_id = owner_id
        self._mission_engine = MissionEngine(client=client)
        self._memory_engine = AtlasMemoryEngine()
        self._decision_engine = AtlasDecisionEngine(owner_id=owner_id)
        self._action_engine = ActionEngine(owner_id=owner_id)
        self._approval_gateway = ApprovalGateway()
        self._connector_registry = connector_registry
        self._human_intervention_manager = HumanInterventionManager(client=client)
        self._employee_engine = EmployeeEngine(
            connector_registry=connector_registry,
            owner_id=owner_id,
            client=client,
        )

    def execute_mission(self, mission_id: str) -> dict[str, Any]:
        """Execute a mission through the durable employee loop workflow.

        Args:
            mission_id: The unique mission identifier to execute.

        Returns:
            A structured dictionary describing the outcome of the execution flow.
        """

        mission = self._mission_engine.get_mission(mission_id, owner_id=self._owner_id)
        if not mission:
            return {"success": False, "error": "Mission not found"}

        worker_id = mission.get("worker_id") or mission.get("assigned_worker")
        if not worker_id:
            return {"success": False, "error": "Mission has no assigned worker"}

        try:
            self._mission_engine.update_status(mission_id, "active")
        except Exception:  # pragma: no cover - defensive runtime handling
            pass

        return self._employee_engine.run_mission(mission_id)

    def resume_mission(self, mission_id: str) -> dict[str, Any]:
        """Resume a mission from persistent state after restart.

        Args:
            mission_id: The unique mission identifier to resume.

        Returns:
            A structured dictionary describing the outcome of the resume flow.
        """
        return self._employee_engine.resume_mission(mission_id)

    def resume_after_approval(
        self,
        mission_id: str,
        execution_id: str,
        worker_id: str,
        approval_request_id: str,
    ) -> dict[str, Any]:
        """Resume execution after approval is granted.

        Args:
            mission_id: The mission identifier.
            execution_id: The execution identifier.
            worker_id: The worker identifier.
            approval_request_id: The approval request ID.

        Returns:
            A structured dictionary describing the outcome.
        """
        return self._employee_engine.resume_after_approval(
            mission_id, execution_id, worker_id, approval_request_id,
        )

    def execute_action_with_approval(
        self,
        mission_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an action with approval gate if required.

        Before executing sensitive actions, this method:
        1. Checks action risk
        2. If approval required: creates approval request and pauses
        3. If approved: continues execution

        Args:
            mission_id: The mission identifier.
            action_type: The type of action to execute.
            payload: Action-specific payload.

        Returns:
            Execution result or approval pending status.
        """
        # Classify the action
        envelope = self._action_engine.create_action_envelope(action_type, payload)

        # Check if approval is required
        if not envelope["requires_approval"]:
            # Safe to execute immediately
            return self._action_engine.execute_action(action_type, payload)

        # Create approval request for sensitive action
        request_result = self._approval_gateway.create_request(
            mission_id=mission_id,
            action_type=action_type,
            risk_level=envelope["risk_level"],
            payload=payload,
        )

        if not request_result.get("success"):
            return {
                "success": False,
                "error": "Failed to create approval request",
            }

        request = request_result.get("request", {})
        request_id = request.get("id")

        # Return pending approval state - execution paused
        return {
            "success": False,
            "pending_approval": True,
            "approval_request_id": request_id,
            "action_type": action_type,
            "risk_level": envelope["risk_level"],
            "message": f"Action {action_type} requires approval before execution",
        }

    def continue_with_approval(
        self,
        mission_id: str,
        approval_request_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Continue action execution after approval.

        Args:
            mission_id: The mission identifier.
            approval_request_id: The approval request ID.
            action_type: The type of action to execute.
            payload: Action-specific payload.

        Returns:
            Execution result.
        """
        # Verify approval status
        if not self._approval_gateway.is_approved(approval_request_id):
            return {
                "success": False,
                "error": "Approval not granted for this action",
            }

        # Execute the action
        return self._action_engine.execute_action(action_type, payload)

    def execute_connector_action(
        self,
        mission_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a connector action, handling approval and human intervention as needed.

        This method coordinates platform connector operations with approval gates
        and human intervention checkpoints.

        Args:
            mission_id: The mission identifier.
            action_type: The connector action type (start_platform_onboarding, etc.).
            payload: Action-specific payload (must include 'platform' key).

        Returns:
            Execution result or checkpoint/approval pending status.
        """
        # Verify connector registry is available
        if not self._connector_registry:
            return {
                "success": False,
                "error": "Connector registry not available",
            }

        # Get platform from payload
        platform = payload.get("platform")
        if not platform:
            return {
                "success": False,
                "error": "Platform must be specified in payload",
            }

        # Check if connector exists
        connector = self._connector_registry.get(platform)
        if not connector:
            return {
                "success": False,
                "error": f"No connector registered for platform '{platform}'",
                "supported_platforms": self._connector_registry.list_platforms(),
            }

        # Check if approval is required for this action
        envelope = self._action_engine.create_action_envelope(action_type, payload)

        if envelope["requires_approval"]:
            # Create approval request
            request_result = self._approval_gateway.create_request(
                mission_id=mission_id,
                action_type=action_type,
                risk_level=envelope["risk_level"],
                payload=payload,
            )

            if not request_result.get("success"):
                return {
                    "success": False,
                    "error": "Failed to create approval request",
                }

            request = request_result.get("request", {})
            request_id = request.get("id")

            return {
                "success": False,
                "pending_approval": True,
                "approval_request_id": request_id,
                "action_type": action_type,
                "platform": platform,
                "message": f"Action {action_type} on {platform} requires approval",
            }

        # Action is safe, dispatch to connector
        return self._dispatch_to_connector(
            mission_id,
            action_type,
            payload,
            connector,
        )

    def _dispatch_to_connector(
        self,
        mission_id: str,
        action_type: str,
        payload: dict[str, Any],
        connector: Any,
    ) -> dict[str, Any]:
        """Dispatch an action to a connector and handle results.

        Args:
            mission_id: The mission identifier.
            action_type: The action type.
            payload: Action payload.
            connector: The connector instance to dispatch to.

        Returns:
            Dispatch result.
        """
        worker_id = payload.get("worker_id")

        try:
            if action_type == "start_platform_onboarding":
                result = connector.start_onboarding(worker_id, mission_id=mission_id)

                # Check if human intervention is needed
                if result.get("requires_human_intervention"):
                    # Create a human intervention checkpoint
                    checkpoint_result = self._human_intervention_manager.create_checkpoint(
                        mission_id=mission_id,
                        platform=connector.platform,
                        checkpoint_type=result.get("checkpoint_type", "manual_platform_step_required"),
                        instructions=result.get("instructions", "Complete the required step on the platform"),
                        metadata={
                            "workflow_id": result.get("workflow_id"),
                            "step": result.get("current_step"),
                            "total_steps": result.get("total_steps"),
                            "metadata": result.get("metadata", {}),
                        },
                    )

                    if checkpoint_result.get("success"):
                        checkpoint = checkpoint_result.get("checkpoint", {})
                        return {
                            "success": False,
                            "awaiting_human_intervention": True,
                            "checkpoint_id": checkpoint.get("id"),
                            "action_type": action_type,
                            "platform": connector.platform,
                            "workflow_id": result.get("workflow_id"),
                            "instructions": result.get("instructions"),
                            "checkpoint_type": result.get("checkpoint_type"),
                            "message": f"Human intervention required: {result.get('instructions')}",
                        }

                    return checkpoint_result

                # No human intervention needed
                return result

            elif action_type == "resume_platform_onboarding":
                workflow_id = payload.get("workflow_id")
                human_input = payload.get("human_input", {})
                result = connector.resume_onboarding(workflow_id, human_input)

                # Update the checkpoint status
                checkpoint_id = payload.get("checkpoint_id")
                if checkpoint_id:
                    self._human_intervention_manager.complete_checkpoint(
                        checkpoint_id,
                        human_input,
                    )

                # Check if another human intervention is needed
                if result.get("requires_human_intervention"):
                    checkpoint_result = self._human_intervention_manager.create_checkpoint(
                        mission_id=mission_id,
                        platform=connector.platform,
                        checkpoint_type=result.get("checkpoint_type", "manual_platform_step_required"),
                        instructions=result.get("instructions", "Complete the next step"),
                        metadata={
                            "workflow_id": result.get("workflow_id"),
                            "step": result.get("current_step"),
                            "total_steps": result.get("total_steps"),
                            "metadata": result.get("metadata", {}),
                        },
                    )

                    if checkpoint_result.get("success"):
                        checkpoint = checkpoint_result.get("checkpoint", {})
                        return {
                            "success": False,
                            "awaiting_human_intervention": True,
                            "checkpoint_id": checkpoint.get("id"),
                            "action_type": action_type,
                            "platform": connector.platform,
                            "workflow_id": result.get("workflow_id"),
                            "instructions": result.get("instructions"),
                            "checkpoint_type": result.get("checkpoint_type"),
                            "message": f"Next step required: {result.get('instructions')}",
                        }

                    return checkpoint_result

                # Workflow completed
                return result

            elif action_type == "check_platform_status":
                return connector.health_check()

            else:
                return {
                    "success": False,
                    "error": f"Connector action '{action_type}' not implemented for {connector.platform}",
                }

        except NotImplementedError as e:
            return {
                "success": False,
                "error": f"Action not supported: {str(e)}",
            }
        except Exception as e:  # pragma: no cover - defensive error handling
            return {
                "success": False,
                "error": f"Connector error: {str(e)}",
            }

    def get_connector_registry(self) -> ConnectorRegistry | None:
        """Get the connector registry.

        Returns:
            The ConnectorRegistry instance or None if not set.
        """
        return self._connector_registry

    def set_connector_registry(self, registry: ConnectorRegistry) -> None:
        """Set the connector registry for platform operations.

        Args:
            registry: The ConnectorRegistry instance to use.
        """
        self._connector_registry = registry

    def get_worker_state(self, worker_id: str) -> dict[str, Any]:
        """Build a lightweight snapshot of the worker's current state.

        Args:
            worker_id: The worker identifier to inspect.

        Returns:
            A structured dictionary with recent missions and memories.
        """

        missions = self._mission_engine.get_worker_missions(worker_id, limit=10)
        memories = self._memory_engine.get_recent_memories(worker_id, limit=10)
        return {
            "worker_id": worker_id,
            "mission_count": len(missions),
            "missions": missions,
            "memories": memories,
        }


__all__ = ["WorkerRuntime"]
