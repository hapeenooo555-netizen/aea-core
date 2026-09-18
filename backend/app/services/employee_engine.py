"""Employee engine for P1-6: Durable, Recoverable, Idempotent execution.

This module implements the full AI Employee execution loop with persistent
state in PostgreSQL/Supabase. The database is the source of truth for all
execution state. Python objects are used as runtime context/cache only.

Architecture:
  mission (existing root)
    └── mission_execution (durable runtime/session state)
          └── mission_steps (durable planned work + step lifecycle)

All state transitions are persisted to the database. Restart/recovery
loads state from PostgreSQL, not from Python objects.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .action_engine import ActionEngine
from .approval_gateway import ApprovalGateway
from .decision_engine import AtlasDecisionEngine
from .human_intervention import HumanInterventionManager
from .memory_engine import AtlasMemoryEngine
from .mission_engine import MissionEngine
from .mission_execution_service import (
    MissionExecutionService,
    VALID_EXECUTION_STATUSES,
    VALID_RETRY_CATEGORIES,
)
from .planning_engine import PlanningEngine
from .retry_classifier import (
    classify_failure,
    BoundedRetryPolicy,
    RETRYABLE_CATEGORIES,
    HUMAN_WAIT_CATEGORIES,
)
from .tool_registry import ToolRegistry
from .connectors.registry import ConnectorRegistry
from .tool_safety import ToolValidator, SafeActionExecutor, ToolSafetyError


class EmployeeEngine:
    """AI Employee execution loop with durable persistent state.

    The engine manages the full employee experience: loading mission state
    from PostgreSQL, creating plans from goals, executing steps with
    idempotency, handling approvals and human intervention, processing
    observations, and managing completion criteria.

    All execution state is persisted to PostgreSQL. Python objects are
    runtime context/cache only — never the source of truth.
    """

    def __init__(
        self,
        connector_registry: ConnectorRegistry | None = None,
        owner_id: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Initialize the employee engine with supporting services.

        Args:
            connector_registry: Optional ConnectorRegistry for platform operations.
            owner_id: Canonical owner identifier (auth.users.id).
        """

        self._owner_id = owner_id
        self._explicit_client = client
        self._mission_engine = MissionEngine(client=client)
        self._memory_engine = AtlasMemoryEngine()
        self._decision_engine = AtlasDecisionEngine(owner_id=owner_id)
        self._action_engine = ActionEngine(owner_id=owner_id)
        self._approval_gateway = ApprovalGateway()
        self._planning_engine = PlanningEngine()
        self._tool_registry = ToolRegistry()
        self._connector_registry = connector_registry
        self._human_intervention_manager = HumanInterventionManager(client=client)
        self._execution_service = MissionExecutionService(client=client)
        self._retry_policy = BoundedRetryPolicy()
        self._tool_validator = ToolValidator(self._tool_registry, self._action_engine)
        self._safe_executor = SafeActionExecutor(self._tool_validator, self._worker_runtime_stub())

        # Initialize test tools
        self._initialize_test_tools()

    def _worker_runtime_stub(self) -> Any:
        """Create a stub for SafeActionExecutor that handles connector actions."""
        return _WorkerRuntimeStub(self._connector_registry, self._human_intervention_manager, client=self._explicit_client)

    def _initialize_test_tools(self) -> None:
        """Register test tools for P1-5 minimal vertical slice."""
        self._tool_registry.register_tool(
            "test_echo",
            "Echo a message with metadata",
            "internal",
        )
        self._tool_registry.register_tool(
            "test_count",
            "Count from 1 to N",
            "internal",
        )
        self._tool_registry.register_tool(
            "log",
            "Log a message",
            "internal",
        )
        self._tool_registry.register_tool(
            "memory_store",
            "Store a memory entry",
            "internal",
        )

    # ------------------------------------------------------------------
    # Main execution entry points
    # ------------------------------------------------------------------

    def run_mission(self, mission_id: str) -> dict[str, Any]:
        """Execute a complete mission through the durable employee loop.

        Args:
            mission_id: The mission identifier to execute.

        Returns:
            Dictionary with execution results including status, progress,
            and any intermediate results.
        """

        mission = self._mission_engine.get_mission(mission_id)
        if not mission:
            return {"success": False, "error": "Mission not found"}

        worker_id = mission.get("assigned_worker") or mission.get("worker_id")
        if not worker_id:
            return {"success": False, "error": "Mission has no assigned worker"}

        # Try to load existing execution (restart/recovery path)
        existing_execution = self._execution_service.load_execution_for_mission(
            mission_id, worker_id,
        )

        if existing_execution:
            return self._resume_mission(
                mission_id, worker_id, existing_execution, mission,
            )

        # Fresh execution: create execution record
        execution_id = str(uuid4())
        idempotency_key = f"exec:{mission_id}:{execution_id}"
        create_result = self._execution_service.create_execution(
            mission_id=mission_id,
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            owner_id=worker_id,
            status="PENDING",
        )
        if not create_result.get("success"):
            return create_result

        execution = create_result["execution"]

        # Create plan from goal
        goal = mission.get("title") or mission.get("description") or ""
        plan_result = self._planning_engine.create_plan_from_goal(goal, mission_id)
        if not plan_result.get("success"):
            return plan_result

        plan_steps = plan_result.get("steps", [])

        # Persist plan steps to mission_steps table
        persist_result = self._persist_plan_steps(
            execution_id, worker_id, plan_steps,
        )
        if not persist_result.get("success"):
            return persist_result

        # Mark execution as RUNNING
        running_result = self._execution_service.update_execution_state(
            execution_id, worker_id,
            status="RUNNING",
            current_step_index=0,
            started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        )
        if not running_result.get("success"):
            return running_result

        execution = running_result["execution"]
        return self._execute_next_step(mission_id, worker_id, execution, plan_steps, mission)

    def resume_mission(self, mission_id: str) -> dict[str, Any]:
        """Resume a mission from persistent state after restart.

        Loads execution from PostgreSQL and continues from where
        it left off. Handles ambiguous states safely.

        Args:
            mission_id: The mission identifier to resume.

        Returns:
            Dictionary with execution results.
        """
        worker_id = self._owner_id or str(uuid4())

        # Load existing execution from DB
        execution = self._execution_service.load_execution_for_mission(
            mission_id, worker_id,
        )
        if not execution:
            # No active execution — start fresh
            return self.run_mission(mission_id)

        mission = self._mission_engine.get_mission(mission_id)
        if not mission:
            return {"success": False, "error": "Mission not found"}

        return self._resume_mission(mission_id, worker_id, execution, mission)

    # ------------------------------------------------------------------
    # Resume from persistent state
    # ------------------------------------------------------------------

    def _resume_mission(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        """Resume execution from a persisted execution record.

        Handles ambiguous states:
        - If execution was RUNNING when process died: do NOT assume success.
          Check the last step status and continue safely.
        - If execution was WAITING_APPROVAL: wait for human input.
        - If execution was WAITING_INPUT: wait for missing input.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: Persisted execution record from DB.
            mission: Mission record.

        Returns:
            Dictionary with execution results.
        """
        status = execution["status"]

        if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            # Terminal state — nothing to resume
            return {
                "success": True,
                "mission_id": mission_id,
                "worker_id": worker_id,
                "status": status,
                "message": f"Execution already in terminal state: {status}",
                "execution": execution,
            }

        if status == "WAITING_APPROVAL":
            # Execution paused for approval — wait for human
            return {
                "success": False,
                "pending_approval": True,
                "mission_id": mission_id,
                "execution_id": execution["execution_id"],
                "status": "WAITING_APPROVAL",
                "message": "Execution paused for approval",
                "execution": execution,
            }

        if status == "WAITING_INPUT":
            # Execution paused for missing input
            return {
                "success": False,
                "waiting_input": True,
                "mission_id": mission_id,
                "execution_id": execution["execution_id"],
                "status": "WAITING_INPUT",
                "message": "Execution paused waiting for human input",
                "execution": execution,
            }

        if status == "RUNNING":
            # Process died while running — do NOT assume success.
            # Check the last step and continue safely.
            return self._handle_ambiguous_running(
                mission_id, worker_id, execution, mission,
            )

        # Default: PENDING or unknown — start from current_step_index
        goal = mission.get("title") or mission.get("description") or ""
        plan_result = self._planning_engine.create_plan_from_goal(goal, mission_id)
        if not plan_result.get("success"):
            return plan_result

        plan_steps = plan_result.get("steps", [])
        return self._execute_next_step(
            mission_id, worker_id, execution, plan_steps, mission,
        )

    def _handle_ambiguous_running(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle ambiguous RUNNING state after process death.

        Do NOT assume success. Check the last step's status and
        continue safely.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: Persisted execution record.
            mission: Mission record.

        Returns:
            Dictionary with execution results.
        """
        current_step_index = execution.get("current_step_index", 0)
        execution_id = execution["execution_id"]

        # Load steps for this execution
        steps = self._execution_service.load_steps_for_execution(
            execution_id, worker_id,
        )

        if not steps:
            # No steps persisted — restart from beginning
            goal = mission.get("title") or mission.get("description") or ""
            plan_result = self._planning_engine.create_plan_from_goal(goal, mission_id)
            if not plan_result.get("success"):
                return plan_result
            plan_steps = plan_result.get("steps", [])
            return self._execute_next_step(
                mission_id, worker_id, execution, plan_steps, mission,
            )

        # Check the last step's status
        last_step = steps[-1]
        last_status = last_step.get("status")

        if last_status == "completed":
            # Last step completed safely — continue to next step
            next_index = current_step_index + 1
            return self._execute_next_step(
                mission_id, worker_id, execution,
                self._load_plan_steps(execution_id, worker_id),
                mission,
            )

        if last_status == "in_progress":
            # Step was in_progress when process died — retry safely
            # with a new attempt_index
            return self._retry_step(
                mission_id, worker_id, execution, last_step,
            )

        if last_status == "failed":
            # Step failed — check retry classification
            retry_category = last_step.get("retry_category", "TRANSIENT")
            if retry_category in RETRYABLE_CATEGORIES:
                return self._retry_step(
                    mission_id, worker_id, execution, last_step,
                )
            return self._handle_step_failure(
                mission_id, worker_id, execution, last_step,
            )

        # Default: continue to next step
        return self._execute_next_step(
            mission_id, worker_id, execution,
            self._load_plan_steps(execution_id, worker_id),
            mission,
        )

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def _execute_next_step(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        plan_steps: list[dict[str, Any]],
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the next executable step.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: Persisted execution record.
            plan_steps: Plan steps from the plan.
            mission: Mission record.

        Returns:
            Dictionary with execution results.
        """
        next_step = self._select_next_executable_step(plan_steps)
        if not next_step:
            return self._handle_mission_completion(
                mission_id, worker_id, execution, plan_steps, mission,
            )

        execution_result = self._execute_step(
            mission_id, worker_id, next_step, execution, plan_steps,
        )

        # Check if execution needs to pause
        if execution_result.get("pending_approval") or execution_result.get("awaiting_human_intervention"):
            return execution_result

        if execution_result.get("waiting_input"):
            return execution_result

        # Update execution state in DB
        current_step_index = execution.get("current_step_index", 0) + 1
        update_result = self._execution_service.update_execution_state(
            execution["execution_id"], worker_id,
            current_step_index=current_step_index,
            retry_count=execution.get("retry_count", 0) + (1 if execution_result.get("retryable") else 0),
        )

        if not update_result.get("success"):
            return update_result

        # Check completion
        completion_result = self._check_completion_criteria(
            mission_id, worker_id, plan_steps, execution,
        )
        if completion_result.get("completed"):
            return completion_result

        return {
            "success": True,
            "mission_id": mission_id,
            "worker_id": worker_id,
            "execution_id": execution["execution_id"],
            "current_step": next_step,
            "execution_result": execution_result,
            "progress": self._calculate_progress(plan_steps),
            "message": f"Step {next_step.get('step_name')} executed",
        }

    def _execute_step(
        self,
        mission_id: str,
        worker_id: str,
        step: dict[str, Any],
        execution: dict[str, Any],
        plan_steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute a single plan step through the durable workflow.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            step: The plan step to execute.
            execution: The persisted execution record.
            plan_steps: All plan steps.

        Returns:
            Execution result dictionary.
        """
        action_type = step.get("action_type")
        payload = step.get("action_payload", {})
        execution_id = execution["execution_id"]
        attempt_index = step.get("attempt_index", 0)
        idempotency_key = f"step:{execution_id}:{step.get('step_name')}:{attempt_index}"

        # Add worker_id to payload for memory tracking
        payload["worker_id"] = worker_id

        # Claim the step for execution (idempotency protection)
        claim_result = self._execution_service.claim_step(
            execution_id=execution_id,
            owner_id=worker_id,
            step_id=str(uuid4()),
            attempt_index=attempt_index,
            idempotency_key=idempotency_key,
        )
        if not claim_result.get("success"):
            return claim_result

        # Validate and execute through tool safety layer
        try:
            safe_result = self._safe_executor.execute(
                action_type=action_type,
                payload=payload,
                mission_id=mission_id,
            )
        except ToolSafetyError as e:
            return self._classify_and_persist_failure(
                mission_id, worker_id, execution, step,
                str(e), e.reason,
            )

        # Handle approval-required result
        if safe_result.get("pending_approval"):
            return self._persist_waiting_approval(
                mission_id, worker_id, execution, step, safe_result,
            )

        # Handle human intervention result
        if safe_result.get("awaiting_human_intervention"):
            return self._persist_waiting_human(
                mission_id, worker_id, execution, step, safe_result,
            )

        # Handle waiting input result
        if safe_result.get("waiting_input"):
            return self._persist_waiting_input(
                mission_id, worker_id, execution, step, safe_result,
            )

        # Check for success/failure
        if safe_result.get("success"):
            return self._persist_step_success(
                mission_id, worker_id, execution, step, safe_result,
            )

        # Execution failure — classify and persist
        error_msg = safe_result.get("error", "Execution failed")
        return self._classify_and_persist_failure(
            mission_id, worker_id, execution, step,
            error_msg,
            self._classify_error(error_msg, action_type),
        )

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def _classify_and_persist_failure(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        step: dict[str, Any],
        error_message: str,
        retry_category: str,
    ) -> dict[str, Any]:
        """Classify failure and persist the step result.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: The persisted execution record.
            step: The failed step.
            error_message: The error message.
            retry_category: The classified retry category.

        Returns:
            Dictionary with failure result.
        """
        execution_id = execution["execution_id"]
        step_name = step.get("step_name")
        attempt_index = step.get("attempt_index", 0)
        idempotency_key = f"step:{execution_id}:{step_name}:{attempt_index}"

        # Persist failure result to mission_steps
        persist_result = self._execution_service.persist_step(
            execution_id=execution_id,
            owner_id=worker_id,
            step_name=step_name,
            attempt_index=attempt_index,
            idempotency_key=idempotency_key,
            status="failed",
            result={"error": error_message},
            retry_category=retry_category,
        )
        if not persist_result.get("success"):
            return persist_result

        # Check retry policy
        if retry_category in RETRYABLE_CATEGORIES:
            if self._retry_policy.can_retry_step(attempt_index):
                return self._retry_step(
                    mission_id, worker_id, execution, step,
                )

        if retry_category in HUMAN_WAIT_CATEGORIES:
            if retry_category == "APPROVAL":
                return self._persist_waiting_approval(
                    mission_id, worker_id, execution, step,
                    {"pending_approval": True, "reason": error_message},
                )
            if retry_category == "MISSING_INPUT":
                return self._persist_waiting_input(
                    mission_id, worker_id, execution, step,
                    {"waiting_input": True, "reason": error_message},
                )

        # Non-retryable or max retries exceeded
        return self._handle_step_failure(
            mission_id, worker_id, execution, step,
        )

    def _retry_step(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        step: dict[str, Any],
    ) -> dict[str, Any]:
        """Retry a failed step with a new attempt index.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: The persisted execution record.
            step: The failed step.

        Returns:
            Dictionary with retry result.
        """
        execution_id = execution["execution_id"]
        step_name = step.get("step_name")
        attempt_index = step.get("attempt_index", 0) + 1
        idempotency_key = f"step:{execution_id}:{step_name}:{attempt_index}"

        # Update execution retry count
        update_result = self._execution_service.update_execution_state(
            execution_id, worker_id,
            retry_count=execution.get("retry_count", 0) + 1,
        )
        if not update_result.get("success"):
            return update_result

        # Create new step attempt
        persist_result = self._execution_service.persist_step(
            execution_id=execution_id,
            owner_id=worker_id,
            step_name=step_name,
            attempt_index=attempt_index,
            idempotency_key=idempotency_key,
            status="in_progress",
        )
        if not persist_result.get("success"):
            return persist_result

        # Re-execute the step
        step["attempt_index"] = attempt_index
        return self._execute_step(
            mission_id, worker_id, step, execution,
            self._load_plan_steps(execution_id, worker_id),
        )

    def _handle_step_failure(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        step: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle step failure that cannot be retried.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: The persisted execution record.
            step: The failed step.

        Returns:
            Dictionary with failure result.
        """
        execution_id = execution["execution_id"]
        step_name = step.get("step_name")
        retry_category = step.get("retry_category", "PERMANENT")

        if retry_category in NON_RETRYABLE_CATEGORIES or not self._retry_policy.can_retry_step(step.get("attempt_index", 0)):
            # Mark execution as failed
            fail_result = self._execution_service.mark_failed(
                execution_id, worker_id,
                error=f"Step {step_name} failed permanently",
                result={"failed_step": step_name, "retry_category": retry_category},
            )
            return {
                "success": False,
                "mission_id": mission_id,
                "execution_id": execution_id,
                "status": "FAILED",
                "error": f"Step {step_name} failed: {retry_category}",
                "execution": fail_result.get("execution"),
            }

        # Check execution-level retry bounds
        if not self._retry_policy.can_retry_execution(execution.get("retry_count", 0)):
            fail_result = self._execution_service.mark_failed(
                execution_id, worker_id,
                error="Max execution retries exceeded",
            )
            return {
                "success": False,
                "mission_id": mission_id,
                "execution_id": execution_id,
                "status": "FAILED",
                "error": "Max execution retries exceeded",
                "execution": fail_result.get("execution"),
            }

        return self._retry_step(mission_id, worker_id, execution, step)

    # ------------------------------------------------------------------
    # Approval handling
    # ------------------------------------------------------------------

    def _persist_waiting_approval(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        step: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist WAITING_APPROVAL state and return pending approval.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: The persisted execution record.
            step: The step requiring approval.
            result: Approval result from ActionEngine/WorkerRuntime.

        Returns:
            Dictionary with pending approval status.
        """
        execution_id = execution["execution_id"]
        step_name = step.get("step_name")

        # Persist step as waiting_approval
        persist_result = self._execution_service.persist_step(
            execution_id=execution_id,
            owner_id=worker_id,
            step_name=step_name,
            attempt_index=step.get("attempt_index", 0),
            idempotency_key=f"step:{execution_id}:{step_name}:{step.get('attempt_index', 0)}",
            status="pending",
            result=result,
        )
        if not persist_result.get("success"):
            return persist_result

        # Update execution state to WAITING_APPROVAL
        update_result = self._execution_service.update_execution_state(
            execution_id, worker_id,
            status="WAITING_APPROVAL",
            current_step_index=execution.get("current_step_index", 0),
        )
        if not update_result.get("success"):
            return update_result

        return {
            "success": False,
            "pending_approval": True,
            "mission_id": mission_id,
            "execution_id": execution_id,
            "status": "WAITING_APPROVAL",
            "action_type": result.get("action_type"),
            "approval_request_id": result.get("approval_request_id"),
            "message": result.get("message", "Approval required"),
            "execution": update_result.get("execution"),
        }

    def _persist_waiting_human(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        step: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist awaiting human intervention state.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: The persisted execution record.
            step: The step requiring human intervention.
            result: Human intervention result from connector.

        Returns:
            Dictionary with waiting human status.
        """
        execution_id = execution["execution_id"]
        step_name = step.get("step_name")

        persist_result = self._execution_service.persist_step(
            execution_id=execution_id,
            owner_id=worker_id,
            step_name=step_name,
            attempt_index=step.get("attempt_index", 0),
            idempotency_key=f"step:{execution_id}:{step_name}:{step.get('attempt_index', 0)}",
            status="pending",
            result=result,
        )
        if not persist_result.get("success"):
            return persist_result

        update_result = self._execution_service.update_execution_state(
            execution_id, worker_id,
            status="WAITING_INPUT",
            current_step_index=execution.get("current_step_index", 0),
        )
        if not update_result.get("success"):
            return update_result

        return {
            "success": False,
            "awaiting_human_intervention": True,
            "mission_id": mission_id,
            "execution_id": execution_id,
            "status": "WAITING_INPUT",
            "checkpoint_id": result.get("checkpoint_id"),
            "message": result.get("message", "Human intervention required"),
            "execution": update_result.get("execution"),
        }

    def _persist_waiting_input(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        step: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist waiting for missing input state.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: The persisted execution record.
            step: The step requiring input.
            result: Input requirement result.

        Returns:
            Dictionary with waiting input status.
        """
        execution_id = execution["execution_id"]
        step_name = step.get("step_name")

        persist_result = self._execution_service.persist_step(
            execution_id=execution_id,
            owner_id=worker_id,
            step_name=step_name,
            attempt_index=step.get("attempt_index", 0),
            idempotency_key=f"step:{execution_id}:{step_name}:{step.get('attempt_index', 0)}",
            status="pending",
            result=result,
        )

        update_result = self._execution_service.update_execution_state(
            execution_id, worker_id,
            status="WAITING_INPUT",
            current_step_index=execution.get("current_step_index", 0),
        )

        return {
            "success": False,
            "waiting_input": True,
            "mission_id": mission_id,
            "execution_id": execution_id,
            "status": "WAITING_INPUT",
            "message": result.get("reason", "Missing input required"),
            "execution": update_result.get("execution"),
        }

    def _persist_step_success(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        step: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist successful step execution.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: The persisted execution record.
            step: The successful step.
            result: Execution result.

        Returns:
            Dictionary with success status.
        """
        execution_id = execution["execution_id"]
        step_name = step.get("step_name")
        attempt_index = step.get("attempt_index", 0)
        idempotency_key = f"step:{execution_id}:{step_name}:{attempt_index}"

        persist_result = self._execution_service.persist_step(
            execution_id=execution_id,
            owner_id=worker_id,
            step_name=step_name,
            attempt_index=attempt_index,
            idempotency_key=idempotency_key,
            status="completed",
            result=result.get("result", {}),
        )

        if not persist_result.get("success"):
            return persist_result

        # Store memory observation
        self._observe_execution(mission_id, worker_id, execution_id, step, result)

        return {
            "success": True,
            "mission_id": mission_id,
            "action": step.get("action_type"),
            "result": result.get("result", {}),
        }

    def _observe_execution(
        self,
        mission_id: str,
        worker_id: str,
        execution_id: str,
        step: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Store memory observation from execution.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution_id: The execution identifier.
            step: The executed step.
            result: The execution result.
        """
        try:
            result_data = result.get("result", {})
            if result_data:
                self._memory_engine.store_memory(
                    worker_id,
                    "action",
                    {
                        "mission_id": mission_id,
                        "execution_id": execution_id,
                        "step_name": step.get("step_name"),
                        "action_type": step.get("action_type"),
                        "result": result_data,
                    },
                    owner_id=self._owner_id,
                )
        except Exception:  # pragma: no cover - defensive runtime handling
            pass

    # ------------------------------------------------------------------
    # Approval resume (called after human approves)
    # ------------------------------------------------------------------

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
            Dictionary with execution results.
        """
        # Verify approval status
        if not self._approval_gateway.is_approved(approval_request_id):
            return {
                "success": False,
                "error": "Approval not granted",
                "approval_request_id": approval_request_id,
            }

        # Load execution from DB
        execution = self._execution_service.get_execution(execution_id, worker_id)
        if not execution:
            return {"success": False, "error": "Execution not found"}

        # Verify execution was in WAITING_APPROVAL
        if execution["status"] != "WAITING_APPROVAL":
            return {
                "success": False,
                "error": f"Execution not in WAITING_APPROVAL state: {execution['status']}",
            }

        # Update execution state to RUNNING
        update_result = self._execution_service.update_execution_state(
            execution_id, worker_id,
            status="RUNNING",
        )
        if not update_result.get("success"):
            return update_result

        execution = update_result["execution"]

        # Get the plan steps
        plan_steps = self._load_plan_steps(execution_id, worker_id)
        mission = self._mission_engine.get_mission(mission_id)

        # Continue executing from current step
        return self._execute_next_step(
            mission_id, worker_id, execution, plan_steps, mission or {},
        )

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def _select_next_executable_step(
        self,
        plan_steps: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Select the next executable step from plan steps.

        Returns the first step with 'pending' or 'failed' status.
        """
        for step in plan_steps:
            status = step.get("status", "pending")
            if status in ("pending", "failed"):
                return step
        return None

    def _persist_plan_steps(
        self,
        execution_id: str,
        owner_id: str,
        plan_steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist plan steps to mission_steps table.

        Args:
            execution_id: The execution identifier.
            owner_id: The owner identifier.
            plan_steps: List of plan steps to persist.

        Returns:
            Dictionary with success flag.
        """
        for i, step in enumerate(plan_steps):
            step_name = step.get("step_name", f"step_{i}")
            step_id = str(uuid4())
            idempotency_key = f"plan:{execution_id}:{step_name}"

            persist_result = self._execution_service.persist_step(
                execution_id=execution_id,
                owner_id=owner_id,
                step_name=step_name,
                step_id=step_id,
                attempt_index=0,
                idempotency_key=idempotency_key,
                status="pending",
                result={},
            )
            if not persist_result.get("success"):
                return persist_result
        return {"success": True}

    def _load_plan_steps(
        self,
        execution_id: str,
        owner_id: str,
    ) -> list[dict[str, Any]]:
        """Load plan steps from mission_steps table.

        Args:
            execution_id: The execution identifier.
            owner_id: The owner identifier.

        Returns:
            List of step dictionaries.
        """
        return self._execution_service.load_steps_for_execution(
            execution_id, owner_id,
        )

    def _handle_mission_completion(
        self,
        mission_id: str,
        worker_id: str,
        execution: dict[str, Any],
        plan_steps: list[dict[str, Any]],
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle mission completion.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            execution: The persisted execution record.
            plan_steps: All plan steps.
            mission: Mission record.

        Returns:
            Dictionary with completion result.
        """
        all_completed = all(
            step.get("status") == "completed" for step in plan_steps
        )

        if all_completed:
            complete_result = self._execution_service.mark_completed(
                execution["execution_id"], worker_id,
                result={"plan": plan_steps, "mission_id": mission_id},
            )
            if not complete_result.get("success"):
                return complete_result
            # Also mark mission as completed
            mission_result = self._mission_engine.complete_mission(mission_id, {"plan": plan_steps})
            if not mission_result.get("success"):
                return mission_result
            return {
                "success": True,
                "mission_id": mission_id,
                "worker_id": worker_id,
                "completed": True,
                "status": "SUCCEEDED",
                "execution_id": execution["execution_id"],
                "message": "Mission completed successfully",
                "execution": complete_result.get("execution"),
            }

        fail_result = self._execution_service.mark_failed(
            execution["execution_id"], worker_id,
            error="No executable steps remaining but not all steps completed",
        )
        if not fail_result.get("success"):
            return fail_result
        return {
            "success": False,
            "mission_id": mission_id,
            "worker_id": worker_id,
            "completed": False,
            "status": "FAILED",
            "error": "No executable steps remaining but not all steps completed",
            "execution": fail_result.get("execution"),
        }

    def _check_completion_criteria(
        self,
        mission_id: str,
        worker_id: str,
        plan_steps: list[dict[str, Any]] | dict[str, Any],
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Check if mission completion criteria are met.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.
            plan_steps: Either a list of plan steps or a state dict containing "plan_steps".
            execution: Optional persisted execution record.

        Returns:
            Dictionary with completion status.
        """
        # Backward compatibility: accept state dict or plan_steps list
        if isinstance(plan_steps, dict):
            steps = plan_steps.get("plan_steps", [])
        else:
            steps = plan_steps

        if not steps:
            return {"completed": False, "mission_id": mission_id,
                    "worker_id": worker_id, "plan_steps_count": len(steps),
                    "completed_steps_count": 0}

        all_completed = all(step.get("status") == "completed" for step in steps)
        return {
            "completed": all_completed,
            "mission_id": mission_id,
            "worker_id": worker_id,
            "plan_steps_count": len(steps),
            "completed_steps_count": sum(
                1 for step in steps if step.get("status") == "completed"
            ),
        }

    def _calculate_progress(
        self,
        plan_steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Calculate mission progress metrics."""
        total = len(plan_steps)
        if total == 0:
            return {"completed": 0, "total": 0, "percentage": 0.0}

        completed = sum(1 for step in plan_steps if step.get("status") == "completed")
        return {
            "completed": completed,
            "total": total,
            "percentage": round((completed / total) * 100, 1),
        }

    def _classify_error(
        self,
        error_message: str,
        action_type: str,
    ) -> str:
        """Classify an error into a retry category.

        Args:
            error_message: The error message.
            action_type: The action type that failed.

        Returns:
            Retry category string.
        """
        decision = classify_failure(error_message, context={"action_type": action_type})
        return decision.category

    def _load_mission_state(
        self,
        mission_id: str,
        worker_id: str,
    ) -> dict[str, Any]:
        """Load mission state from persistent storage.

        Args:
            mission_id: The mission identifier.
            worker_id: The worker identifier.

        Returns:
            Dictionary with mission state.
        """
        execution = self._execution_service.load_execution_for_mission(
            mission_id, worker_id,
        )
        steps = []
        if execution:
            steps = self._load_plan_steps(execution["execution_id"], worker_id)

        return {
            "mission_id": mission_id,
            "worker_id": worker_id,
            "plan_steps": steps,
            "plan_exists": len(steps) > 0,
            "execution": execution,
        }


class _WorkerRuntimeStub:
    """Stub for SafeActionExecutor to handle connector actions."""

    def __init__(
        self,
        connector_registry: ConnectorRegistry | None = None,
        human_intervention: HumanInterventionManager | None = None,
        client: Any | None = None,
    ) -> None:
        self._connector_registry = connector_registry
        self._human_intervention = human_intervention or HumanInterventionManager(client=client)

    def execute_connector_action(
        self,
        mission_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Stub for connector action execution."""
        if not self._connector_registry:
            return {"success": False, "error": "Connector registry not available"}
        platform = payload.get("platform")
        if not platform:
            return {"success": False, "error": "Platform must be specified"}
        connector = self._connector_registry.get(platform)
        if not connector:
            return {"success": False, "error": f"No connector for {platform}"}
        return {"success": False, "error": f"Connector action {action_type} not implemented in stub"}

    def execute_action_with_approval(
        self,
        mission_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Stub for approval-required action execution."""
        return {
            "success": False,
            "pending_approval": True,
            "action_type": action_type,
            "message": "Approval required",
        }

    def continue_with_approval(
        self,
        mission_id: str,
        approval_request_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Stub for continuing after approval."""
        return {"success": True, "action": action_type, "message": "Approved and executed"}


__all__ = ["EmployeeEngine"]