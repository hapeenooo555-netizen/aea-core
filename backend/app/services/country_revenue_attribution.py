"""Owner-scoped, privacy-minimized content-to-revenue event validation."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from app.services.country_intelligence import MARKETS

EVENT_TYPES = {
    "content_published", "impression_or_view", "tracked_click", "lead_captured",
    "lead_qualified", "checkout_started_or_quote_requested", "purchase_confirmed",
    "payment_settled",
}
ALLOWED_CHANNELS = {"facebook", "instagram", "whatsapp", "youtube", "pinterest", "tiktok", "website", "email", "other"}
ALLOWED_METADATA_KEYS = {"source", "provider_event_id", "landing_page", "experiment_variant", "notes_code"}
METADATA_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/?&=%+\\-]{0,119}$")


def validate_revenue_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate event dimensions; never accept direct identifiers."""
    code = str(payload.get("country_code", "")).upper()
    if code not in MARKETS:
        raise ValueError("Unsupported country_code.")
    event_type = payload.get("event_type")
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported event_type.")
    channel = str(payload.get("channel", "")).lower()
    if channel not in ALLOWED_CHANNELS:
        raise ValueError("Unsupported channel.")
    currency = payload.get("currency")
    expected_currency = MARKETS[code]["currency"]
    if currency != expected_currency:
        raise ValueError(f"Currency for {code} must be {expected_currency}.")
    campaign_id = str(payload.get("campaign_id", "")).strip()
    if not campaign_id or len(campaign_id) > 120:
        raise ValueError("campaign_id is required and must be at most 120 characters.")
    content_id = payload.get("content_id")
    if content_id is not None and (not str(content_id).strip() or len(str(content_id)) > 120):
        raise ValueError("content_id must be a non-empty identifier of at most 120 characters.")
    key = str(payload.get("idempotency_key", "")).strip()
    if len(key) < 16 or len(key) > 128:
        raise ValueError("idempotency_key must contain 16 to 128 characters.")
    occurred_at = payload.get("occurred_at")
    if isinstance(occurred_at, str):
        try:
            occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO-8601 timestamp.") from exc
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
        raise ValueError("occurred_at must include a timezone.")
    if occurred_at > datetime.now(timezone.utc):
        raise ValueError("occurred_at cannot be in the future.")
    amount = payload.get("amount_minor")
    if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool) or amount < 0):
        raise ValueError("amount_minor must be a non-negative integer.")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict) or len(metadata) > 5 or set(metadata) - ALLOWED_METADATA_KEYS:
        raise ValueError("metadata contains too many or unsupported keys; do not submit personal data.")
    for value in metadata.values():
        if not isinstance(value, str) or not METADATA_VALUE_PATTERN.fullmatch(value):
            raise ValueError("metadata values must be short identifiers or URL-safe tokens, not free text or personal data.")
    if event_type not in {"purchase_confirmed", "payment_settled"} and amount is not None:
        raise ValueError("amount_minor is only valid for purchase or settled payment events.")
    if event_type == "payment_settled" and amount is None:
        raise ValueError("payment_settled requires amount_minor.")
    return {
        "idempotency_key": key,
        "event_type": event_type,
        "country_code": code,
        "currency": currency,
        "campaign_id": campaign_id,
        "content_id": str(content_id).strip() if content_id is not None else None,
        "channel": channel,
        "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
        "amount_minor": amount,
        "metadata": metadata,
    }
