"""Deterministic AI-ready lead qualification for HAPE BROTHERS.

The first production vertical slice is intentionally deterministic: it extracts
commercial signals from inbound messages without requiring an external LLM.
The resulting structured profile can later be enriched by an LLM adapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Qualification:
    quantity: int | None
    gauge: str | None
    product: str | None
    location: str | None
    urgency: str
    score: int
    tier: str
    missing_information: list[str]
    next_action: str


class LeadQualificationService:
    """Extract and score buying intent using auditable rules."""

    LOCATION_RE = re.compile(
        r"\b(?:in|at|from|to)\s+([A-Za-z][A-Za-z .'-]{1,60})",
        re.IGNORECASE,
    )
    QUANTITY_RE = re.compile(
        r"\b(\d[\d,]*)\s*(?:pcs?|pieces?|sheets?|bati|mabati)\b",
        re.IGNORECASE,
    )
    GAUGE_RE = re.compile(r"\b(?:gauge|ga|g)\s*[-:]?\s*(\d{1,2})\b", re.IGNORECASE)

    URGENT_TERMS = ("today", "now", "asap", "urgent", "haraka", "leo", "kesho")
    PURCHASE_TERMS = (
        "buy", "order", "need", "price", "quote", "purchase",
        "nunua", "nahitaji", "bei", "oda", "agiza",
    )

    def qualify(self, *, message: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        text = (message or "").strip()
        existing = existing or {}

        quantity = self._quantity(text) or self._as_int(existing.get("quantity"))
        gauge = self._gauge(text) or self._as_text(existing.get("gauge"))
        location = self._location(text) or self._as_text(existing.get("location"))
        product = self._product(text) or self._as_text(existing.get("product"))

        lower = text.lower()
        urgency = "urgent" if any(term in lower for term in self.URGENT_TERMS) else "normal"

        score = 0
        if quantity:
            score += 30 if quantity >= 500 else 20 if quantity >= 100 else 10
        if gauge:
            score += 15
        if location:
            score += 15
        if product:
            score += 10
        if any(term in lower for term in self.PURCHASE_TERMS):
            score += 20
        if urgency == "urgent":
            score += 10
        score = min(score, 100)

        missing: list[str] = []
        if not quantity:
            missing.append("quantity")
        if not gauge:
            missing.append("gauge")
        if not location:
            missing.append("delivery_location")

        tier = "hot" if score >= 70 else "warm" if score >= 40 else "cold"
        next_action = "send_quote" if tier == "hot" and not missing else (
            "ask_missing_information" if missing else "follow_up"
        )

        return {
            "success": True,
            "qualification": asdict(Qualification(
                quantity=quantity,
                gauge=gauge,
                product=product,
                location=location,
                urgency=urgency,
                score=score,
                tier=tier,
                missing_information=missing,
                next_action=next_action,
            )),
        }

    def _quantity(self, text: str) -> int | None:
        match = self.QUANTITY_RE.search(text)
        return int(match.group(1).replace(",", "")) if match else None

    def _gauge(self, text: str) -> str | None:
        match = self.GAUGE_RE.search(text)
        return match.group(1) if match else None

    def _location(self, text: str) -> str | None:
        match = self.LOCATION_RE.search(text)
        return match.group(1).strip(" .,") if match else None

    @staticmethod
    def _product(text: str) -> str | None:
        lower = text.lower()
        if "roofing" in lower or "mabati" in lower or "bati" in lower:
            return "metal_roofing_sheet"
        return None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_text(value: Any) -> str | None:
        value = str(value).strip() if value is not None else ""
        return value or None
