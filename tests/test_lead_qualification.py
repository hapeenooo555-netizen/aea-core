from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.lead_qualification import LeadQualificationService


def test_qualifies_high_intent_roofing_lead() -> None:
    result = LeadQualificationService().qualify(
        message="Nahitaji bati 500 gauge 32 delivery Mwanza leo"
    )
    q = result["qualification"]
    assert q["quantity"] == 500
    assert q["gauge"] == "32"
    assert q["product"] == "metal_roofing_sheet"
    assert q["location"] == "Mwanza leo"
    assert q["urgency"] == "urgent"
    assert q["tier"] == "hot"
    assert q["next_action"] == "send_quote"


def test_missing_information_requires_follow_up() -> None:
    result = LeadQualificationService().qualify(message="Nahitaji bati 200")
    q = result["qualification"]
    assert q["quantity"] == 200
    assert "gauge" in q["missing_information"]
    assert "delivery_location" in q["missing_information"]
    assert q["next_action"] == "ask_missing_information"
