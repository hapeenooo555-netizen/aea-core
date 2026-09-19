"""P1-7B objective-to-report employee vertical slice.

This module composes the existing P1-4/P1-6/P1-7A services. It owns no
alternative persistence or authorization model: execution state goes through
MissionExecutionService and external side effects go through ApprovalGateway.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .approval_gateway import ApprovalGateway
from .approval_resume_service import ApprovalResumeService
from .capability_discovery import CapabilityDiscovery
from .connectors.pinterest_connector import PinterestConnector
from .connectors.registry import ConnectorRegistry
from .memory_engine import AtlasMemoryEngine
from .mission_execution_service import MissionExecutionService
from .mission_engine import MissionEngine
from .p1_7_contracts import (
    BoundedDecisionLoop,
    ObjectiveParser,
    Observation,
    ToolContract,
    sanitize_payload,
)
from .plan_validator import PlanValidator
from .retry_classifier import classify_failure
from .stores.platform_connection_store import PlatformConnectionStore
from .stores.onboarding_workflow_store import OnboardingWorkflowStore
from .tool_registry import ToolRegistry
from .tool_safety import ToolSafetyError, ToolValidator
from .action_engine import ActionEngine


class EmployeeVerticalSlice:
    """Run one bounded, user-scoped employee objective."""

    def __init__(
        self,
        owner_id: str,
        *,
        connector_registry: ConnectorRegistry | None = None,
        client: Any | None = None,
        connection_store: PlatformConnectionStore | None = None,
        execution_service: MissionExecutionService | None = None,
        approval_gateway: ApprovalGateway | None = None,
        memory_engine: AtlasMemoryEngine | None = None,
        max_decisions: int = 3,
    ) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._claim_token = str(uuid4())
        self._client = client
        self._connectors = connector_registry or ConnectorRegistry()
        if not self._connectors.has_connector("pinterest"):
            self._connectors.register(PinterestConnector(
                workflow_store=OnboardingWorkflowStore(
                    client=client,
                    durable_required=True,
                ),
                connection_store=PlatformConnectionStore(client=client),
            ))
        self._connections = connection_store or PlatformConnectionStore(client=client)
        self._execution = execution_service or MissionExecutionService(client=client, durable_required=True)
        self._mission_engine = MissionEngine(client=client)
        self._approvals = approval_gateway or ApprovalGateway(client=client)
        self._memory = memory_engine or AtlasMemoryEngine(client=client, owner_id=owner_id)
        self._tools = ToolRegistry()
        self._register_tools()
        self._discovery = CapabilityDiscovery(self._tools, self._connectors)
        self._validator = PlanValidator(self._tools, self._discovery)
        self._tool_validator = ToolValidator(self._tools, ActionEngine(owner_id=owner_id))
        self._max_decisions = max_decisions

    def _register_tools(self) -> None:
        self._tools.register_contract(ToolContract(
            tool_name="log",
            description="Record a non-sensitive employee outcome",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            risk_level="READ_ONLY",
        ))
        self._tools.register_contract(ToolContract(
            tool_name="pinterest.get_account_status",
            description="Read the authenticated user's Pinterest connection status",
            input_schema={
                "type": "object",
                "properties": {"platform": {"type": "string"}},
                "required": ["platform"],
                "additionalProperties": False,
            },
            capability="account_status",
            platform="pinterest",
            operation="get_account_status",
            risk_level="READ_ONLY",
        ))
        self._tools.register_contract(ToolContract(
            tool_name="start_platform_onboarding",
            description="Start Pinterest onboarding after user approval",
            input_schema={
                "type": "object",
                "properties": {"platform": {"type": "string"}},
                "required": ["platform"],
                "additionalProperties": False,
            },
            capability="onboarding",
            platform="pinterest",
            operation="start_onboarding",
            risk_level="WRITE_EXTERNAL",
            requires_approval=True,
            idempotency_behavior="approval_and_workflow_idempotent",
        ))

    def discover_capabilities(self) -> dict[str, Any]:
        """Return only tools available to this owner and their connections."""
        return self._discovery.discover(
            self.owner_id,
            lambda owner, platform: self._connections.get(owner, platform),
        )

    def run(
        self,
        goal: str,
        *,
        mission_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Understand, plan, validate, execute, observe, and report."""
        mission_result = self._resolve_mission(mission_id, goal)
        if not mission_result.get("success"):
            return {"success": False, "status": "FAIL", "error": mission_result.get("error", "Mission unavailable")}
        mission_id = mission_result["mission"]["id"]
        objective = ObjectiveParser().parse(goal, metadata)
        discovered = self.discover_capabilities()
        plan = self._build_plan(objective, discovered)
        validation = self._validator.validate(
            plan,
            owner_id=self.owner_id,
            discovered=discovered,
        )
        report: dict[str, Any] = {
            "user_goal": objective.goal,
            "understood_objective": objective.to_dict(),
            "discovered_capabilities": discovered,
            "plan": plan,
            "selected_tools": [step.get("tool_name") for step in plan],
            "approvals": [],
            "executed_actions": [],
            "results": [],
            "observations": [],
            "failures_retries": [],
            "learning": [],
            "required_user_action": None,
            "resume_information": None,
        }
        if not validation.get("success"):
            report.update({"final_status": "FAIL", "failures_retries": validation["errors"]})
            return {"success": False, "status": "FAIL", "report": sanitize_payload(report)}

        existing_execution = self._execution.load_execution_for_mission(mission_id, self.owner_id)
        if existing_execution and existing_execution.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return self._resume_execution(mission_id, existing_execution, report, plan)

        execution_id = str(uuid4())
        claim = self._execution.claim_execution(
            mission_id, execution_id, f"p1-7b:{self.owner_id}:{mission_id}", self.owner_id,
        )
        if not claim.get("success"):
            return self._failed_report(report, claim.get("error", "Execution claim failed"))
        execution = claim["execution"]
        execution_id = execution["execution_id"]
        report["execution_id"] = execution_id
        running = self._execution.update_execution_state(execution_id, self.owner_id, status="RUNNING")
        if not running.get("success"):
            return self._failed_report(report, running.get("error", "Execution start persistence failed"))
        return self._execute_plan(mission_id, execution_id, plan, report, metadata)

    def _execute_plan(
        self,
        mission_id: str,
        execution_id: str,
        plan: list[dict[str, Any]],
        report: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        completed_steps: set[str] = set()
        persisted_steps = self._execution.load_steps_for_execution(execution_id, self.owner_id)
        for step in persisted_steps:
            status = step.get("status")
            if status == "completed":
                completed_steps.add(step.get("step_name") or "")

        observation_index = 0
        for index, step in enumerate(plan):
            step_name = step.get("step_name", f"p1_7b_step_{index + 1}")
            if step_name in completed_steps:
                continue
            attempt_index = self._next_attempt_index(execution_id, step_name)
            operation_key = self._operation_key(mission_id, execution_id, step_name)
            claim = self._execution.claim_step(
                execution_id,
                self.owner_id,
                step_id=str(uuid4()),
                attempt_index=attempt_index,
                idempotency_key=f"p1-7b:{execution_id}:{step_name}:{attempt_index}",
                step_name=step_name,
                operation_key=operation_key,
                claim_token=self._claim_token,
            )
            if not claim.get("success"):
                return self._failed_report(report, claim.get("error", "Step claim failed"))
            claimed_step = claim["step"]
            if not claim.get("claimed") and claimed_step.get("status") == "completed":
                completed_steps.add(step_name)
                continue
            if not claim.get("claimed"):
                return {
                    "success": False,
                    "status": "RETRY",
                    "error": "Step is already claimed by another worker",
                    "report": sanitize_payload(report),
                }
            claimed_step_name = self._execution.update_step(
                execution_id,
                self.owner_id,
                claimed_step["id"],
                step_name=step_name,
                status="in_progress",
                claim_token=self._claim_token,
            )
            if not claimed_step_name.get("success"):
                return self._failed_report(report, claimed_step_name.get("error", "Step claim state update failed"))
            result = self._execute_tool(step, mission_id, execution_id, operation_key)

            observation = Observation(
                action=step["tool_name"],
                result=result,
                success=bool(result.get("success")),
                state_change="completed" if result.get("success") else "paused_or_failed",
                new_information=[str(result.get("status"))] if result.get("status") else [],
                next_possible_actions=["COMPLETE", "FOLLOW_UP", "WAIT_FOR_APPROVAL", "WAIT_FOR_HUMAN_INPUT", "RETRY", "FAIL"],
            )
            report["executed_actions"].append(sanitize_payload(step))
            report["results"].append(sanitize_payload(result))
            report["approvals"] = self._approval_summary(result)
            report["observations"].append(observation.to_dict())
            if "observation" not in report:
                report["observation"] = observation.to_dict()
            report[f"observation_{observation_index}"] = observation.to_dict()
            observation_index += 1

            decision = self._next_decision(result, step_name, plan, index)
            report["decision"] = decision
            next_step = self._next_step_name(decision, plan, index)
            report["learning"] = [
                "Pinterest account status was checked without persisting credentials"
            ] if step["tool_name"] == "pinterest.get_account_status" else []

            if result.get("success") and step.get("tool_name") in {"pinterest.get_account_status"}:
                status = result.get("status")
                if status in {"not_started", "needs_reconnect", "failed", "onboarding"}:
                    report["next_step_required"] = True

            step_status = "completed" if result.get("success") else "failed"
            retry_category = None if result.get("success") else classify_failure(result.get("error", "execution failure")).category
            step_update = self._execution.update_step(
                execution_id,
                self.owner_id,
                claimed_step["id"],
                status=step_status,
                result=sanitize_payload({"result": result, "observation": observation.to_dict()}),
                retry_category=retry_category,
                claim_token=self._claim_token,
            )
            if not step_update.get("success"):
                return self._failed_report(report, step_update.get("error", "Step update failed"))
            state_update = self._execution.update_execution_state(
                execution_id,
                self.owner_id,
                current_step_index=index + 1,
                result=sanitize_payload({
                    "observation": observation.to_dict(),
                    "decision": decision,
                    "next_step": next_step,
                    "step_name": step_name,
                    "attempt_index": attempt_index,
                }),
            )
            if not state_update.get("success"):
                return self._failed_report(report, state_update.get("error", "Execution state update failed"))

            if decision == "COMPLETE":
                completion = self._execution.mark_completed(execution_id, self.owner_id, {"report": sanitize_payload(report)})
                if not completion.get("success"):
                    return self._failed_report(report, completion.get("error", "Execution completion failed"))
                report["final_status"] = "COMPLETE"
                return {"success": True, "status": "COMPLETE", "report": sanitize_payload(report)}
            if decision == "WAIT_FOR_APPROVAL":
                approval_state = dict(state_update.get("execution", {}).get("result") or {})
                approval_state["approval_request_id"] = result.get("approval_request_id")
                approval_update = self._execution.update_execution_state(
                    execution_id,
                    self.owner_id,
                    status="WAITING_APPROVAL",
                    result=sanitize_payload(approval_state),
                )
                if not approval_update.get("success"):
                    return self._failed_report(report, approval_update.get("error", "Approval state persistence failed"))
                report["required_user_action"] = "Approve the pending action"
                report["resume_information"] = {"approval_request_id": result.get("approval_request_id"), "execution_id": execution_id}
                report["final_status"] = "WAIT_FOR_APPROVAL"
                return {"success": False, "status": "WAIT_FOR_APPROVAL", "report": sanitize_payload(report)}
            if decision == "WAIT_FOR_HUMAN_INPUT":
                input_update = self._execution.update_execution_state(
                    execution_id,
                    self.owner_id,
                    status="WAITING_INPUT",
                    result=sanitize_payload(state_update.get("execution", {}).get("result") or {}),
                )
                if not input_update.get("success"):
                    return self._failed_report(report, input_update.get("error", "Input state persistence failed"))
                report["required_user_action"] = result.get("message", "Complete the requested human step")
                report["final_status"] = "WAIT_FOR_HUMAN_INPUT"
                return {"success": False, "status": "WAIT_FOR_HUMAN_INPUT", "report": sanitize_payload(report)}
            if decision == "FOLLOW_UP":
                continue
            if decision == "RETRY":
                retry_decision = classify_failure(result.get("error", "execution failure"))
                if retry_decision.retryable and attempt_index < self._max_decisions:
                    retry_update = self._execution.update_execution_state(execution_id, self.owner_id, status="RETRYING", retry_count=attempt_index + 1, result=sanitize_payload({"decision": decision, "next_step": step_name, "result": result}))
                    if not retry_update.get("success"):
                        return self._failed_report(report, retry_update.get("error", "Retry state persistence failed"))
                    report["final_status"] = "RETRY"
                    return {"success": False, "status": "RETRY", "report": sanitize_payload(report)}
            failure = self._execution.mark_failed(execution_id, self.owner_id, result=sanitize_payload(result), error=result.get("error", "Mission failed"))
            if not failure.get("success"):
                return self._failed_report(report, failure.get("error", "Execution failure persistence failed"))
            report["final_status"] = "FAIL"
            return {"success": False, "status": "FAIL", "report": sanitize_payload(report)}

        completion = self._execution.mark_completed(execution_id, self.owner_id, {"report": sanitize_payload(report)})
        if not completion.get("success"):
            return self._failed_report(report, completion.get("error", "Execution completion failed"))
        report["final_status"] = "COMPLETE"
        return {"success": True, "status": "COMPLETE", "report": sanitize_payload(report)}

    def _resume_execution(
        self,
        mission_id: str,
        execution: dict[str, Any],
        report: dict[str, Any],
        plan: list[dict[str, Any]],
    ) -> dict[str, Any]:
        execution_id = execution["execution_id"]
        report["execution_id"] = execution_id
        durable_result = execution.get("result") or {}
        completed = {
            step.get("step_name")
            for step in self._execution.load_steps_for_execution(execution_id, self.owner_id)
            if step.get("status") == "completed"
        }
        durable_next = durable_result.get("next_step")
        if durable_next and durable_next not in completed:
            plan = [step for step in plan if step.get("step_name") == durable_next or step.get("step_name") not in completed]
        remaining = [step for step in plan if step.get("step_name") not in completed]
        if not remaining:
            completion = self._execution.mark_completed(execution_id, self.owner_id, {"report": sanitize_payload(report)})
            if not completion.get("success"):
                return self._failed_report(report, completion.get("error", "Execution completion failed"))
            report["final_status"] = "COMPLETE"
            return {"success": True, "status": "COMPLETE", "report": sanitize_payload(report)}
        running = self._execution.update_execution_state(execution_id, self.owner_id, status="RUNNING")
        if not running.get("success"):
            return self._failed_report(report, running.get("error", "Execution resume persistence failed"))
        return self._execute_plan(mission_id, execution_id, remaining, report, None)

    def resume_approval(self, approval_request_id: str) -> dict[str, Any]:
        """Resume an approved onboarding action through ApprovalResumeService."""
        request = self._approvals.get_request(approval_request_id)
        request_payload = request.get("payload") or {} if request else {}
        if not request or request_payload.get("worker_id") != self.owner_id:
            return {"success": False, "status": "FAIL", "error": "Approval request not found"}
        resume_service = ApprovalResumeService(
            approval_gateway=self._approvals,
            connector_registry=self._connectors,
        )
        result = resume_service.resume(approval_request_id, current_user_id=self.owner_id)
        safe_result = sanitize_payload(result)
        payload = request.get("payload") or {}
        execution_id = payload.get("execution_id")
        if execution_id:
            if result.get("status") == "approval_rejected":
                rejection = self._execution.mark_failed(
                    execution_id,
                    self.owner_id,
                    error="Approval rejected",
                    result={"decision": "REJECTED", "observation": safe_result},
                )
                if not rejection.get("success"):
                    return {"success": False, "status": "FAIL", "error": rejection.get("error", "Approval rejection persistence failed")}
                return {"success": False, "status": "approval_rejected", "result": safe_result}
            claim = self._execution.claim_step(
                execution_id,
                self.owner_id,
                step_id=str(uuid4()),
                attempt_index=0,
                idempotency_key=f"p1-7b:{execution_id}:start_onboarding:approval",
                step_name="start_onboarding",
                operation_key=payload.get("operation_key") or self._operation_key(
                    payload.get("mission_id") or "", execution_id, "start_onboarding"
                ),
                claim_token=self._claim_token,
            )
            if not claim.get("success"):
                return {"success": False, "status": "FAIL", "error": claim.get("error", "Approval step claim failed")}
            if not claim.get("claimed") and claim["step"].get("status") == "completed":
                return {"success": True, "status": "completed", "result": claim["step"].get("result", {})}
            step_result = self._execution.update_step(
                execution_id,
                self.owner_id,
                claim["step"]["id"],
                status="completed" if result.get("status") in {"completed", "resumed"} else "failed",
                result=safe_result,
                retry_category=None if result.get("status") in {"completed", "resumed"} else "EXECUTION",
                claim_token=self._claim_token,
            )
            if not step_result.get("success"):
                return {"success": False, "status": "FAIL", "error": step_result.get("error", "Approval result persistence failed")}
            if result.get("status") == "completed":
                completion = self._execution.mark_completed(execution_id, self.owner_id, {"approval_resume": safe_result})
                if not completion.get("success"):
                    return {"success": False, "status": "FAIL", "error": completion.get("error", "Approval completion persistence failed")}
            elif result.get("status") == "awaiting_human_intervention":
                waiting = self._execution.update_execution_state(
                    execution_id,
                    self.owner_id,
                    status="WAITING_INPUT",
                    result=safe_result,
                )
                if not waiting.get("success"):
                    return {"success": False, "status": "FAIL", "error": waiting.get("error", "Approval wait persistence failed")}
        return {"success": result.get("status") in {"completed", "resumed"}, "status": result.get("status"), "result": safe_result}

    def _resolve_mission(self, mission_id: str | None, goal: str) -> dict[str, Any]:
        """Resolve an owned durable mission before creating execution state."""
        if mission_id:
            mission = self._mission_engine.get_mission(mission_id, client=self._client)
            if mission is None:
                if getattr(self._execution, "_durable_required", False):
                    return {"success": False, "error": "Mission not found"}
                return {"success": True, "mission": {"id": mission_id, "owner_id": self.owner_id}}
            if mission.get("owner_id") != self.owner_id:
                return {"success": False, "error": "Mission not found"}
            return {"success": True, "mission": mission}

        if not getattr(self._execution, "_durable_required", False):
            return {"success": True, "mission": {"id": str(uuid4()), "owner_id": self.owner_id}}
        created = self._mission_engine.create_mission(goal, goal, owner_id=self.owner_id, client=self._client)
        if not created.get("success") or not created.get("mission"):
            return {"success": False, "error": created.get("error", "Mission creation failed")}
        return created

    def _next_attempt_index(self, execution_id: str, step_name: str) -> int:
        attempts = [
            step.get("attempt_index", 0)
            for step in self._execution.load_steps_for_execution(execution_id, self.owner_id)
            if step.get("step_name") == step_name
        ]
        return max(attempts, default=-1) + 1

    @staticmethod
    def _operation_key(mission_id: str, execution_id: str, step_name: str) -> str:
        """Bind one logical side effect to its server-owned execution graph."""
        return f"p1-7d:{mission_id}:{execution_id}:{step_name}"

    @staticmethod
    def _next_step_name(decision: str, plan: list[dict[str, Any]], index: int) -> str | None:
        if decision == "RETRY":
            return plan[index].get("step_name")
        if decision in {"WAIT_FOR_APPROVAL", "WAIT_FOR_HUMAN_INPUT", "FAIL", "COMPLETE"}:
            return None
        if index + 1 < len(plan):
            return plan[index + 1].get("step_name")
        return None

    def _build_plan(self, objective: Any, discovered: dict[str, Any]) -> list[dict[str, Any]]:
        goal = objective.goal.lower()
        required_payload = dict(getattr(objective, "content_inputs", {}) or {})
        platform_constraints = list(getattr(objective, "platform_constraints", []) or [])
        steps: list[dict[str, Any]] = []
        is_pinterest = "pinterest" in goal or "pinterest" in platform_constraints or required_payload.get("platform") == "pinterest"
        if is_pinterest or "board" in goal or "account" in goal:
            affiliate_meta = {
                k: v for k, v in required_payload.items()
                if k not in {"platform"}
            }
            status_payload = {"platform": "pinterest", **required_payload}
            steps.append({
                "step_name": "check_connection_status",
                "tool_name": "pinterest.get_account_status",
                "input": {"platform": "pinterest"},
                "action_type": "pinterest.get_account_status",
                "action_payload": status_payload,
                "metadata": affiliate_meta,
            })
            if any(word in goal for word in ("connect", "onboard", "authorize", "link", "enroll")):
                steps.append({
                    "step_name": "start_onboarding",
                    "tool_name": "start_platform_onboarding",
                    "input": {"platform": "pinterest"},
                    "action_type": "start_platform_onboarding",
                    "action_payload": {"platform": "pinterest", **required_payload},
                    "requires_approval": True,
                    "metadata": affiliate_meta,
                })
        if not steps:
            steps.append({
                "step_name": "record_goal",
                "tool_name": "log",
                "input": {"message": objective.goal},
                "action_type": "log",
                "action_payload": {"message": objective.goal, **required_payload},
                "metadata": required_payload if required_payload else None,
            })
        return steps

    def _execute_tool(
        self,
        step: dict[str, Any],
        mission_id: str,
        execution_id: str,
        operation_key: str | None = None,
    ) -> dict[str, Any]:
        tool_name = step["tool_name"]
        payload = dict(step.get("input") or {})
        if payload.get("force_error") == "transient":
            return {"success": False, "status": "retrying", "error": "connection timed out while checking Pinterest status"}
        if payload.get("force_error") == "missing_input":
            return {"success": False, "status": "missing_input", "error": "Missing required Pinterest board id"}
        try:
            prepared = self._tool_validator.validate_and_prepare(tool_name, payload)
        except ToolSafetyError as exc:
            return {"success": False, "error": str(exc), "reason": exc.reason}
        if tool_name == "pinterest.get_account_status":
            connector = self._connectors.get("pinterest")
            if connector is None:
                return {"success": False, "status": "UNAVAILABLE", "error": "Pinterest connector is unavailable"}
            return connector.get_account_status(self.owner_id)
        if tool_name == "start_platform_onboarding":
            request = self._approvals.create_request(
                mission_id=mission_id, action_type="start_platform_onboarding",
                risk_level="WRITE_EXTERNAL",
                payload={
                    **prepared["payload"],
                    "worker_id": self.owner_id,
                    "execution_id": execution_id,
                    "operation_key": operation_key or self._operation_key(mission_id, execution_id, "start_onboarding"),
                },
                owner_id=self.owner_id,
            )
            if not request.get("success"):
                return request
            return {"success": False, "pending_approval": True, "status": "WAITING_APPROVAL", "approval_request_id": request["request"].get("id"), "message": "Approval required before Pinterest onboarding"}
        return ActionEngine(owner_id=self.owner_id).execute_action(tool_name, prepared["payload"])

    def _next_decision(
        self,
        result: dict[str, Any],
        step_name: str,
        plan: list[dict[str, Any]],
        index: int,
    ) -> str:
        if result.get("pending_approval"):
            return "WAIT_FOR_APPROVAL"
        if result.get("awaiting_human_intervention") or result.get("waiting_input"):
            return "WAIT_FOR_HUMAN_INPUT"
        if result.get("success"):
            if result.get("status") in {"not_started", "needs_reconnect", "failed", "onboarding"} and index + 1 < len(plan):
                return "FOLLOW_UP"
            return "COMPLETE"
        decision = classify_failure(result.get("error", "execution failure"))
        if decision.category in {"MISSING_INPUT"}:
            return "WAIT_FOR_HUMAN_INPUT"
        if decision.category == "APPROVAL":
            return "WAIT_FOR_APPROVAL"
        if decision.retryable and self._max_decisions > 0:
            return "RETRY"
        return "FAIL"

    @staticmethod
    def _approval_summary(result: dict[str, Any]) -> list[dict[str, Any]]:
        if not result.get("approval_request_id"):
            return []
        return [{"approval_request_id": result["approval_request_id"], "status": result.get("status", "pending")}]

    @staticmethod
    def _failed_report(report: dict[str, Any], error: str) -> dict[str, Any]:
        report["final_status"] = "FAIL"
        report["failures_retries"] = [error]
        return {"success": False, "status": "FAIL", "report": sanitize_payload(report)}


__all__ = ["EmployeeVerticalSlice"]
