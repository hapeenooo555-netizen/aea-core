"""P1-8 durable employee orchestration for bounded affiliate jobs.

This service owns mission selection, lifecycle, scheduling, dependencies, and
operational events. It deliberately delegates execution to the existing
EmployeeVerticalSlice; it is not a second execution engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from .employee_vertical_slice import EmployeeVerticalSlice
from .memory_engine import AtlasMemoryEngine
from .p1_7_contracts import sanitize_payload


MISSION_STATES = {
    "pending", "scheduled", "active", "paused", "retrying", "waiting_dependency",
    "waiting_approval", "waiting_human", "completed", "failed", "cancelled",
}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
READY_STATES = {"pending", "scheduled", "retrying"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"scheduled", "active", "paused", "cancelled", "waiting_dependency"},
    "scheduled": {"active", "paused", "cancelled", "waiting_dependency"},
    "active": {"completed", "failed", "paused", "retrying", "waiting_approval", "waiting_human", "cancelled"},
    "paused": {"pending", "scheduled", "active", "cancelled"},
    "retrying": {"active", "paused", "failed", "cancelled", "waiting_dependency"},
    "waiting_dependency": {"pending", "scheduled", "paused", "cancelled"},
    "waiting_approval": {"active", "waiting_human", "completed", "failed", "paused", "cancelled"},
    "waiting_human": {"active", "failed", "paused", "cancelled"},
    "completed": set(),
    "failed": {"retrying", "cancelled"},
    "cancelled": set(),
}

PRIORITY_RANK = {"urgent": 4, "high": 3, "normal": 2, "low": 1}
URGENCY_RANK = {"critical": 4, "high": 3, "normal": 2, "low": 1}
AFFILIATE_KEYWORDS = ("affiliate", "pinterest", "content", "campaign", "pin", "commission")
ORCHESTRATION_LEASE_SECONDS = 60
_MISSION_LOCK = RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class MissionOrchestrationService:
    """Own bounded mission orchestration for one authenticated owner."""

    _memory_missions: dict[str, dict[str, Any]] = {}
    _memory_events: list[dict[str, Any]] = []
    _objective_keys: dict[tuple[str, str], str] = {}

    def __init__(
        self,
        owner_id: str,
        *,
        client: Any | None = None,
        employee: EmployeeVerticalSlice | None = None,
        employee_factory: Callable[[str, Any | None], EmployeeVerticalSlice] | None = None,
        memory_engine: AtlasMemoryEngine | None = None,
        max_orchestration_steps: int = 3,
        max_mission_attempts: int = 3,
    ) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        if max_orchestration_steps < 1 or max_mission_attempts < 1:
            raise ValueError("orchestration limits must be positive")
        self.owner_id = owner_id
        self.client = client
        self.employee = employee
        self.employee_factory = employee_factory
        self.memory = memory_engine or AtlasMemoryEngine(client=client, owner_id=owner_id)
        self.max_orchestration_steps = max_orchestration_steps
        self.max_mission_attempts = max_mission_attempts

    # ------------------------------------------------------------------
    # Mission lifecycle and persistence
    # ------------------------------------------------------------------
    def create_mission(
        self,
        objective: str,
        *,
        title: str | None = None,
        priority: str = "normal",
        urgency: str = "normal",
        business_importance: int = 1,
        scheduled_at: str | None = None,
        recurrence: str | None = None,
        dependencies: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(objective, str) or not objective.strip():
            return {"success": False, "error": "objective is required"}
        priority = priority.lower()
        urgency = urgency.lower()
        if priority not in PRIORITY_RANK or urgency not in URGENCY_RANK:
            return {"success": False, "error": "unsupported priority or urgency"}
        if not isinstance(business_importance, int) or not 1 <= business_importance <= 5:
            return {"success": False, "error": "business_importance must be between 1 and 5"}
        dependencies = list(dict.fromkeys(dependencies or []))
        if idempotency_key:
            existing_id = self._objective_keys.get((self.owner_id, idempotency_key))
            if existing_id:
                return {"success": True, "mission": self.get_mission(existing_id), "idempotent": True}
            if self.client is not None:
                try:
                    response = self.client.table("missions").select("*").eq("owner_id", self.owner_id).eq("orchestration_idempotency_key", idempotency_key).limit(1).execute()
                    if response.data:
                        existing = self._normalize(response.data[0])
                        self._objective_keys[(self.owner_id, idempotency_key)] = existing["id"]
                        return {"success": True, "mission": existing, "idempotent": True}
                except Exception:
                    return {"success": False, "error": "Mission idempotency lookup failed"}

        mission_id = str(uuid4())
        scheduled_state = "scheduled" if scheduled_at else "pending"
        record = {
            "id": mission_id,
            "owner_id": self.owner_id,
            "title": (title or objective[:120]).strip(),
            "description": objective.strip(),
            "objective": objective.strip(),
            "domain": "affiliate_jobs" if any(word in objective.lower() for word in AFFILIATE_KEYWORDS) else "general",
            "orchestration_idempotency_key": idempotency_key,
            "orchestration_claim_token": None,
            "orchestration_lease_expires_at": None,
            "priority": priority,
            "urgency": urgency,
            "business_importance": business_importance,
            "status": scheduled_state,
            "scheduled_at": scheduled_at,
            "recurrence": recurrence,
            "dependencies": dependencies,
            "metadata": sanitize_payload(metadata or {}),
            "result": {},
            "attempt_count": 0,
            "max_attempts": self.max_mission_attempts,
            "next_retry_at": None,
            "created_at": _iso(_now()),
            "updated_at": _iso(_now()),
        }
        persisted = self._insert_mission(record)
        if not persisted.get("success"):
            if idempotency_key and self.client is not None:
                try:
                    response = self.client.table("missions").select("*").eq("owner_id", self.owner_id).eq("orchestration_idempotency_key", idempotency_key).limit(1).execute()
                    if response.data:
                        existing = self._normalize(response.data[0])
                        self._objective_keys[(self.owner_id, idempotency_key)] = existing["id"]
                        return {"success": True, "mission": existing, "idempotent": True}
                except Exception:
                    pass
            return persisted
        if idempotency_key:
            self._objective_keys[(self.owner_id, idempotency_key)] = mission_id
        self._event("mission_created", mission_id, {"domain": record["domain"], "status": scheduled_state})
        return {"success": True, "mission": self.get_mission(mission_id)}

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        if not mission_id:
            return None
        if self.client is not None:
            try:
                response = self.client.table("missions").select("*").eq("id", mission_id).eq("owner_id", self.owner_id).limit(1).execute()
                rows = response.data or []
                return self._normalize(rows[0]) if rows else None
            except Exception:
                return None
        record = self._memory_missions.get(mission_id)
        return dict(record) if record and record.get("owner_id") == self.owner_id else None

    def list_missions(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if self.client is not None:
            try:
                query = self.client.table("missions").select("*").eq("owner_id", self.owner_id)
                if status:
                    query = query.eq("status", status)
                response = query.order("created_at", desc=True).limit(limit).execute()
                return [self._normalize(row) for row in (response.data or [])]
            except Exception:
                return []
        rows = [dict(row) for row in self._memory_missions.values() if row.get("owner_id") == self.owner_id]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return sorted(rows, key=lambda row: row.get("created_at") or "", reverse=True)[:limit]

    def transition(self, mission_id: str, target: str, *, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
        target = target.lower()
        if target not in MISSION_STATES:
            return {"success": False, "error": "unsupported mission state"}
        mission = self.get_mission(mission_id)
        if not mission:
            return {"success": False, "error": "Mission not found"}
        current = str(mission.get("status") or "pending")
        if target != current and target not in ALLOWED_TRANSITIONS.get(current, set()):
            return {"success": False, "error": f"Invalid transition: {current} -> {target}"}
        updates = {"status": target, "updated_at": _iso(_now())}
        if result is not None:
            result_payload = sanitize_payload(result)
            if error is not None:
                result_payload = dict(result_payload)
                result_payload["error"] = error
            updates["result"] = result_payload
        elif error is not None:
            updates["result"] = {"error": error}
        updated = self._update_mission(mission_id, updates)
        if updated.get("success") and target != current:
            event_name = {
                "active": "mission_started", "paused": "mission_paused", "waiting_approval": "mission_waiting_approval",
                "waiting_human": "mission_waiting_human", "completed": "mission_completed", "failed": "mission_failed",
                "cancelled": "mission_cancelled", "retrying": "mission_retry", "scheduled": "mission_scheduled",
            }.get(target, "mission_state_changed")
            self._event(event_name, mission_id, {"from": current, "to": target, "error": error})
        return updated

    def pause(self, mission_id: str) -> dict[str, Any]:
        return self.transition(mission_id, "paused")

    def resume(self, mission_id: str) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if not mission:
            return {"success": False, "error": "Mission not found"}
        target = "scheduled" if mission.get("scheduled_at") and not self._is_due(mission.get("scheduled_at")) else "pending"
        return self.transition(mission_id, target)

    def schedule(self, mission_id: str, scheduled_at: str, recurrence: str | None = None) -> dict[str, Any]:
        if not scheduled_at:
            return {"success": False, "error": "scheduled_at is required"}
        try:
            datetime.fromisoformat(str(scheduled_at).replace("Z", "+00:00"))
        except ValueError:
            return {"success": False, "error": "scheduled_at must be ISO-8601"}
        mission = self.get_mission(mission_id)
        if not mission:
            return {"success": False, "error": "Mission not found"}
        updated = self._update_mission(mission_id, {
            "scheduled_at": scheduled_at,
            "recurrence": recurrence,
            "next_retry_at": None,
        })
        if not updated.get("success"):
            return updated
        return self.transition(mission_id, "scheduled")

    def cancel(self, mission_id: str) -> dict[str, Any]:
        return self.transition(mission_id, "cancelled")

    # ------------------------------------------------------------------
    # Deterministic selection and scheduling
    # ------------------------------------------------------------------
    def readiness(self, mission: dict[str, Any]) -> dict[str, Any]:
        status = str(mission.get("status") or "pending")
        if status not in READY_STATES and status != "waiting_dependency":
            return {"ready": False, "reason": f"status_{status}"}
        scheduled_at = mission.get("scheduled_at")
        if scheduled_at and not self._is_due(scheduled_at):
            return {"ready": False, "reason": "scheduled_for_later", "scheduled_at": scheduled_at}
        retry_at = mission.get("next_retry_at")
        if retry_at and not self._is_due(retry_at):
            return {"ready": False, "reason": "retry_delay", "scheduled_at": retry_at}
        blocked = []
        for dependency_id in mission.get("dependencies") or []:
            dependency = self.get_mission(dependency_id)
            if not dependency or dependency.get("status") != "completed":
                blocked.append(dependency_id)
        if blocked:
            return {"ready": False, "reason": "dependencies", "blocked_by": blocked}
        return {"ready": True, "reason": "ready"}

    def priority_reason(self, mission: dict[str, Any]) -> dict[str, Any]:
        readiness = self.readiness(mission)
        age_hours = max(0.0, (_now() - self._parse_time(mission.get("created_at"))).total_seconds() / 3600)
        due_bonus = 20 if mission.get("scheduled_at") and self._is_due(mission.get("scheduled_at")) else 0
        retry_bonus = 5 if mission.get("status") == "retrying" else 0
        score = (
            PRIORITY_RANK.get(str(mission.get("priority") or "normal"), 2) * 100
            + URGENCY_RANK.get(str(mission.get("urgency") or "normal"), 2) * 30
            + int(mission.get("business_importance") or 1) * 10
            + min(int(age_hours), 24)
            + due_bonus + retry_bonus
        )
        return {
            "priority_score": score,
            "priority": mission.get("priority"),
            "urgency": mission.get("urgency"),
            "business_importance": mission.get("business_importance", 1),
            "readiness": readiness["reason"],
            "blocking_reason": readiness.get("blocked_by") or (None if readiness["ready"] else readiness["reason"]),
            "scheduled_at": mission.get("scheduled_at"),
            "age_hours": round(age_hours, 3),
        }

    def select_next(self) -> dict[str, Any]:
        candidates = []
        for mission in self.list_missions(limit=100):
            reason = self.priority_reason(mission)
            if reason["blocking_reason"] and reason["readiness"] == "dependencies" and mission.get("status") in {"pending", "retrying"}:
                self.transition(mission["id"], "waiting_dependency")
            if reason["blocking_reason"] is None:
                candidates.append((reason["priority_score"], mission.get("created_at") or "", mission, reason))
        if not candidates:
            return {"success": True, "mission": None, "reason": "no_ready_mission"}
        _, _, mission, reason = max(candidates, key=lambda item: (item[0], item[1]))
        self._event("mission_selected", mission["id"], reason)
        return {"success": True, "mission": mission, "reason": reason}

    # ------------------------------------------------------------------
    # Execution, reporting, and events
    # ------------------------------------------------------------------
    def run_next(self) -> dict[str, Any]:
        selected = self.select_next()
        if not selected.get("mission"):
            return selected
        return self.run_mission(selected["mission"]["id"], selection_reason=selected["reason"])

    def run_mission(self, mission_id: str, *, selection_reason: dict[str, Any] | None = None) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if not mission:
            return {"success": False, "error": "Mission not found"}
        if mission.get("status") in TERMINAL_STATES:
            return {
                "success": mission.get("status") == "completed",
                "status": str(mission.get("status")).upper(),
                "mission": mission,
                "reason": "terminal_mission_not_reexecuted",
            }
        ready = self.readiness(mission)
        if not ready["ready"]:
            return {"success": False, "status": "WAITING", "mission": mission, "reason": ready}
        if int(mission.get("attempt_count") or 0) >= int(mission.get("max_attempts") or self.max_mission_attempts):
            self.transition(mission_id, "failed", error="Mission attempt limit exceeded")
            return {"success": False, "status": "FAIL", "error": "Mission attempt limit exceeded"}
        claim_token = str(uuid4())
        claimed = self._claim_mission(mission, claim_token)
        if not claimed:
            return {"success": False, "status": "RETRY", "mission": self.get_mission(mission_id), "reason": "mission_already_claimed"}
        self._update_mission(mission_id, {"attempt_count": int(mission.get("attempt_count") or 0) + 1})
        started = self.transition(mission_id, "active")
        if not started.get("success"):
            return started
        employee = self._employee()
        try:
            result = employee.run(mission.get("objective") or mission.get("description") or mission.get("title") or "", mission_id=mission_id, metadata=mission.get("metadata") or {})
        except Exception as exc:
            result = {"success": False, "status": "FAIL", "error": f"Employee execution failed: {exc}"}
        status = result.get("status")
        if status == "COMPLETE":
            self.transition(mission_id, "completed", result=result)
        elif status == "WAIT_FOR_APPROVAL":
            self.transition(mission_id, "waiting_approval", result=result)
        elif status in {"WAIT_FOR_HUMAN_INPUT", "WAITING_INPUT"}:
            self.transition(mission_id, "waiting_human", result=result)
        elif status == "RETRY":
            retry_seconds = int((mission.get("metadata") or {}).get("retry_after_seconds", 60))
            retry_seconds = max(1, min(retry_seconds, 3600))
            self._update_mission(mission_id, {"next_retry_at": _iso(_now() + timedelta(seconds=retry_seconds))})
            self.transition(mission_id, "retrying", result=result)
        elif status == "FAIL":
            self.transition(mission_id, "failed", result=result, error=result.get("error") or "Mission failed")
        self._release_mission(mission_id, claim_token)
        return {"success": bool(result.get("success")), "mission": self.get_mission(mission_id), "execution": result, "selection_reason": selection_reason}

    def report(self) -> dict[str, Any]:
        missions = self.list_missions(limit=100)
        completed = [m for m in missions if m.get("status") == "completed"]
        waiting = [m for m in missions if m.get("status") in {"waiting_approval", "waiting_human", "scheduled", "waiting_dependency", "paused"}]
        failed = [m for m in missions if m.get("status") == "failed"]
        active = [m for m in missions if m.get("status") in {"active", "retrying"}]
        next_job = self.select_next()
        return {
            "owner_id": self.owner_id,
            "completed": completed,
            "in_progress": active,
            "waiting": waiting,
            "failed": failed,
            "next": next_job,
            "events": self.events(limit=25),
        }

    def events(self, *, mission_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if self.client is not None:
            try:
                query = self.client.table("mission_orchestration_events").select("*").eq("owner_id", self.owner_id)
                if mission_id:
                    query = query.eq("mission_id", mission_id)
                response = query.order("created_at", desc=True).limit(limit).execute()
                return [sanitize_payload(row) for row in (response.data or [])]
            except Exception:
                return []
        rows = [row for row in self._memory_events if row.get("owner_id") == self.owner_id and (not mission_id or row.get("mission_id") == mission_id)]
        return [sanitize_payload(row) for row in rows[-limit:][::-1]]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _employee(self) -> EmployeeVerticalSlice:
        if self.employee is None:
            self.employee = self.employee_factory(self.owner_id, self.client) if self.employee_factory else EmployeeVerticalSlice(self.owner_id, client=self.client)
        return self.employee

    def _insert_mission(self, record: dict[str, Any]) -> dict[str, Any]:
        if self.client is not None:
            try:
                payload = {key: value for key, value in record.items() if key != "id"}
                payload["id"] = record["id"]
                response = self.client.table("missions").insert(payload).execute()
                if response.data:
                    return {"success": True, "mission": self._normalize(response.data[0])}
                return {"success": False, "error": "Mission persistence failed"}
            except Exception as exc:
                return {"success": False, "error": f"Mission persistence failed: {exc}"}
        self._memory_missions[record["id"]] = dict(record)
        return {"success": True, "mission": record}

    def _claim_mission(self, mission: dict[str, Any], claim_token: str) -> bool:
        now = _now()
        expiry = self._parse_time(mission.get("orchestration_lease_expires_at")) if mission.get("orchestration_lease_expires_at") else None
        if expiry and expiry > now and mission.get("orchestration_claim_token"):
            return False
        updates = {
            "orchestration_claim_token": claim_token,
            "orchestration_lease_expires_at": _iso(now + timedelta(seconds=ORCHESTRATION_LEASE_SECONDS)),
        }
        if self.client is not None:
            try:
                response = self.client.rpc(
                    "claim_mission_for_orchestration",
                    {"p_mission_id": mission["id"], "p_claim_token": claim_token, "p_lease_seconds": ORCHESTRATION_LEASE_SECONDS},
                ).execute()
                row = (getattr(response, "data", None) or [{}])[0]
                return bool(row.get("claimed"))
            except Exception:
                return False
        with _MISSION_LOCK:
            current = self._memory_missions.get(mission["id"])
            if not current or current.get("owner_id") != self.owner_id:
                return False
            if current.get("status") in TERMINAL_STATES:
                return False
            current_expiry = self._parse_time(current.get("orchestration_lease_expires_at")) if current.get("orchestration_lease_expires_at") else None
            if current_expiry and current_expiry > now and current.get("orchestration_claim_token"):
                return False
            current.update(updates)
            return True

    def _release_mission(self, mission_id: str, claim_token: str) -> None:
        if self.client is not None:
            try:
                self.client.table("missions").update({
                    "orchestration_claim_token": None,
                    "orchestration_lease_expires_at": None,
                }).eq("id", mission_id).eq("owner_id", self.owner_id).eq("orchestration_claim_token", claim_token).execute()
            except Exception:
                pass
            return
        mission = self.get_mission(mission_id)
        if not mission or mission.get("orchestration_claim_token") != claim_token:
            return
        self._update_mission(mission_id, {
            "orchestration_claim_token": None,
            "orchestration_lease_expires_at": None,
        })

    def _update_mission(self, mission_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        if self.client is not None:
            try:
                response = self.client.table("missions").update(sanitize_payload(updates)).eq("id", mission_id).eq("owner_id", self.owner_id).execute()
                if response.data:
                    return {"success": True, "mission": self._normalize(response.data[0])}
                return {"success": False, "error": "Mission not found"}
            except Exception as exc:
                return {"success": False, "error": f"Mission update failed: {exc}"}
        record = self._memory_missions.get(mission_id)
        if not record or record.get("owner_id") != self.owner_id:
            return {"success": False, "error": "Mission not found"}
        record.update(sanitize_payload(updates))
        return {"success": True, "mission": dict(record)}

    def _event(self, event_type: str, mission_id: str, payload: dict[str, Any]) -> None:
        event = sanitize_payload({"id": str(uuid4()), "owner_id": self.owner_id, "mission_id": mission_id, "event_type": event_type, "payload": payload, "created_at": _iso(_now())})
        if self.client is not None:
            try:
                self.client.table("mission_orchestration_events").insert(event).execute()
            except Exception:
                return
        else:
            self._memory_events.append(event)
        try:
            mission = self.get_mission(mission_id)
            worker_id = mission.get("worker_id") if mission else None
            if worker_id:
                self.memory.store_memory(worker_id, "mission_event", {"mission_id": mission_id, "event_type": event_type, "payload": payload}, owner_id=self.owner_id)
        except Exception:
            pass

    @staticmethod
    def _normalize(row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized["dependencies"] = row.get("dependencies") or []
        normalized["metadata"] = row.get("metadata") or {}
        normalized["result"] = row.get("result") or {}
        return normalized

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        if not value:
            return _now()
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return _now()

    def _is_due(self, value: Any) -> bool:
        return self._parse_time(value) <= _now()


__all__ = ["ALLOWED_TRANSITIONS", "MISSION_STATES", "MissionOrchestrationService"]