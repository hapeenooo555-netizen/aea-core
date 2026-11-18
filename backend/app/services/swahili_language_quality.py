"""Deterministic Tanzanian Swahili terminology and language-quality guardrails.

This is a conservative terminology pass, not an automatic translator. It only
replaces known awkward or misleading terms and leaves unknown wording intact.
"""
from __future__ import annotations

import re
from typing import Any

# Match whole phrases/words case-insensitively; preserve the surrounding text.
_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bspecifikesheni\b", re.IGNORECASE), "maelezo"),
    (re.compile(r"\bdesign specification(s)?\b", re.IGNORECASE), "mwongozo wa ubunifu"),
    (re.compile(r"\bcreative direction\b", re.IGNORECASE), "mwelekeo wa ubunifu"),
    (re.compile(r"\bexport specifications\b", re.IGNORECASE), "vipimo vya faili la mwisho"),
    (re.compile(r"\bvisual hierarchy\b", re.IGNORECASE), "mpangilio wa vipaumbele vya kuona"),
    (re.compile(r"\bcall to action\b", re.IGNORECASE), "wito wa kuchukua hatua"),
    (re.compile(r"\blayout\b", re.IGNORECASE), "mpangilio"),
    (re.compile(r"\bapplication\b", re.IGNORECASE), "programu tumizi"),
)

def normalize_swahili_text(value: str) -> str:
    """Apply conservative known-term fixes without translating arbitrary text."""
    result = value
    for pattern, replacement in _REPLACEMENTS:
        result = pattern.sub(replacement, result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result

def apply_swahili_language_quality(value: Any) -> Any:
    """Recursively normalize known terms in structured model output."""
    if isinstance(value, str):
        return normalize_swahili_text(value)
    if isinstance(value, list):
        return [apply_swahili_language_quality(item) for item in value]
    if isinstance(value, tuple):
        return tuple(apply_swahili_language_quality(item) for item in value)
    if isinstance(value, dict):
        return {key: apply_swahili_language_quality(item) for key, item in value.items()}
    return value
