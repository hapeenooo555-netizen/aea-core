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
    ObjectiveParser,
    Observation,
    ToolContract,
    sanitize_payload,
)
from .plan_validator import PlanValidator
from .retry_classifier import classify_failure
from .stores.platform_connection_store import PlatformConnectionStore
from .stores.onboarding_workflow_store import OnboardingWorkflowStore
from .stores.pin_publish_store import PinPublishStore
from .studio_objective import (
    STUDIO_GENERATION_LEASE_SECONDS,
    execute_studio_step,
    interpret_studio_goal,
    register_studio_tools,
    studio_plan,
)
from app import database as database_module
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
            # Publish ledger writes must bypass client-controlled table DML.
            # The service-role client stays server-side and is only used after
            # this vertical slice has validated the authenticated owner and approval.
            pin_store = PinPublishStore(
                client=database_module.get_supabase_pin_publish_client(),
                durable_required=True,
            )
            self._connectors.register(PinterestConnector(
                workflow_store=OnboardingWorkflowStore(
                    client=client,
                    durable_required=True,
                ),
                connection_store=PlatformConnectionStore(client=client),
                pin_store=pin_store,
            ))
        self._connections = connection_store or PlatformConnectionStore(client=client)
        self._execution = execution_service or MissionExecutionService(client=client, durable_required=True)
        self._mission_engine = MissionEngine(client=client)
        self._approvals = approval_gateway or ApprovalGateway(client=client)
        self._memory = memory_engine or AtlasMemoryEngine(client=client, owner_id=owner_id)
        self._tools = ToolRegistry()
        self._register_tools()
        register_studio_tools(self._tools)
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
            tool_name="prepare_affiliate_content",
            description="Prepare a draft affiliate campaign and sample Pinterest content without publishing",
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "niche": {"type": "string"},
                    "markets": {"type": "string"},
                    "platform": {"type": "string"},
                },
                "required": ["goal"],
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
        self._tools.register_contract(ToolContract(
            tool_name="publish_content",
            description="Publish a Pinterest pin on behalf of the authenticated user",
            input_schema={
                "type": "object",
                "properties": {
                    "platform": {"type": "string"},
                    "board_name": {"type": "string"},
                    "pin_text": {"type": "string"},
                    "link_url": {"type": "string"},
                    "opportunity_id": {"type": "string"},
                    "image_url": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["board_name", "pin_text", "link_url"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "pin_id": {"type": "string"},
                    "status": {"type": "string"},
                    "operation_key": {"type": "string"},
                    "link_url": {"type": "string"},
                },
            },
            capability="publish_content",
            platform="pinterest",
            operation="publish_content",
            risk_level="WRITE_EXTERNAL",
            requires_connection=True,
            requires_approval=True,
            supports_dry_run=False,
            idempotency_behavior="approval_and_operation_idempotent",
            timeout=30.0,
            retry_policy="bounded",
        ))

    def discover_capabilities(self) -> dict[str, Any]:
        """Return only tools available to this owner and their connections."""
        return self._discovery.discover(
            self.owner_id,
            lambda owner, platform: self._connections.get(owner, platform),
        )

    @staticmethod
    def _has_complete_pinterest_pin_inputs(metadata: dict[str, Any] | None) -> bool:
        """Only allow a planned Pinterest publish step when its required content is present."""
        inputs = (metadata or {}).get("content_inputs") or {}
        return all(
            isinstance(inputs.get(key), str) and bool(inputs.get(key, "").strip())
            for key in ("board_name", "pin_text", "link_url", "image_url", "title")
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
        mission = mission_result["mission"]
        mission_id = mission["id"]
        objective = ObjectiveParser().parse(goal, metadata)
        intent = interpret_studio_goal(goal, objective.content_inputs, objective.locale)
        stored_goal = (mission.get("description") or "").strip()
        if (
            stored_goal and stored_goal != goal.strip()
            and (intent or interpret_studio_goal(stored_goal, {}, objective.locale))
        ):
            return {"success": False, "status": "FAIL", "error": "Objective differs from the saved mission."}
        discovered = self.discover_capabilities()
        raw_goal = " ".join(goal.lower().split())
        gated_pin = (
            "pinterest" in raw_goal
            and ("pin" in raw_goal or "publish" in raw_goal)
            and any(phrase in raw_goal for phrase in (
                "do not publish automatically",
                "don't publish automatically",
                "dont publish automatically",
                "wait for my explicit approval before publishing",
                "wait for approval before publishing",
            ))
        )
        if intent:
            plan = studio_plan(goal, intent) if intent.get("topic") else []
        else:
            plan = self._build_plan(objective, discovered)
            # Keep the existing draft-first handling of approval-gated Pin requests.
            if gated_pin and not self._has_complete_pinterest_pin_inputs(metadata):
                plan = self._pinterest_draft_plan(goal)
        validation = self._validator.validate(
            plan,
            owner_id=self.owner_id,
            discovered=discovered,
        )
        report: dict[str, Any] = {
            "mission_id": mission_id,
            "user_goal": goal,
            "original_user_goal": goal,
            "understood_objective": {**objective.to_dict(), **(intent or {})},
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
        if intent and intent["intent"] == "studio_compound":
            return self._failed_report(report, "The combined request requires a workflow that is currently unavailable.")
        if intent and not intent.get("topic"):
            report.update({
                "final_status": "WAIT_FOR_HUMAN_INPUT",
                "missing_inputs": ["topic"],
                "required_user_action": (
                    "Maudhui yawe kuhusu nini?" if intent["language"] == "Swahili"
                    else "What should the draft be about?"
                ),
            })
            return {"success": False, "status": "WAIT_FOR_HUMAN_INPUT", "report": sanitize_payload(report)}
        if not plan:
            return self._failed_report(report, "No available employee workflow matches this objective.")
        if not validation.get("success"):
            report.update({"final_status": "FAIL", "failures_retries": validation["errors"]})
            return {"success": False, "status": "FAIL", "report": sanitize_payload(report)}

        # Execute only the normalized plan returned by the side-effect-free
        # validator; never fall back to the untrusted/original plan.
        validated_plan = validation.get("steps") or []
        report["validated_plan"] = sanitize_payload(validated_plan)
        report["selected_tools"] = [step.get("tool_name") for step in validated_plan]

        existing_execution = self._execution.load_execution_for_mission(mission_id, self.owner_id)
        if existing_execution and existing_execution.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return self._resume_execution(mission_id, existing_execution, report, validated_plan)

        execution_id = str(uuid4())
        claim = self._execution.claim_execution(
            mission_id, execution_id, f"p1-7b:{self.owner_id}:{mission_id}", self.owner_id,
        )
        if not claim.get("success"):
            return self._failed_report(report, claim.get("error", "Execution claim failed"))
        execution = claim["execution"]
        execution_id = execution["execution_id"]
        report["execution_id"] = execution_id
        if not claim.get("created"):
            return self._resume_execution(mission_id, execution, report, validated_plan)
        running = self._execution.update_execution_state(execution_id, self.owner_id, status="RUNNING")
        if not running.get("success"):
            return self._failed_report(report, running.get("error", "Execution start persistence failed"))
        return self._execute_plan(mission_id, execution_id, validated_plan, report, metadata)

    def _execute_plan(
        self,
        mission_id: str,
        execution_id: str,
        plan: list[dict[str, Any]],
        report: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        completed_steps = {}
        for persisted_step in self._execution.load_steps_for_execution(execution_id, self.owner_id):
            if persisted_step.get("status") != "completed":
                continue
            saved = persisted_step.get("result") or {}
            completed_steps[persisted_step.get("step_name") or ""] = saved.get("result") or {}
            if saved.get("execution_evidence"):
                report.setdefault("execution_evidence", []).append(saved["execution_evidence"])

        observation_index = 0
        index = 0
        while index < len(plan):
            step = plan[index]
            step_name = step.get("step_name", f"p1_7b_step_{index + 1}")
            if step_name in completed_steps:
                index += 1
                continue
            dependencies = step.get("depends_on", [])
            if any(name not in completed_steps for name in dependencies):
                return self._failed_report(report, "A required execution checkpoint is unavailable")
            attempt_index = self._next_attempt_index(execution_id, step_name)
            lease = (
                {"lease_seconds": STUDIO_GENERATION_LEASE_SECONDS}
                if step["tool_name"] == "studio.generate_draft" else {}
            )
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
                **lease,
            )
            if not claim.get("success"):
                return self._failed_report(report, claim.get("error", "Step claim failed"))
            claimed_step = claim["step"]
            if not claim.get("claimed") and claimed_step.get("status") == "completed":
                saved = claimed_step.get("result") or {}
                completed_steps[step_name] = saved.get("result") or {}
                if saved.get("execution_evidence"):
                    report.setdefault("execution_evidence", []).append(saved["execution_evidence"])
                index += 1
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
            execution_step = {
                **step,
                "dependency_results": {name: completed_steps[name] for name in dependencies},
            } if dependencies else step
            result = self._execute_tool(execution_step, mission_id, execution_id, operation_key)
            public_result = {key: value for key, value in result.items() if key != "draft"}

            observation = Observation(
                action=step["tool_name"],
                result=public_result,
                success=bool(result.get("success")),
                state_change="completed" if result.get("success") else "paused_or_failed",
                new_information=[str(result.get("status"))] if result.get("status") else [],
                next_possible_actions=["COMPLETE", "FOLLOW_UP", "WAIT_FOR_APPROVAL", "WAIT_FOR_HUMAN_INPUT", "RETRY", "FAIL"],
            )
            # Record sanitized evidence for each tool attempt. This is not
            # a claim that an external side effect succeeded; verification is
            # only asserted when the tool explicitly reports it.
            evidence_entry = {
                "mission_id": mission_id,
                "execution_id": execution_id,
                "step_name": step_name,
                "attempt_index": attempt_index,
                "tool_name": step.get("tool_name"),
                "tool_version": step.get("tool_version", "1.0"),
                "risk_level": step.get("risk_level", "READ_ONLY"),
                "approval_class": step.get("approval_class", "READ_ONLY"),
                "approval_reference": result.get("approval_request_id"),
                "input": step.get("input", step.get("action_payload", {})),
                "outcome_status": result.get("status"),
                "success": bool(result.get("success")),
                "verification": result.get("verification") or result.get("verification_status") or "NOT_REPORTED",
            }
            report.setdefault("execution_evidence", []).append(sanitize_payload(evidence_entry))
            report["executed_actions"].append(sanitize_payload(step))
            report["results"].append(sanitize_payload(public_result))
            report["approvals"] = self._approval_summary(result)
            report["observations"].append(observation.to_dict())
            if "observation" not in report:
                report["observation"] = observation.to_dict()
            report[f"observation_{observation_index}"] = observation.to_dict()
            observation_index += 1

            if (
                result.get("success")
                and step.get("tool_name") == "pinterest.get_account_status"
                and result.get("status") in {"not_started", "needs_reconnect"}
                and "start_onboarding" not in completed_steps
                and not any(s.get("step_name") == "start_onboarding" for s in plan)
            ):
                dynamic_step = self._build_dynamic_onboarding_step(step, metadata)
                dynamic_validation = self._validator.validate(
                    [dynamic_step],
                    owner_id=self.owner_id,
                    discovered=self.discover_capabilities(),
                )
                if not dynamic_validation.get("success"):
                    return self._failed_report(
                        report,
                        "Dynamic onboarding step failed validation: "
                        + "; ".join(dynamic_validation.get("errors", [])),
                    )
                validated_dynamic_step = dynamic_validation["steps"][0]
                plan.append(validated_dynamic_step)
                report.setdefault("plan", []).append(sanitize_payload(validated_dynamic_step))
                report.setdefault("validated_plan", []).append(sanitize_payload(validated_dynamic_step))

            decision = self._next_decision(
                result,
                step_name,
                plan,
                index,
                goal=report.get("user_goal", ""),
            )
            # Do not mark a Pin-intent mission complete after only generating
            # generic affiliate content. Pause and collect the missing inputs
            # before a publish approval can ever be created.
            original_goal = str(
                report.get("original_user_goal")
                or (step.get("input") or {}).get("goal")
                or report.get("user_goal", "")
            )
            normalized_goal = " ".join(original_goal.lower().split())
            approval_gated_pin_request = (
                "pinterest" in normalized_goal
                and ("pin" in normalized_goal or "publish" in normalized_goal)
                and any(phrase in normalized_goal for phrase in (
                    "do not publish automatically",
                    "don't publish automatically",
                    "dont publish automatically",
                    "wait for my explicit approval before publishing",
                    "wait for approval before publishing",
                ))
            )
            if (
                result.get("success")
                and approval_gated_pin_request
                and not step["tool_name"].startswith("studio.")
                and (
                    bool(result.get("sample_pin"))
                    or "prepare_affiliate_content" in str(step.get("tool_name", ""))
                    or ("content" in str(step.get("tool_name", "")).lower()
                        and "prepare" in str(step.get("tool_name", "")).lower())
                )
                and not self._has_complete_pinterest_pin_inputs(metadata)
            ):
                decision = "WAIT_FOR_HUMAN_INPUT"
                result["message"] = (
                    "Review the draft, then provide the Pinterest board name, "
                    "destination/affiliate URL, and image URL. Nothing has been published."
                )
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
                result=sanitize_payload({
                    "result": result,
                    "observation": observation.to_dict(),
                    "execution_evidence": evidence_entry,
                }),
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
                    "execution_evidence": evidence_entry,
                }),
            )
            if not state_update.get("success"):
                return self._failed_report(report, state_update.get("error", "Execution state update failed"))

            if step_status == "completed":
                completed_steps[step_name] = result
            if decision == "COMPLETE":
                return self._complete_execution(execution_id, report, completed_steps)
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
                report["required_user_action"] = result.get("message") or (
                    "Provide your affiliate niche, target product or offer, and content goals before we create a Pinterest plan."
                    if step_name == "check_connection_status"
                    and result.get("status") == "connected"
                    and "affiliate marketing" in report.get("user_goal", "").lower()
                    else "Complete the requested human step"
                )
                report["final_status"] = "WAIT_FOR_HUMAN_INPUT"
                return {"success": False, "status": "WAIT_FOR_HUMAN_INPUT", "report": sanitize_payload(report)}
            if decision == "FOLLOW_UP":
                index += 1
                continue
            if decision == "RETRY":
                retry_decision = classify_failure(result.get("error", "execution failure"))
                if result.get("retryable", retry_decision.retryable) and attempt_index < self._max_decisions:
                    retry_update = self._execution.update_execution_state(execution_id, self.owner_id, status="RETRYING", retry_count=attempt_index + 1, result=sanitize_payload({"decision": decision, "next_step": step_name, "result": result}))
                    if not retry_update.get("success"):
                        return self._failed_report(report, retry_update.get("error", "Retry state persistence failed"))
                    report["final_status"] = "RETRY"
                    return {"success": False, "status": "RETRY", "report": sanitize_payload(report)}
            report["final_status"] = "FAIL"
            report["failures_retries"].append(result.get("error") or "Mission failed")
            if completed_steps.get("generate_studio_draft"):
                report["outcome"] = "PARTIAL"
                if completed_steps.get("save_studio_draft"):
                    report["deliverable"] = completed_steps["save_studio_draft"]
            failure = self._execution.mark_failed(
                execution_id, self.owner_id,
                result={"report": sanitize_payload(report)},
                error=result.get("error", "Mission failed"),
            )
            if not failure.get("success"):
                return self._failed_report(report, failure.get("error", "Execution failure persistence failed"))
            return {"success": False, "status": "FAIL", "report": sanitize_payload(report)}

        return self._complete_execution(execution_id, report, completed_steps)

    def _complete_execution(
        self, execution_id: str, report: dict[str, Any], completed_steps: dict[str, Any],
    ) -> dict[str, Any]:
        if report["understood_objective"].get("intent") == "create_studio_draft":
            deliverable = completed_steps.get("verify_studio_draft")
            if not deliverable or deliverable.get("verification") != "OWNER_SCOPED_STUDIO_READBACK":
                return self._failed_report(report, "Studio draft verification evidence is missing")
            report["outcome"] = "VERIFIED"
            report["deliverable"] = deliverable
        report["final_status"] = "COMPLETE"
        safe_report = sanitize_payload(report)
        completion = self._execution.mark_completed(execution_id, self.owner_id, {"report": safe_report})
        if not completion.get("success"):
            return self._failed_report(report, completion.get("error", "Execution completion failed"))
        return {"success": True, "status": "COMPLETE", "report": safe_report}

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
        status = execution.get("status")
        if status == "SUCCEEDED":
            saved_report = durable_result.get("report") or {**report, "final_status": "COMPLETE"}
            return {"success": True, "status": "COMPLETE", "report": sanitize_payload(saved_report)}
        if status in {"FAILED", "CANCELLED"}:
            saved_report = durable_result.get("report")
            if saved_report:
                return {"success": False, "status": "FAIL", "report": sanitize_payload(saved_report)}
            return self._failed_report(report, f"Execution is already {status.lower()}")
        if status == "WAITING_APPROVAL" and durable_result.get("approval_request_id"):
            report["resume_information"] = {
                "approval_request_id": durable_result["approval_request_id"],
                "execution_id": execution_id,
            }
            report["final_status"] = "WAIT_FOR_APPROVAL"
            return {"success": False, "status": "WAIT_FOR_APPROVAL", "report": sanitize_payload(report)}
        running = self._execution.update_execution_state(execution_id, self.owner_id, status="RUNNING")
        if not running.get("success"):
            return self._failed_report(report, running.get("error", "Execution resume persistence failed"))
        return self._execute_plan(mission_id, execution_id, plan, report, None)

    def _approval_worker_is_owned(self, worker_id: Any) -> bool:
        """Verify that an approval's worker belongs to this authenticated owner.

        Older in-memory flows used the owner UUID as the worker identifier.
        Production worker records have their own UUID, so accept those only
        after a user-scoped ownership and active-status lookup.
        """
        normalized = str(worker_id or "").strip()
        if not normalized:
            return False
        if normalized == self.owner_id:
            return True
        if self._client is None:
            return False
        try:
            rows = (
                self._client.table("workers")
                .select("id,owner_id,status")
                .eq("id", normalized)
                .eq("owner_id", self.owner_id)
                .eq("status", "active")
                .limit(1)
                .execute()
                .data
                or []
            )
        except Exception:
            # Authorization must fail closed when the ownership lookup fails.
            return False
        return any(
            str(row.get("id") or "") == normalized
            and str(row.get("owner_id") or "") == self.owner_id
            and str(row.get("status") or "").lower() == "active"
            for row in rows
        )

    def resume_approval(self, approval_request_id: str) -> dict[str, Any]:
        """Resume an approved onboarding action through ApprovalResumeService."""
        request = self._approvals.get_request(approval_request_id)
        request_payload = request.get("payload") or {} if request else {}
        if (
            not request
            or request.get("owner_id") != self.owner_id
            or not self._approval_worker_is_owned(request_payload.get("worker_id"))
        ):
            return {"success": False, "status": "FAIL", "error": "Approval request not found"}
        action_type = request.get("action_type") or ""
        if action_type not in {"start_platform_onboarding", "publish_content"}:
            return {"success": False, "status": "FAIL", "error": "Unsupported approval action"}
        approval_status = (request.get("status") or "").lower()
        payload = request.get("payload") or {}
        execution_id = payload.get("execution_id")
        if not execution_id:
            return {
                "success": False,
                "status": "FAIL",
                "error": "Durable execution_id is required to resume an external action",
            }
        owned_execution = self._execution.get_execution(
            execution_id, owner_id=self.owner_id
        )
        if not owned_execution:
            return {"success": False, "status": "FAIL", "error": "Execution not found"}
        if approval_status == "rejected":
            rejection = self._execution.mark_failed(
                execution_id,
                self.owner_id,
                error="Approval rejected",
                result={"decision": "REJECTED", "approval_request_id": approval_request_id},
            )
            if not rejection.get("success"):
                return {"success": False, "status": "FAIL", "error": rejection.get("error", "Approval rejection persistence failed")}
            return {"success": False, "status": "approval_rejected", "result": rejection}
        if approval_status == "pending":
            return {"success": False, "status": "WAITING_APPROVAL", "error": "Approval is still pending"}
        if approval_status != "approved":
            return {"success": False, "status": "FAIL", "error": "Approval is not in an executable state"}

        claim = None
        if execution_id:
            step_name = "publish_content" if action_type == "publish_content" else "start_onboarding"
            # Approval resume is a new durable attempt for the same logical
            # operation. Keep attempt_index monotonic so the original
            # pre-approval failure cannot be mistaken for the latest attempt.
            attempt_index = self._next_attempt_index(execution_id, step_name)
            claim = self._execution.claim_step(
                execution_id,
                self.owner_id,
                step_id=str(uuid4()),
                attempt_index=attempt_index,
                idempotency_key=f"p1-7b:{execution_id}:{step_name}:approval:{attempt_index}",
                step_name=step_name,
                operation_key=payload.get("operation_key") or self._operation_key(
                    request.get("mission_id") or "", execution_id, step_name
                ),
                claim_token=self._claim_token,
            )
            if not claim.get("success"):
                return {"success": False, "status": "FAIL", "error": claim.get("error", "Approval step claim failed")}
            if not claim.get("claimed"):
                stored_step = claim.get("step") or {}
                if stored_step.get("status") == "completed":
                    return {"success": True, "status": "completed", "result": stored_step.get("result", {})}
                if stored_step.get("status") == "in_progress":
                    return {"success": False, "status": "WAITING_APPROVAL", "error": "Approval resume is already in progress"}
                return {
                    "success": False,
                    "status": "FAIL",
                    "error": "Approval execution step is not claimable; manual reconciliation is required",
                }

        resume_service = ApprovalResumeService(
            approval_gateway=self._approvals,
            connector_registry=self._connectors,
        )
        result = resume_service.resume(approval_request_id, current_user_id=self.owner_id)
        safe_result = sanitize_payload(result)
        if execution_id and claim:
            step_result = self._execution.update_step(
                execution_id,
                self.owner_id,
                claim["step"]["id"],
                # An onboarding action that creates a human checkpoint has
                # completed its connector step; the execution itself remains
                # WAITING_INPUT until the user completes that checkpoint.
                status="completed" if result.get("status") in {"completed", "resumed", "awaiting_human_intervention"} else "failed",
                result=safe_result,
                retry_category=None if result.get("status") in {"completed", "resumed", "awaiting_human_intervention"} else "EXECUTION",
                claim_token=self._claim_token,
            )
            if not step_result.get("success"):
                return {"success": False, "status": "FAIL", "error": step_result.get("error", "Approval result persistence failed")}
            if result.get("status") in {"completed", "resumed"}:
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
            elif result.get("status") in {"publish_uncertain", "publish_in_progress"}:
                # A provider timeout or in-flight reservation is not proof of
                # failure. Stop automatic retries and require reconciliation.
                waiting = self._execution.update_execution_state(
                    execution_id,
                    self.owner_id,
                    status="WAITING_INPUT",
                    result=safe_result,
                )
                if not waiting.get("success"):
                    return {"success": False, "status": "FAIL", "error": waiting.get("error", "Publish reconciliation state persistence failed")}
            elif result.get("status") == "resume_failed":
                failure = self._execution.mark_failed(
                    execution_id,
                    self.owner_id,
                    error=result.get("error") or "Approved action failed",
                    result={"approval_resume": safe_result, "approval_request_id": approval_request_id},
                )
                if not failure.get("success"):
                    return {"success": False, "status": "FAIL", "error": failure.get("error", "Approval failure persistence failed")}
        return {"success": result.get("status") in {"completed", "resumed"}, "status": result.get("status"), "result": safe_result}

    def _resolve_mission(self, mission_id: str | None, goal: str) -> dict[str, Any]:
        """Resolve an owned durable mission before creating execution state."""
        if mission_id:
            mission = self._mission_engine.get_mission(mission_id, owner_id=self.owner_id, client=self._client)
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

    def _publish_intent(self, goal: str) -> bool:
        """Return True for Pin publishing intent, except genuinely draft-only requests."""
        normalized = " ".join(goal.lower().replace("pinterest", "").split())
        approval_gated = any(phrase in normalized for phrase in (
            "do not publish automatically",
            "don't publish automatically",
            "dont publish automatically",
            "wait for my explicit approval before publishing",
            "wait for approval before publishing",
        ))
        # A request that explicitly says not to publish anything is draft-only,
        # even if it mentions Pins or content ideas.
        hard_draft_only = any(phrase in normalized for phrase in (
            "do not publish anything",
            "don't publish anything",
            "dont publish anything",
        ))
        if hard_draft_only:
            return False
        draft_only = any(phrase in normalized for phrase in (
            "prepare five content ideas and one sample pin",
            "draft only",
            "content ideas",
            "content plan",
            "prepare content",
        ))
        if draft_only and not approval_gated:
            return False
        negated = any(phrase in normalized for phrase in (
            "do not publish",
            "don't publish",
            "dont publish",
            "without publishing",
            "not publish anything",
        ))
        if negated and not approval_gated:
            return False
        return "publish" in normalized or ("create" in normalized and "pin" in normalized) or "affiliate content" in normalized

    def _publish_inputs_complete(self, payload: dict[str, Any]) -> bool:
        """Require complete content inputs before creating a publish approval."""
        return all(payload.get(field) for field in ("board_name", "pin_text", "link_url", "image_url"))

    def _publish_step(self, required_payload: dict[str, Any], affiliate_meta: dict[str, Any]) -> dict[str, Any]:
        """Build a validated publish_content plan step."""
        content_input = {
            k: required_payload.get(k)
            for k in ("board_name", "pin_text", "link_url", "opportunity_id", "image_url", "title")
            if k in required_payload
        }
        return {
            "step_name": "publish_content",
            "tool_name": "publish_content",
            "input": {"platform": "pinterest", **content_input},
            "action_type": "publish_content",
            "requires_approval": True,
            "metadata": affiliate_meta,
        }

    def _build_plan(self, objective: Any, discovered: dict[str, Any]) -> list[dict[str, Any]]:
        goal = objective.goal.lower()
        required_payload = dict(getattr(objective, "content_inputs", {}) or {})
        platform_constraints = list(getattr(objective, "platform_constraints", []) or [])
        steps: list[dict[str, Any]] = []
        is_pinterest = "pinterest" in goal or "pinterest" in platform_constraints or required_payload.get("platform") == "pinterest"
        # "Do not publish automatically" is an approval constraint, not a
        # request to stop at a generic affiliate-content draft. Keep explicit
        # draft-only requests separate from a Pinterest Pin request that must
        # be previewed and approved before publishing.
        approval_gated_publish = any(phrase in goal for phrase in (
            "do not publish automatically",
            "don't publish automatically",
            "dont publish automatically",
            "wait for my explicit approval before publishing",
            "wait for approval before publishing",
        ))
        # A read-only Pinterest status request may explicitly say "do not
        # publish anything". That is a safety constraint, not a request to
        # generate generic affiliate content. Keep status/connection checks
        # on the Pinterest path; only explicit drafting language selects the
        # content-only worker.
        explicit_draft_only = any(term in goal for term in (
            "draft only", "prepare five content ideas", "content ideas",
            "content plan", "sample pin", "marketing tips", "prepare content",
        )) and not approval_gated_publish
        explicit_connection = any(term in goal for term in (
            "connect", "onboard", "authorize", "link pinterest", "enroll",
            "kuunganisha pinterest",
        ))
        campaign_start = any(term in goal for term in (
            "start affiliate marketing", "start an affiliate", "start affiliate",
            "kuanza affiliate",
        ))
        is_content_planning = explicit_draft_only
        connection_intent = explicit_connection and not is_content_planning
        status_check_intent = (
            any(term in goal for term in (
                "check my pinterest", "pinterest account status", "pinterest connection status",
                "check pinterest status", "is pinterest connected", "hali ya pinterest",
            ))
            or campaign_start
        ) and not is_content_planning
        if is_content_planning:
            steps.append({
                "step_name": "prepare_affiliate_content",
                "tool_name": "prepare_affiliate_content",
                "input": {
                    "goal": objective.goal,
                    "niche": str(required_payload.get("niche") or "AI tools and digital marketing"),
                    "markets": str(required_payload.get("markets") or "USA, UK, Canada, Australia"),
                    "platform": str(required_payload.get("platform") or "Pinterest"),
                },
                "action_type": "prepare_affiliate_content",
                "action_payload": {"goal": objective.goal, **required_payload},
                "metadata": required_payload or None,
            })
        if is_pinterest and not is_content_planning and (connection_intent or status_check_intent or self._publish_intent(goal)):
            affiliate_meta = {k: v for k, v in required_payload.items() if k not in {"platform"}}
            steps.append({
                "step_name": "check_connection_status",
                "tool_name": "pinterest.get_account_status",
                "input": {"platform": "pinterest"},
                "action_type": "pinterest.get_account_status",
                "action_payload": {"platform": "pinterest", **required_payload},
                "metadata": affiliate_meta,
            })
            if connection_intent:
                steps.append({
                    "step_name": "start_onboarding",
                    "tool_name": "start_platform_onboarding",
                    "input": {"platform": "pinterest"},
                    "action_type": "start_platform_onboarding",
                    "action_payload": {"platform": "pinterest", **required_payload},
                    "requires_approval": True,
                    "metadata": affiliate_meta,
                })
            if self._publish_intent(goal):
                discovered_tools = {tool.get("tool_name") for tool in discovered.get("tools", [])}
                if self._publish_inputs_complete(required_payload):
                    if "publish_content" in discovered_tools:
                        steps.append(self._publish_step(required_payload, affiliate_meta))
                elif "prepare_affiliate_content" in discovered_tools:
                    # A Pin request without a board, destination URL, and image
                    # must create a draft first, then pause for those inputs.
                    steps.append({
                        "step_name": "prepare_pinterest_pin_draft",
                        "tool_name": "prepare_affiliate_content",
                        "input": {
                            "goal": objective.goal,
                            "niche": str(required_payload.get("niche") or "AI tools and digital marketing"),
                            "markets": str(required_payload.get("markets") or "USA, UK, Canada, Australia"),
                            "platform": "Pinterest",
                        },
                        "action_type": "prepare_affiliate_content",
                        "action_payload": {"goal": objective.goal, **required_payload},
                        "metadata": affiliate_meta or None,
                    })
        if not steps and goal.startswith("log "):
            steps.append({
                "step_name": "record_goal",
                "tool_name": "log",
                "input": {"message": objective.goal},
                "action_type": "log",
                "action_payload": {"message": objective.goal, **required_payload},
                "metadata": required_payload if required_payload else None,
            })
        return steps

    @staticmethod
    def _pinterest_draft_plan(goal: str) -> list[dict[str, Any]]:
        return [
            {
                "step_name": "check_connection_status",
                "tool_name": "pinterest.get_account_status",
                "input": {"platform": "pinterest"},
                "action_type": "pinterest.get_account_status",
                "action_payload": {"platform": "pinterest"},
                "metadata": None,
            },
            {
                "step_name": "prepare_pinterest_pin_draft",
                "tool_name": "prepare_affiliate_content",
                "input": {
                    "goal": goal,
                    "niche": "AI tools and digital marketing",
                    "markets": "USA, UK, Canada, Australia",
                    "platform": "Pinterest",
                },
                "action_type": "prepare_affiliate_content",
                "action_payload": {"goal": goal},
                "metadata": None,
            },
        ]

    @staticmethod
    def _build_dynamic_onboarding_step(
        status_step: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build an ``start_onboarding`` step to inject after a status check."""
        affiliate_meta = dict(status_step.get("metadata") or {})
        return {
            "step_name": "start_onboarding",
            "tool_name": "start_platform_onboarding",
            "input": {"platform": "pinterest"},
            "action_type": "start_platform_onboarding",
            "action_payload": {"platform": "pinterest", **affiliate_meta},
            "requires_approval": True,
            "metadata": affiliate_meta,
        }

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
        if tool_name.startswith("studio."):
            return execute_studio_step(
                tool_name, prepared["payload"], step.get("dependency_results", {}),
                owner_id=self.owner_id, client=self._client, operation_key=operation_key,
            )
        if tool_name == "prepare_affiliate_content":
            niche = str(payload.get("niche") or "AI tools and digital marketing")
            markets = str(payload.get("markets") or "USA, UK, Canada, and Australia")
            ideas = [
                {"title": f"5 {niche.title()} Tools That Save Time", "keywords": [niche, "AI productivity tools", "digital tools"], "audience_problem": "Too much time spent on repetitive work", "call_to_action": "Compare the tools and choose one that fits your workflow.", "visual_concept": "A clean numbered list with five tool categories and simple icons."},
                {"title": f"Beginner's Guide to Choosing {niche.title()} Tools", "keywords": [niche, "beginner guide", "tools for beginners"], "audience_problem": "Beginners feel overwhelmed by too many options", "call_to_action": "Save this guide before choosing a tool.", "visual_concept": "A three-step checklist with a clear beginner-friendly layout."},
                {"title": f"Free vs Paid {niche.title()} Tools: What to Check", "keywords": [niche, "free vs paid", "software comparison"], "audience_problem": "Uncertainty about whether a paid plan is worth it", "call_to_action": "Check features, limits, and pricing on the official site.", "visual_concept": "A balanced two-column comparison checklist."},
                {"title": f"A Simple {niche.title()} Workflow for Small Businesses", "keywords": [niche, "small business workflow", "digital marketing tips"], "audience_problem": "Small businesses need a manageable workflow", "call_to_action": "Save this workflow and adapt it to your business.", "visual_concept": "A five-step workflow diagram with short labels."},
                {"title": f"How to Evaluate {niche.title()} Tools Before Buying", "keywords": [niche, "tool review checklist", "buying guide"], "audience_problem": "Risk of buying a tool that does not fit their needs", "call_to_action": "Use the checklist and verify current terms before purchasing.", "visual_concept": "A buyer's checklist with pricing, features, privacy, and support."},
            ]
            return {
                "success": True,
                "status": "draft_prepared",
                "research_note": "These are AI-generated content ideas, not verified live market research. Confirm product claims, current prices, and affiliate-program terms before use.",
                "target_markets": markets,
                "content_ideas": ideas,
                "sample_pin": {
                    "title": f"A Beginner-Friendly Guide to {niche.title()} Tools",
                    "description": f"Explore practical ways to evaluate {niche} tools for your workflow. Compare features, pricing, free-plan limits, privacy, and support before choosing. Save this guide for your next tool review. If you use an affiliate link, clearly disclose that you may earn a commission at no extra cost to the buyer.",
                    "keywords": [niche, "AI tools", "digital marketing", "productivity", "beginner guide"],
                    "visual_concept": "Vertical Pinterest graphic with a bold headline, three evaluation tips, and a simple checklist.",
                    "affiliate_link": None,
                    "published": False,
                },
                "published": False,
                "requires_human_approval_before_publishing": True,
            }
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
        if tool_name == "publish_content":
            approval_payload = {
                "platform": "pinterest",
                "content": prepared["payload"],
                "worker_id": self.owner_id,
                "execution_id": execution_id,
                "operation_key": operation_key or self._operation_key(mission_id, execution_id, "publish_content"),
            }
            request = self._approvals.create_request(
                mission_id=mission_id,
                action_type="publish_content",
                risk_level="WRITE_EXTERNAL",
                payload=approval_payload,
                owner_id=self.owner_id,
            )
            if not request.get("success"):
                return request
            return {
                "success": False,
                "pending_approval": True,
                "status": "WAITING_APPROVAL",
                "approval_request_id": request["request"].get("id"),
                "message": "Approval required before publishing content",
            }
        return ActionEngine(owner_id=self.owner_id).execute_action(tool_name, prepared["payload"])

    def _next_decision(
        self,
        result: dict[str, Any],
        step_name: str,
        plan: list[dict[str, Any]],
        index: int,
        *,
        goal: str = "",
    ) -> str:
        current_step = plan[index] if 0 <= index < len(plan) else {}
        current_tool = str(current_step.get("tool_name", ""))
        if current_tool.startswith("studio."):
            if result.get("success"):
                return "FOLLOW_UP" if index + 1 < len(plan) else "COMPLETE"
            return "RETRY" if result.get("retryable") and self._max_decisions > 0 else "FAIL"
        if result.get("pending_approval"):
            return "WAIT_FOR_APPROVAL"
        if result.get("awaiting_human_intervention") or result.get("waiting_input"):
            return "WAIT_FOR_HUMAN_INPUT"
        normalized_goal = " ".join(goal.lower().split())
        approval_gated_pin_request = (
            "pinterest" in normalized_goal
            and ("pin" in normalized_goal or "publish" in normalized_goal)
            and any(phrase in normalized_goal for phrase in (
                "do not publish automatically",
                "don't publish automatically",
                "dont publish automatically",
                "wait for my explicit approval before publishing",
                "wait for approval before publishing",
            ))
        )
        # A successful generic draft is not the end of an approval-gated Pin
        # mission: collect the missing board/link/image details before approval.
        # The plan step can be named prepare_pinterest_pin_draft while its tool
        # is prepare_affiliate_content, so check both to preserve the approval gate.
        is_pin_draft_step = (
            step_name in {"prepare_affiliate_content", "prepare_pinterest_pin_draft"}
            or current_tool == "prepare_affiliate_content"
        )
        if is_pin_draft_step and approval_gated_pin_request:
            return "WAIT_FOR_HUMAN_INPUT"
        if result.get("success"):
            if result.get("status") in {"not_started", "needs_reconnect", "failed", "onboarding"} and index + 1 < len(plan):
                return "FOLLOW_UP"
            if index + 1 < len(plan) and result.get("status") == "connected" and plan[index + 1].get("tool_name") != "start_platform_onboarding":
                return "FOLLOW_UP"
            if (
                step_name == "check_connection_status"
                and result.get("status") == "connected"
                and index + 1 >= len(plan)
                and "affiliate marketing" in goal.lower()
            ):
                return "WAIT_FOR_HUMAN_INPUT"
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
