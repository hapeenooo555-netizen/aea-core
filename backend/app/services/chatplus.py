from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.services.approval_gateway import ApprovalGateway
from app.services.approval_resume_service import ApprovalResumeService
from app.services.connectors.pinterest_connector import PinterestConnector
from app.services.connectors.registry import ConnectorRegistry
from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.human_intervention import HumanInterventionManager
from app.services.memory_engine import AtlasMemoryEngine
from app.services.mission_execution_service import MissionExecutionService
from app.services.mission_orchestration import MissionOrchestrationService
from app.services.p1_7_contracts import sanitize_payload
from app.services.stores.onboarding_workflow_store import OnboardingWorkflowStore
from app.services.stores.pin_publish_store import PinPublishStore
from app.services.stores.platform_connection_store import PlatformConnectionStore

CHAT_USER_TYPE = "chatplus:user"
CHAT_ASSISTANT_TYPE = "chatplus:assistant"
CHAT_TYPES = {CHAT_USER_TYPE, CHAT_ASSISTANT_TYPE}
DEFAULT_CONVERSATION_ID = "default"

_STATE_LABELS = {
    "ready": "Ready",
    "thinking": "Thinking",
    "working": "Working",
    "waiting_for_approval": "Waiting for approval",
    "waiting_for_human": "Waiting for your action",
    "completed": "Completed",
    "failed": "Failed",
}

_MEMORY_NOTIFICATIONS: dict[tuple[str, str], list[dict[str, Any]]] = {}
_MEMORY_LOCK = RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or _now_iso())


def _build_in_memory_employee(owner_id: str, client: Any | None, approval_gateway: ApprovalGateway | None = None) -> EmployeeVerticalSlice:
    workflow_store = OnboardingWorkflowStore(client=client, durable_required=client is not None)
    connection_store = PlatformConnectionStore(client=client)
    pin_store = PinPublishStore(client=client, durable_required=client is not None)
    registry = ConnectorRegistry()
    registry.register(
        PinterestConnector(
            workflow_store=workflow_store,
            connection_store=connection_store,
            pin_store=pin_store,
        )
    )
    return EmployeeVerticalSlice(
        owner_id,
        connector_registry=registry,
        connection_store=connection_store,
        execution_service=MissionExecutionService(client=client, durable_required=client is not None),
        approval_gateway=approval_gateway,
        memory_engine=AtlasMemoryEngine(client=client, owner_id=owner_id),
    )


def _build_resume_service(owner_id: str, client: Any | None, approval_gateway: ApprovalGateway | None = None) -> ApprovalResumeService:
    workflow_store = OnboardingWorkflowStore(client=client, durable_required=client is not None)
    connection_store = PlatformConnectionStore(client=client)
    pin_store = PinPublishStore(client=client, durable_required=client is not None)
    registry = ConnectorRegistry()
    registry.register(
        PinterestConnector(
            workflow_store=workflow_store,
            connection_store=connection_store,
            pin_store=pin_store,
        )
    )
    return ApprovalResumeService(
        approval_gateway=approval_gateway,
        connector_registry=registry,
        human_intervention_manager=HumanInterventionManager(client=client),
    )


class ChatPlusService:
    _in_memory_approvals: ApprovalGateway | None = None

    def __init__(self, owner_id: str, client: Any | None = None) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self.client = client
        if client is None:
            if ChatPlusService._in_memory_approvals is None:
                gateway = ApprovalGateway(client=None)
                gateway._client = None
                ChatPlusService._in_memory_approvals = gateway
            self.approvals = ChatPlusService._in_memory_approvals
            if self.approvals._client is None:
                self.approvals._client = None
        else:
            self.approvals = ApprovalGateway(client=client)
        employee = _build_in_memory_employee(owner_id, client, self.approvals) if client is None else None
        self.orchestration = MissionOrchestrationService(
            owner_id,
            client=client,
            employee=employee,
        )

    def list_messages(self, conversation_id: str = DEFAULT_CONVERSATION_ID, limit: int = 100) -> dict[str, Any]:
        if self.client is not None:
            try:
                response = (
                    self.client.table("notifications")
                    .select("*")
                    .eq("owner_id", self.owner_id)
                    .eq("title", conversation_id)
                    .order("created_at", desc=False)
                    .limit(max(1, min(limit, 100)))
                    .execute()
                )
                rows = response.data or []
            except Exception as exc:
                return {"success": False, "error": str(exc)}
        else:
            with _MEMORY_LOCK:
                rows = [
                    dict(row)
                    for row in _MEMORY_NOTIFICATIONS.get((self.owner_id, conversation_id), [])
                ]
        rows = [row for row in rows if row.get("type") in CHAT_TYPES]
        rows = rows[-max(1, min(limit, 100)) :]
        messages = [self._normalize_message(row) for row in rows]
        return {"success": True, "conversation_id": conversation_id, "messages": messages}

    def send_message(
        self,
        message: str,
        conversation_id: str = DEFAULT_CONVERSATION_ID,
        client_message_id: str | None = None,
    ) -> dict[str, Any]:
        text = (message or "").strip()
        if not text:
            return {"success": False, "error": "Message is required"}
        if len(text) > 4000:
            return {"success": False, "error": "Message must be 4000 characters or fewer"}

        conversation_id = (conversation_id or DEFAULT_CONVERSATION_ID).strip() or DEFAULT_CONVERSATION_ID
        message_id = (client_message_id or str(uuid4())).strip()[:128] or str(uuid4())
        user_record = self._append_notification(
            conversation_id,
            CHAT_USER_TYPE,
            text,
            message_id=message_id,
        )
        if not user_record.get("success"):
            return user_record

        metadata = sanitize_payload(
            {
                "source": "chatplus",
                "conversation_id": conversation_id,
                "client_message_id": message_id,
            }
        )
        idempotency_key = f"chatplus:{self.owner_id}:{conversation_id}:{message_id}"
        created = self.orchestration.create_mission(
            text,
            title=text[:120],
            priority="normal",
            urgency="normal",
            business_importance=1,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )
        if not created.get("success") or not created.get("mission"):
            error = str(created.get("error") or "Mission creation failed")
            self._append_notification(
                conversation_id,
                CHAT_ASSISTANT_TYPE,
                f"I could not start the Employee workflow: {error}",
            )
            return {"success": False, "error": error, "message": self._last_assistant_message(conversation_id)}

        mission_id = created["mission"]["id"]
        execution = self.orchestration.run_mission(mission_id)
        mission = self.orchestration.get_mission(mission_id)
        approvals = self.list_approvals(mission_id)
        state = self.state_for_mission(mission, execution)
        assistant_text = self._assistant_text(text, mission, execution, state, approvals)
        assistant_record = self._append_notification(
            conversation_id,
            CHAT_ASSISTANT_TYPE,
            assistant_text,
        )
        assistant_message = assistant_record.get("message") or self._normalize_message(assistant_record)
        return {
            "success": bool(mission and mission.get("status") == "completed") or state["state"] == "waiting_for_approval",
            "conversation_id": conversation_id,
            "message": assistant_message,
            "mission": sanitize_payload(mission or {}),
            "execution": sanitize_payload(execution or {}),
            "state": state,
            "approvals": approvals,
        }

    def current_state(self, conversation_id: str = DEFAULT_CONVERSATION_ID) -> dict[str, Any]:
        missions = self.orchestration.list_missions(limit=100)
        chat_missions = [
            mission
            for mission in missions
            if (mission.get("metadata") or {}).get("source") == "chatplus"
            and (mission.get("metadata") or {}).get("conversation_id") == conversation_id
        ]
        if not chat_missions:
            return {
                "success": True,
                "conversation_id": conversation_id,
                "state": {"state": "ready", "label": _STATE_LABELS["ready"], "detail": "Ready for your next goal"},
                "mission": None,
                "approvals": [],
            }
        mission = max(chat_missions, key=lambda item: _timestamp(item.get("created_at")))
        approvals = self.list_approvals(mission.get("id"))
        execution = (mission.get("result") or {}) if isinstance(mission.get("result"), dict) else {}
        return {
            "success": True,
            "conversation_id": conversation_id,
            "mission": sanitize_payload(mission),
            "execution": sanitize_payload(execution),
            "state": self.state_for_mission(mission, None),
            "approvals": approvals,
        }

    def list_approvals(self, mission_id: str | None = None) -> list[dict[str, Any]]:
        if not mission_id:
            return []
        rows = self.approvals.list_requests(mission_id=mission_id, status="pending", limit=20, client=self.client)
        return [
            sanitize_payload(row)
            for row in rows
            if not row.get("owner_id") or row.get("owner_id") == self.owner_id
        ]

    def approve_approval(self, approval_id: str) -> dict[str, Any]:
        approval = self.approvals.get_request(approval_id, client=self.client)
        if not approval or approval.get("owner_id") != self.owner_id:
            return {"success": False, "error": "Approval request not found"}
        approved = self.approvals.approve_request(
            approval_id,
            approved_by=self.owner_id,
            client=self.client,
        )
        if not approved.get("success"):
            return approved
        resume = _build_resume_service(self.owner_id, self.client, self.approvals)
        resumed = resume.resume(approval_id, current_user_id=self.owner_id)
        self._update_mission_after_approval(approval, resumed)
        return {"success": True, "approval": sanitize_payload(approved.get("request") or approval), "resume": sanitize_payload(resumed)}

    def reject_approval(self, approval_id: str, reason: str = "") -> dict[str, Any]:
        approval = self.approvals.get_request(approval_id, client=self.client)
        if not approval or approval.get("owner_id") != self.owner_id:
            return {"success": False, "error": "Approval request not found"}
        rejected = self.approvals.reject_request(
            approval_id,
            reason=(reason or "")[:1000],
            rejected_by=self.owner_id,
            client=self.client,
        )
        if not rejected.get("success"):
            return rejected
        execution_id = (approval.get("payload") or {}).get("execution_id")
        if execution_id:
            MissionExecutionService(
                client=self.client,
                durable_required=self.client is not None,
            ).mark_failed(
                execution_id,
                self.owner_id,
                error="Approval rejected",
                result={"decision": "REJECTED", "approval_request_id": approval_id},
            )
        self._update_mission_after_approval(approval, {"status": "approval_rejected"})
        return {"success": True, "approval": sanitize_payload(rejected.get("request") or approval)}

    def _append_notification(
        self,
        conversation_id: str,
        notification_type: str,
        message: str,
        *,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": message_id or str(uuid4()),
            "user_id": self.owner_id,
            "owner_id": self.owner_id,
            "title": conversation_id,
            "message": message,
            "type": notification_type,
            "is_read": False,
            "created_at": _now_iso(),
        }
        if self.client is not None:
            try:
                response = self.client.table("notifications").insert(record).execute()
                row = (response.data or [record])[0]
                return {"success": True, "message": self._normalize_message(row)}
            except Exception as exc:
                return {"success": False, "error": f"Chat history persistence failed: {exc}"}
        with _MEMORY_LOCK:
            key = (self.owner_id, conversation_id)
            _MEMORY_NOTIFICATIONS.setdefault(key, []).append(dict(record))
        return {"success": True, "message": self._normalize_message(record)}

    def _last_assistant_message(self, conversation_id: str) -> dict[str, Any]:
        result = self.list_messages(conversation_id, limit=20)
        messages = result.get("messages") or []
        return next((message for message in reversed(messages) if message.get("role") == "assistant"), {})

    @staticmethod
    def _normalize_message(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "role": "user" if row.get("type") == CHAT_USER_TYPE else "assistant",
            "content": row.get("message") or "",
            "created_at": _timestamp(row.get("created_at")),
        }

    @staticmethod
    def state_for_mission(mission: dict[str, Any] | None, execution: dict[str, Any] | None = None) -> dict[str, str]:
        mission_status = str((mission or {}).get("status") or "").lower()
        execution_status = str((execution or {}).get("status") or "").upper()
        if mission_status in {"waiting_approval"}:
            state = "waiting_for_approval"
            detail = "Waiting for approval"
        elif mission_status in {"waiting_human"}:
            state = "waiting_for_human"
            detail = "Waiting for your action"
        elif mission_status in {"active"}:
            state = "working"
            detail = "Working"
        elif mission_status in {"retrying"} or execution_status in {"RETRY", "RETRYING"}:
            state = "thinking"
            detail = "Thinking"
        elif mission_status in {"pending", "scheduled"}:
            state = "ready"
            detail = "Ready"
        elif mission_status == "completed" or execution_status in {"COMPLETE", "COMPLETED", "SUCCEEDED"}:
            state = "completed"
            detail = "Completed"
        else:
            state = "failed"
            detail = "Failed"
        return {"state": state, "label": _STATE_LABELS[state], "detail": detail}

    @staticmethod
    def _assistant_text(
        goal: str,
        mission: dict[str, Any] | None,
        execution: dict[str, Any] | None,
        state: dict[str, str],
        approvals: list[dict[str, Any]],
    ) -> str:
        execution = execution or {}
        if state["state"] == "waiting_for_approval":
            text = "I'm waiting for your approval before continuing. Review the approval below."
        elif state["state"] == "waiting_for_human":
            text = "A Pinterest authorization/action is required before the Employee can continue. Please complete the requested step."
        elif state["state"] == "failed":
            error = execution.get("error") or (mission or {}).get("result", {}).get("error") or "The goal could not be completed"
            text = f"I could not complete that goal: {error}"
        elif state["state"] == "completed":
            report = execution.get("report") or (mission or {}).get("result", {})
            selected = (report or {}).get("selected_tools") or []
            suffix = f" Used tools: {', '.join(str(tool) for tool in selected)}." if selected else ""
            text = f"Your goal was processed through the Employee workflow.{suffix}"
        else:
            text = "Your goal is in the Employee workflow. I’ll keep its status available here."

        lowered = goal.lower()
        limitations: list[str] = []
        if "pinterest" in lowered:
            limitations.append("Pinterest actions use the existing connector and approval flow; this MVP does not provide live Pinterest opportunity search.")
        if "opportunity" in lowered:
            limitations.append("The Opportunity Engine is not available in this MVP, so no opportunity discovery was performed.")
        if approvals:
            limitations.append(f"Approval required: {approvals[0].get('id', 'pending action')}.")
        if limitations:
            text += " " + " ".join(limitations)
        return text

    def _update_mission_after_approval(self, approval: dict[str, Any], outcome: dict[str, Any]) -> None:
        mission_id = approval.get("mission_id")
        if not mission_id:
            return
        mission = self.orchestration.get_mission(mission_id)
        if not mission or (mission.get("metadata") or {}).get("source") != "chatplus":
            return
        target = {
            "completed": "completed",
            "awaiting_human_intervention": "waiting_human",
            "approval_rejected": "failed",
        }.get(str(outcome.get("status") or "").lower())
        if target:
            self.orchestration.transition(mission_id, target, result=sanitize_payload(outcome))


def require_success(result: dict[str, Any]) -> None:
    if result.get("success"):
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(result.get("error") or "Chat operation failed"),
    )


__all__ = [
    "CHAT_ASSISTANT_TYPE",
    "CHAT_USER_TYPE",
    "DEFAULT_CONVERSATION_ID",
    "ChatPlusService",
    "require_success",
]
