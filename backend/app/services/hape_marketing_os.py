"""HAPE BROTHERS Marketing OS vertical slice.

This module builds the first marketing automation layer on top of the existing
AEA mission/ownership model. It deliberately does not store OAuth tokens or
call Meta APIs directly; external account execution will be added behind
connector adapters once the required platform credentials are configured.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.mission_orchestration import MissionOrchestrationService

SUPPORTED_CHANNELS = {"facebook", "instagram", "whatsapp"}
LEAD_STATUSES = {"new", "qualified", "contacted", "negotiating", "won", "lost", "paused"}
DEAL_STAGES = {"qualified", "proposal", "negotiating", "won", "lost"}

VERTICAL = "hape_brothers_marketing"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HapeBrothersMarketingOS:
    """Owns HAPE BROTHERS marketing workflows while reusing AEA missions."""

    def __init__(self, owner_id: str, client: Any | None = None) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self.client = client
        self.missions = MissionOrchestrationService(owner_id, client=client)

    @staticmethod
    def normalize_channels(channels: list[str]) -> list[str]:
        normalized = []
        for channel in channels:
            value = str(channel).strip().lower()
            if value and value not in normalized:
                normalized.append(value)
        unsupported = sorted(set(normalized) - SUPPORTED_CHANNELS)
        if unsupported:
            raise ValueError(f"Unsupported channel(s): {', '.join(unsupported)}")
        if not normalized:
            raise ValueError("At least one marketing channel is required")
        return normalized

    def create_campaign(
        self,
        *,
        objective: str,
        channels: list[str],
        audience: str,
        call_to_action: str,
        approval_required: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        channels = self.normalize_channels(channels)
        if not objective.strip():
            return {"success": False, "error": "objective is required"}
        if not audience.strip():
            return {"success": False, "error": "audience is required"}
        if not call_to_action.strip():
            return {"success": False, "error": "call_to_action is required"}

        metadata = {
            "vertical": VERTICAL,
            "resource_type": "campaign",
            "channels": channels,
            "audience": audience.strip(),
            "call_to_action": call_to_action.strip(),
            "approval_required": approval_required,
        }
        return self.missions.create_mission(
            objective.strip(),
            title=f"HAPE BROTHERS Campaign: {objective.strip()[:80]}",
            priority="high",
            urgency="normal",
            business_importance=5,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )

    def create_content(
        self,
        *,
        channel: str,
        topic: str,
        offer: str,
        call_to_action: str,
        language: str = "en",
        approval_required: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        channels = self.normalize_channels([channel])
        channel = channels[0]
        if not topic.strip():
            return {"success": False, "error": "topic is required"}
        if not offer.strip():
            return {"success": False, "error": "offer is required"}
        if not call_to_action.strip():
            return {"success": False, "error": "call_to_action is required"}

        content = self._draft_content(
            channel=channel,
            topic=topic.strip(),
            offer=offer.strip(),
            call_to_action=call_to_action.strip(),
            language=language.strip().lower() or "en",
        )
        metadata = {
            "vertical": VERTICAL,
            "resource_type": "content",
            "channel": channel,
            "language": language.strip().lower() or "en",
            "topic": topic.strip(),
            "offer": offer.strip(),
            "call_to_action": call_to_action.strip(),
            "approval_required": approval_required,
            "draft": content,
        }
        return self.missions.create_mission(
            f"Create HAPE BROTHERS {channel} marketing content about {topic.strip()}",
            title=f"Content: {topic.strip()[:80]}",
            priority="normal",
            urgency="normal",
            business_importance=4,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )

    def create_lead(
        self,
        *,
        name: str,
        phone: str | None = None,
        company: str | None = None,
        location: str | None = None,
        source: str = "manual",
        channel: str = "whatsapp",
        need: str | None = None,
        quantity: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip():
            return {"success": False, "error": "name is required"}
        channels = self.normalize_channels([channel])
        lead = {
            "name": name.strip(),
            "phone": phone.strip() if phone else None,
            "company": company.strip() if company else None,
            "location": location.strip() if location else None,
            "source": source.strip().lower() or "manual",
            "channel": channels[0],
            "need": need.strip() if need else None,
            "quantity": quantity,
            "status": "new",
            "created_at": _now_iso(),
        }
        metadata = {
            "vertical": VERTICAL,
            "resource_type": "lead",
            "lead": lead,
        }
        return self.missions.create_mission(
            f"Qualify HAPE BROTHERS lead: {lead['name']}",
            title=f"Lead: {lead['name']}",
            priority="high",
            urgency="normal",
            business_importance=5,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )

    def list_leads(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.missions.list_missions(limit=max(1, min(limit, 500)))
        leads = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if metadata.get("vertical") != VERTICAL or metadata.get("resource_type") != "lead":
                continue
            lead = dict(metadata.get("lead") or {})
            lead["id"] = row.get("id")
            lead["mission_status"] = row.get("status")
            lead["updated_at"] = row.get("updated_at")
            leads.append(lead)
        return leads

    def follow_up_lead(
        self,
        lead_id: str,
        *,
        message: str,
        channel: str = "whatsapp",
        next_action: str = "follow_up",
    ) -> dict[str, Any]:
        channels = self.normalize_channels([channel])
        if not message.strip():
            return {"success": False, "error": "message is required"}
        mission = self.missions.get_mission(lead_id)
        if not self._is_lead(mission):
            return {"success": False, "error": "Lead not found"}
        lead = dict((mission.get("metadata") or {}).get("lead") or {})
        lead["status"] = "contacted"
        lead["last_contact_channel"] = channels[0]
        lead["last_contact_at"] = _now_iso()
        lead["next_action"] = next_action.strip() or "follow_up"
        result = {
            "action": "follow_up",
            "channel": channels[0],
            "message": message.strip(),
            "lead_status": lead["status"],
            "next_action": lead["next_action"],
        }
        started = self.missions.transition(lead_id, "active", result={"action": "follow_up_started"})
        if not started.get("success"):
            return started
        return self.missions.transition(lead_id, "completed", result=result)

    def record_deal(
        self,
        lead_id: str,
        *,
        stage: str,
        amount: float | None = None,
        currency: str = "TZS",
        notes: str | None = None,
    ) -> dict[str, Any]:
        stage = stage.strip().lower()
        if stage not in DEAL_STAGES:
            return {"success": False, "error": f"Unsupported deal stage '{stage}'"}
        mission = self.missions.get_mission(lead_id)
        if not self._is_lead(mission):
            return {"success": False, "error": "Lead not found"}
        result = {
            "action": "deal_update",
            "stage": stage,
            "amount": amount,
            "currency": currency.strip().upper() or "TZS",
            "notes": notes.strip() if notes else None,
            "lead_status": "won" if stage == "won" else "lost" if stage == "lost" else "negotiating",
            "updated_at": _now_iso(),
        }
        return self.missions.transition(lead_id, "completed", result=result)

    def dashboard(self) -> dict[str, Any]:
        rows = self.missions.list_missions(limit=500)
        counts = {"campaigns": 0, "content": 0, "leads": 0, "completed": 0, "waiting_approval": 0, "failed": 0}
        lead_statuses = {status: 0 for status in sorted(LEAD_STATUSES)}
        for row in rows:
            metadata = row.get("metadata") or {}
            if metadata.get("vertical") != VERTICAL:
                continue
            resource = metadata.get("resource_type")
            if resource in counts:
                counts[resource] += 1
            status = str(row.get("status") or "")
            if status == "completed":
                counts["completed"] += 1
            elif status == "waiting_approval":
                counts["waiting_approval"] += 1
            elif status == "failed":
                counts["failed"] += 1
            if resource == "lead":
                lead = metadata.get("lead") or {}
                lead_status = str(lead.get("status") or "new")
                if lead_status in lead_statuses:
                    lead_statuses[lead_status] += 1
        return {
            "success": True,
            "vertical": VERTICAL,
            "counts": counts,
            "lead_statuses": lead_statuses,
            "supported_channels": sorted(SUPPORTED_CHANNELS),
            "external_execution": {
                "facebook": "adapter_pending_credentials",
                "instagram": "adapter_pending_credentials",
                "whatsapp": "adapter_pending_credentials",
            },
        }

    @staticmethod
    def _is_lead(mission: dict[str, Any] | None) -> bool:
        if not mission:
            return False
        metadata = mission.get("metadata") or {}
        return metadata.get("vertical") == VERTICAL and metadata.get("resource_type") == "lead"

    @staticmethod
    def _draft_content(*, channel: str, topic: str, offer: str, call_to_action: str, language: str) -> dict[str, str]:
        if language != "en":
            language = "en"
        opening = f"Strong roofing starts with the right material: {topic}."
        body = f"HAPE BROTHERS offers {offer}. Built for customers who need reliable mabati supply and responsive service."
        return {
            "language": language,
            "hook": opening,
            "body": body,
            "cta": call_to_action,
            "channel": channel,
        }
