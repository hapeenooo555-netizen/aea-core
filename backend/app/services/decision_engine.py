"""Rule-based decision engine for Atlas mission planning.

This module provides a lightweight service layer for choosing workers,
prioritizing queued missions, and determining the next action based on
mission metadata. The implementation is intentionally deterministic and
contains no LLM or external inference logic.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from .llm_provider import BaseLLMProvider, get_llm_provider
from .memory_engine import AtlasMemoryEngine


class AtlasDecisionEngine:
    """Apply simple rule-based heuristics to mission planning tasks.

    The engine is designed to support the Sprint 3 Atlas workflow with a
    minimal, dependency-free decision layer. It can select a worker for a
    mission, prioritize a collection of missions, and propose the next action
    to take.
    """

    def __init__(
        self,
        provider: BaseLLMProvider | None = None,
        memory_engine: AtlasMemoryEngine | None = None,
        owner_id: str | None = None,
    ) -> None:
        """Initialize the engine with the built-in worker routing rules.

        Args:
            provider: Optional LLM provider for decision generation.
            memory_engine: Optional AtlasMemoryEngine instance.
            owner_id: Canonical owner identifier (auth.users.id). When provided,
                memory operations will be scoped to this owner for RLS enforcement.
        """

        self._provider = provider or get_llm_provider()
        self._memory_engine = memory_engine or AtlasMemoryEngine()
        self._owner_id = owner_id
        self._memory_context_limit = int(os.getenv("MEMORY_CONTEXT_LIMIT", "5") or "5")
        self._worker_rules: dict[str, str] = {
            "pinterest": "Pinterest Worker",
            "digistore": "Digistore Worker",
            "research": "Research Worker",
            "content": "Content Worker",
            "analytics": "Analytics Worker",
        }

    def select_worker(self, mission: dict[str, Any] | None) -> str | None:
        """Select a worker name for the provided mission payload.

        Args:
            mission: A mission-like dictionary that may contain a worker type,
                assigned worker, or a title/description hint.

        Returns:
            A worker name when a supported rule matches, otherwise ``None``.
        """

        if not mission:
            return None

        worker_type = self._extract_worker_type(mission)
        if not worker_type:
            return None

        normalized_type = worker_type.lower()
        return self._worker_rules.get(normalized_type)

    def prioritize_missions(self, missions: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Return missions sorted by priority score.

        Missions with higher urgency are ordered first. The scoring rules are:
        - missions with an explicit priority value are ranked first by a
          predefined order: ``high`` > ``medium`` > ``normal`` > ``low``.
        - missions with a non-empty ``assigned_worker`` receive a small boost.
        - missions with a non-zero ``progress`` are treated as more active.

        Args:
            missions: An iterable of mission dictionaries.

        Returns:
            A new list of missions sorted from highest to lowest priority.
        """

        if not missions:
            return []

        def _priority_score(mission: dict[str, Any]) -> tuple[int, int, int, int]:
            priority_value = str(mission.get("priority") or "normal").lower()
            priority_rank = {
                "high": 3,
                "medium": 2,
                "normal": 1,
                "low": 0,
            }.get(priority_value, 1)

            assigned_boost = 1 if mission.get("assigned_worker") else 0
            progress_value = int(mission.get("progress") or 0)
            return (-priority_rank, -assigned_boost, -progress_value, 0)

        return sorted(missions, key=_priority_score)

    def next_action(self) -> str:
        """Return a simple default next action for the current engine state.

        Returns:
            A deterministic action string that can be used by downstream code.
        """

        decision = self.next_action_details()
        return str(decision.get("action") or decision.get("content") or "Review pending missions")

    def next_action_details(self, worker_id: str | None = None, mission: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a normalized decision payload for downstream callers.

        The method preserves the historical ``next_action`` string contract while
        exposing a stable structured response that is provider-agnostic.
        """

        context = self._build_decision_context(worker_id=worker_id, mission=mission)
        decision_prompt = self._build_prompt(context)

        try:
            response = self._provider.generate(
                decision_prompt,
                system_prompt="You are an assistant that returns a concise next action.",
            )
            normalized = self._normalize_provider_response(response)
            normalized["context"] = context
            if normalized.get("success") and normalized.get("action"):
                self._save_decision_memory(worker_id, mission, normalized)
                return normalized
            if normalized.get("status") == "MOCK":
                normalized["status"] = "MOCK"
                normalized["success"] = False
                normalized["error"] = normalized.get("error") or "Mock output cannot be treated as a verified decision"
                return normalized
        except Exception:  # pragma: no cover - defensive runtime handling
            pass

        try:
            fallback = self._fallback_decision()
            if self._provider.__class__.__name__.lower() != "mockllmprovider":
                fallback["provider"] = "mock"
                fallback["model"] = "mock"
                fallback["content"] = fallback["action"]
            fallback["context"] = context
            self._save_decision_memory(worker_id, mission, fallback)
            return fallback
        except Exception:  # pragma: no cover - defensive runtime handling
            fallback = self._fallback_decision()
            fallback["context"] = context
            return fallback

    def _normalize_provider_response(self, response: Any) -> dict[str, Any]:
        """Normalize provider output into a stable decision contract."""

        if not isinstance(response, dict):
            return self._fallback_decision()

        content = str(response.get("content") or "").strip()
        action = str(response.get("action") or content or "").strip()
        provider_name = str(response.get("provider") or self._provider.__class__.__name__.lower()).strip()
        is_mock = provider_name.strip().lower() == "mock" or content.lower().startswith("mock response:")
        provider_success = bool(response.get("success", bool(action)))
        return {
            "success": provider_success and bool(action) and not is_mock,
            "status": "MOCK" if is_mock else ("READY" if provider_success and action else "FAILED"),
            "action": action or "Review pending missions",
            "content": action or content,
            "provider": provider_name,
            "model": response.get("model") or "unknown",
            "usage": response.get("usage") or {},
            "error": "Mock provider output is not a verified AI decision" if is_mock else response.get("error"),
        }

    def _build_decision_context(self, worker_id: str | None = None, mission: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a normalized context payload for provider prompts and downstream consumers."""

        recent_memories = []
        if worker_id:
            try:
                recent_memories = self._memory_engine.get_recent_memories(worker_id, limit=self._memory_context_limit) or []
            except Exception:  # pragma: no cover - defensive runtime handling
                recent_memories = []

        normalized_mission = dict(mission or {})
        metadata = normalized_mission.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        normalized_mission["metadata"] = metadata

        return {
            "worker_id": worker_id,
            "mission": normalized_mission,
            "recent_memories": recent_memories,
            "mission_metadata": metadata,
            "memory_context_limit": self._memory_context_limit,
        }

    def _build_prompt(self, context: dict[str, Any]) -> str:
        """Create a prompt that incorporates the worker context and recent memories."""

        mission = context.get("mission") or {}
        title = mission.get("title") or "Untitled mission"
        description = mission.get("description") or ""
        memories = context.get("recent_memories") or []

        memory_lines = []
        for idx, memory in enumerate(memories, start=1):
            content = memory.get("content") if isinstance(memory, dict) else None
            text = content.get("text") if isinstance(content, dict) else None
            if text:
                memory_lines.append(f"{idx}. {text}")
            elif memory:
                memory_lines.append(f"{idx}. {memory}")

        memory_block = "\n".join(memory_lines) if memory_lines else "None"
        return (
            f"Mission: {title}\n"
            f"Description: {description}\n"
            f"Recent memories:\n{memory_block}\n"
            "Suggest the next action for this mission planning workflow."
        )

    def _save_decision_memory(self, worker_id: str | None, mission: dict[str, Any] | None, decision: dict[str, Any]) -> None:
        """Persist a lightweight decision summary for the worker via the memory adapter."""

        if not worker_id or not self._memory_engine:
            return

        try:
            content = {
                "mission_id": mission.get("id") if isinstance(mission, dict) else None,
                "mission_title": mission.get("title") if isinstance(mission, dict) else None,
                "action": decision.get("action"),
                "reasoning": decision.get("reasoning"),
                "decision": decision.get("content") or decision.get("action"),
                "timestamp": decision.get("timestamp") or "now",
            }
            self._memory_engine.store_memory(worker_id, "decision", content, owner_id=self._owner_id)
        except Exception:  # pragma: no cover - defensive runtime handling
            pass

    def _fallback_decision(self) -> dict[str, Any]:
        """Return a deterministic fallback response for unsupported providers."""

        provider_name = self._provider.__class__.__name__.lower()
        return {
            "success": False,
            "status": "FALLBACK",
            "action": "Review pending missions",
            "content": "Decision unavailable. Review pending missions manually.",
            "provider": "fallback",
            "model": "none",
            "usage": {},
            "error": f"Decision provider unavailable or returned an invalid response ({provider_name})",
        }

    def _extract_worker_type(self, mission: dict[str, Any]) -> str | None:
        """Extract a worker-type hint from a mission payload.

        The method checks common keys in order of specificity: ``worker_type``,
        ``role``, ``worker_role``, and then the mission title/description.
        """

        for key in ("worker_type", "role", "worker_role"):
            value = mission.get(key)
            if isinstance(value, str) and value.strip():
                return value

        for key in ("title", "description"):
            value = mission.get(key)
            if isinstance(value, str):
                lowered = value.lower()
                if "pinterest" in lowered:
                    return "pinterest"
                if "digistore" in lowered:
                    return "digistore"
                if "research" in lowered:
                    return "research"
                if "content" in lowered:
                    return "content"
                if "analytics" in lowered:
                    return "analytics"

        return None
