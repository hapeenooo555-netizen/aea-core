from datetime import datetime, timezone, timedelta

import pytest

from app.services.country_revenue_attribution import validate_revenue_event


def _event(**overrides):
    payload = {
        "idempotency_key": "event-key-00000001",
        "event_type": "payment_settled",
        "country_code": "TZ",
        "currency": "TZS",
        "campaign_id": "aea-launch-tz",
        "content_id": "video-001",
        "channel": "facebook",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "amount_minor": 1500000,
        "metadata": {"source": "manual_verified"},
    }
    payload.update(overrides)
    return payload


def test_valid_tanzania_settled_payment_event_is_normalized():
    event = validate_revenue_event(_event())
    assert event["country_code"] == "TZ"
    assert event["currency"] == "TZS"
    assert event["amount_minor"] == 1500000
    assert event["event_type"] == "payment_settled"


@pytest.mark.parametrize(("country", "currency"), [
    ("KE", "TZS"), ("NG", "USD"), ("ZA", "GBP"), ("US", "ZAR"), ("UK", "KES"),
])
def test_rejects_currency_mismatch(country, currency):
    with pytest.raises(ValueError, match="Currency"):
        validate_revenue_event(_event(country_code=country, currency=currency))


def test_rejects_unknown_market_and_event():
    with pytest.raises(ValueError, match="country_code"):
        validate_revenue_event(_event(country_code="XX"))
    with pytest.raises(ValueError, match="event_type"):
        validate_revenue_event(_event(event_type="refund_requested"))


def test_rejects_naive_or_future_timestamps():
    with pytest.raises(ValueError, match="timezone"):
        validate_revenue_event(_event(occurred_at="2026-11-18T10:00:00"))
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        validate_revenue_event(_event(occurred_at=future))


def test_rejects_personal_data_in_metadata():
    with pytest.raises(ValueError, match="metadata"):
        validate_revenue_event(_event(metadata={"email": "customer@example.com"}))


def test_only_purchase_or_settlement_events_accept_amount():
    with pytest.raises(ValueError, match="only valid"):
        validate_revenue_event(_event(event_type="tracked_click", amount_minor=100))
    with pytest.raises(ValueError, match="requires amount_minor"):
        validate_revenue_event(_event(amount_minor=None))


def test_rejects_free_text_and_email_like_metadata_values():
    with pytest.raises(ValueError, match="identifiers or URL-safe"):
        validate_revenue_event(_event(metadata={"source": "Customer called and gave details"}))
    with pytest.raises(ValueError, match="identifiers or URL-safe"):
        validate_revenue_event(_event(metadata={"source": "customer@example.com"}))


def test_rejects_more_than_five_metadata_fields():
    with pytest.raises(ValueError, match="too many"):
        validate_revenue_event(_event(metadata={
            "source": "manual", "provider_event_id": "evt_123", "landing_page": "https://example.test/a",
            "experiment_variant": "variant_a", "notes_code": "note_01", "extra": "x"
        }))
