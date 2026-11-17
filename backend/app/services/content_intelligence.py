"""Deterministic quality guardrails for shareability and hashtag suggestions.

This module does not claim to measure live trends. It organizes AI-proposed
hashtags by relevance and labels trend status as unverified until a trusted
trend-data provider is integrated.
"""
from __future__ import annotations

import re
from typing import Any

_GENERIC_TAGS = {"#viral", "#fyp", "#trending", "#explorepage", "#follow"}
_STOP_WORDS = {
    "about", "with", "from", "this", "that", "your", "kwa", "katika",
    "yako", "yetu", "sana", "zaidi", "kuhusu", "kwa ajili ya",
}


def _clean_tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            continue
        words = re.findall(r"[\w]+", raw.strip().lstrip("#"), flags=re.UNICODE)
        if not words:
            continue
        tag = "#" + "".join(words[:5])
        key = tag.casefold()
        if key in seen or key in _GENERIC_TAGS:
            continue
        seen.add(key)
        result.append(tag)
    return result[:15]


def _tokens(value: str) -> list[str]:
    return [
        word for word in re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE)
        if len(word) >= 4 and word not in _STOP_WORDS
    ]


def enrich_content_intelligence(draft: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    """Normalize hashtag suggestions and attach transparent strategy metadata."""
    tags = _clean_tags(draft.get("hashtags"))
    draft["hashtags"] = tags

    niche = str(brief.get("niche") or "")
    audience = str(brief.get("audience") or "")
    prompt = str(brief.get("prompt") or "")
    title = str(draft.get("title") or "")
    location_terms = ("tanzania", "tz", "dar es salaam", "arusha", "mwanza", "dodoma", "zanzibar")
    context = " ".join((niche, audience, prompt, title)).casefold()
    location_relevant = any(term in context for term in location_terms)
    brand_tags = [tag for tag in tags if any(token in tag.casefold() for token in _tokens(title)[:2])]
    niche_tags = [tag for tag in tags if tag not in brand_tags][:6]
    audience_tags = [tag for tag in tags if any(token in tag.casefold() for token in _tokens(audience))][:4]

    strategy = draft.get("hashtag_strategy")
    if not isinstance(strategy, dict):
        strategy = {}
    strategy.update({
        "rationale": strategy.get("rationale") or "Hashtags are selected for topic, audience and offer relevance, not assumed popularity.",
        "groups": {
            "niche": strategy.get("groups", {}).get("niche", niche_tags) if isinstance(strategy.get("groups"), dict) else niche_tags,
            "audience": strategy.get("groups", {}).get("audience", audience_tags) if isinstance(strategy.get("groups"), dict) else audience_tags,
            "brand": strategy.get("groups", {}).get("brand", brand_tags) if isinstance(strategy.get("groups"), dict) else brand_tags,
            "location": strategy.get("groups", {}).get("location", []) if isinstance(strategy.get("groups"), dict) else [],
        },
        "trend_status": "unverified_no_live_trend_source",
        "data_limitations": "No live hashtag volume, ranking, velocity or trend provider was queried. Suggestions are relevance-based only.",
    })
    if not location_relevant and isinstance(strategy["groups"].get("location"), list):
        strategy["groups"]["location"] = []
    draft["hashtag_strategy"] = strategy

    if not draft.get("shareability_angle"):
        draft["shareability_angle"] = "Lead with a specific audience need, deliver a practical takeaway, and give a natural reason to save or share."
    if not draft.get("audience_trigger"):
        draft["audience_trigger"] = "Audience relevance inferred from the brief; validate with real post performance."
    if not draft.get("hook_rationale"):
        draft["hook_rationale"] = "Hook quality is a creative recommendation, not a prediction of reach or virality."
    notes = draft.get("research_notes")
    if not isinstance(notes, list):
        notes = [str(notes)] if notes else []
    note = "No live trend source was queried; trend and hashtag popularity are unverified."
    if note not in notes:
        notes.append(note)
    draft["research_notes"] = notes
    return draft
