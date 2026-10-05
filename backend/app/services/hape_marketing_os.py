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
from app.services.lead_qualification import LeadQualificationService
from app.services.approval_gateway import ApprovalGateway

SUPPORTED_CHANNELS = {"facebook", "instagram", "whatsapp"}
LEAD_STATUSES = {"new", "qualified", "contacted", "negotiating", "won", "lost", "paused"}
DEAL_STAGES = {"qualified", "quote_pending_approval", "quote_sent", "negotiation", "won", "lost"}

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
        self.qualifier = LeadQualificationService()

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
            lead["deal_stage"] = metadata.get("deal_stage") or "qualified"
            lead["quote"] = metadata.get("quote")
            lead["deal"] = metadata.get("deal")
            lead["updated_at"] = row.get("updated_at")
            leads.append(lead)
        return leads

    def qualify_lead(self, lead_id: str, *, message: str | None = None) -> dict[str, Any]:
        mission = self.missions.get_mission(lead_id)
        if not self._is_lead(mission):
            return {"success": False, "error": "Lead not found"}
        metadata = dict(mission.get("metadata") or {})
        lead = dict(metadata.get("lead") or {})
        source_message = (message or lead.get("need") or "").strip()
        result = self.qualifier.qualify(message=source_message, existing=lead)
        qualification = result["qualification"]
        lead.update({
            "quantity": qualification.get("quantity") or lead.get("quantity"),
            "gauge": qualification.get("gauge"),
            "product": qualification.get("product"),
            "location": qualification.get("location") or lead.get("location"),
            "urgency": qualification.get("urgency"),
            "qualification_score": qualification.get("score"),
            "qualification_tier": qualification.get("tier"),
            "missing_information": qualification.get("missing_information", []),
            "next_action": qualification.get("next_action"),
            "qualified_at": _now_iso(),
            "status": "qualified" if qualification.get("score", 0) >= 40 else "new",
        })
        metadata["lead"] = lead
        metadata["qualification"] = qualification
        metadata["last_inbound_message"] = source_message
        return self.missions.update_mission_metadata(lead_id, metadata=metadata, result={
            "action": "lead_qualified",
            "qualification": qualification,
        })

    def suggest_follow_up(self, lead_id: str) -> dict[str, Any]:
        mission = self.missions.get_mission(lead_id)
        if not self._is_lead(mission):
            return {"success": False, "error": "Lead not found"}
        lead = dict((mission.get("metadata") or {}).get("lead") or {})
        missing = list(lead.get("missing_information") or [])
        if missing:
            labels = {"quantity": "quantity", "gauge": "gauge", "delivery_location": "delivery location"}
            requested = ", ".join(labels.get(item, item) for item in missing)
            message = f"Thank you for contacting HAPE BROTHERS. To prepare your roofing sheet quote, please share your {requested}."
        else:
            message = "Thank you for contacting HAPE BROTHERS. We have your requirements. We can prepare the next step for your order now."
        channel = lead.get("channel") or "whatsapp"
        if channel != "whatsapp":
            return {"success": False, "error": "Automated lead follow-up currently supports WhatsApp only"}
        recipient = lead.get("phone")
        if not recipient:
            return {"success": False, "error": "Lead is missing a WhatsApp phone number"}
        approval = ApprovalGateway(client=self.client).create_request(
            mission_id=lead_id,
            action_type="publish_content",
            risk_level="medium",
            owner_id=self.owner_id,
            payload={
                "platform": "meta",
                "worker_id": self.owner_id,
                "content": {
                    "channel": "whatsapp",
                    "recipient": recipient,
                    "message": message,
                },
                "lead_id": lead_id,
                "operation": "lead_follow_up",
            },
            client=self.client,
        )
        if not approval.get("success"):
            return {"success": False, "error": approval.get("error", "Failed to create follow-up approval")}
        return {
            "success": True,
            "status": "waiting_approval",
            "action": "follow_up",
            "channel": channel,
            "message": message,
            "lead_id": lead_id,
            "qualification_tier": lead.get("qualification_tier"),
            "approval_request_id": (approval.get("request") or {}).get("id"),
        }

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

    def draft_quote(self, lead_id: str, *, unit_price: float, delivery_cost: float = 0, currency: str = "TZS", notes: str | None = None) -> dict[str, Any]:
        if unit_price < 0 or delivery_cost < 0:
            return {"success": False, "error": "unit_price and delivery_cost must be non-negative"}
        mission = self.missions.get_mission(lead_id)
        if not self._is_lead(mission):
            return {"success": False, "error": "Lead not found"}
        metadata = dict(mission.get("metadata") or {})
        lead = dict(metadata.get("lead") or {})
        quantity = lead.get("quantity")
        if not quantity:
            return {"success": False, "error": "Lead quantity is required before drafting a quote"}
        phone = lead.get("phone")
        if not phone:
            return {"success": False, "error": "Lead is missing a WhatsApp phone number"}
        quantity = int(quantity)
        subtotal = quantity * float(unit_price)
        total = subtotal + float(delivery_cost)
        cur = currency.strip().upper() or "TZS"
        quote = {"quantity": quantity, "gauge": lead.get("gauge"), "product": lead.get("product") or "mabati", "delivery_location": lead.get("location"), "unit_price": float(unit_price), "subtotal": subtotal, "delivery_cost": float(delivery_cost), "total": total, "currency": cur, "notes": notes.strip() if notes else None, "created_at": _now_iso()}
        message = f"HAPE BROTHERS QUOTATION\nProduct: {quote['product']}\nQuantity: {quantity} sheets" + (f"\nGauge: {quote['gauge']}" if quote["gauge"] else "") + (f"\nDelivery: {quote['delivery_location']}" if quote["delivery_location"] else "") + f"\nUnit price: {cur} {float(unit_price):,.0f}\nSubtotal: {cur} {subtotal:,.0f}" + (f"\nDelivery: {cur} {float(delivery_cost):,.0f}" if delivery_cost else "") + f"\nTOTAL: {cur} {total:,.0f}" + (f"\nNotes: {quote['notes']}" if quote["notes"] else "") + "\n\nPlease confirm if you would like us to proceed with your order."
        quote["message"] = message
        quote["approval_required"] = True
        metadata["quote"] = quote
        metadata["deal_stage"] = "quote_pending_approval"
        metadata["lead"] = lead
        approval = ApprovalGateway(client=self.client).create_request(mission_id=lead_id, action_type="publish_content", risk_level="medium", owner_id=self.owner_id, payload={"platform":"meta","worker_id":self.owner_id,"content":{"channel":"whatsapp","recipient":phone,"message":message},"lead_id":lead_id,"operation":"send_quote"}, client=self.client)
        if not approval.get("success"):
            return {"success": False, "error": approval.get("error", "Failed to create quote approval")}
        approval_id = (approval.get("request") or {}).get("id")
        metadata["quote"]["approval_request_id"] = approval_id
        updated = self.missions.update_mission_metadata(lead_id, metadata=metadata, result={"action":"quote_drafted","deal_stage":"quote_pending_approval","approval_request_id":approval_id})
        if not updated.get("success"):
            return updated
        return {"success": True, "status":"waiting_approval", "deal_stage":"quote_pending_approval", "lead_id":lead_id, "quote":quote, "approval_request_id":approval_id}

    def get_pipeline(self, lead_id: str) -> dict[str, Any]:
        mission = self.missions.get_mission(lead_id)
        if not self._is_lead(mission):
            return {"success": False, "error": "Lead not found"}
        metadata = dict(mission.get("metadata") or {})
        lead = dict(metadata.get("lead") or {})
        stage = str(metadata.get("deal_stage") or "qualified")
        if stage not in DEAL_STAGES:
            stage = "qualified"
        return {"success":True,"lead_id":lead_id,"deal_stage":stage,"lead_status":lead.get("status") or "new","qualification":metadata.get("qualification") or {},"quote":metadata.get("quote"),"deal":metadata.get("deal"),"next_action":lead.get("next_action")}

    def record_deal(self, lead_id: str, *, stage: str, amount: float | None = None, currency: str = "TZS", notes: str | None = None) -> dict[str, Any]:
        stage = stage.strip().lower()
        if stage == "negotiating": stage = "negotiation"
        if stage not in DEAL_STAGES:
            return {"success": False, "error": f"Unsupported deal stage '{stage}'"}
        mission = self.missions.get_mission(lead_id)
        if not self._is_lead(mission):
            return {"success": False, "error": "Lead not found"}
        metadata = dict(mission.get("metadata") or {})
        lead = dict(metadata.get("lead") or {})
        deal = dict(metadata.get("deal") or {})
        deal.update({"stage":stage,"amount":amount,"currency":currency.strip().upper() or "TZS","notes":notes.strip() if notes else None,"updated_at":_now_iso()})
        metadata["deal"] = deal
        metadata["deal_stage"] = stage
        if stage == "won": lead["status"]="won"
        elif stage == "lost": lead["status"]="lost"
        elif stage == "negotiation": lead["status"]="negotiating"
        elif stage == "quote_sent": lead["status"]="contacted"
        elif stage == "qualified": lead["status"]="qualified"
        metadata["lead"] = lead
        return self.missions.update_mission_metadata(lead_id, metadata=metadata, result={"action":"deal_update","stage":stage,"deal":deal,"lead_status":lead.get("status")})

    def dashboard(self) -> dict[str, Any]:
        rows = self.missions.list_missions(limit=500)
        counts = {"campaigns": 0, "content": 0, "leads": 0, "completed": 0, "waiting_approval": 0, "failed": 0}
        lead_statuses = {status: 0 for status in sorted(LEAD_STATUSES)}
        qualification_tiers = {"hot": 0, "warm": 0, "cold": 0}
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
                tier = str(lead.get("qualification_tier") or "").lower()
                if tier in qualification_tiers:
                    qualification_tiers[tier] += 1
        return {
            "success": True,
            "vertical": VERTICAL,
            "counts": counts,
            "lead_statuses": lead_statuses,
            "qualification_tiers": qualification_tiers,
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
