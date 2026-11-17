"""Country-aware content-to-revenue planning primitives.

Market profiles are configuration defaults, not measured audience or demand data.
All performance metrics must come from first-party campaign events or provider APIs.
"""
from __future__ import annotations

from typing import Any, Literal

MARKETS: dict[str, dict[str, Any]] = {
    "TZ": {"country": "Tanzania", "currency": "TZS", "languages": ["sw", "en"], "primary_channels": ["facebook", "instagram", "whatsapp", "youtube"], "localization_notes": "Swahili-first option; adapt examples, payment and delivery to the actual region."},
    "KE": {"country": "Kenya", "currency": "KES", "languages": ["en", "sw"], "primary_channels": ["facebook", "instagram", "whatsapp", "tiktok", "youtube"], "localization_notes": "Offer English and Swahili variants; validate local price and fulfilment."},
    "NG": {"country": "Nigeria", "currency": "NGN", "languages": ["en"], "primary_channels": ["facebook", "instagram", "whatsapp", "youtube"], "localization_notes": "Use English by default; validate state-level context, local payment and fulfilment."},
    "ZA": {"country": "South Africa", "currency": "ZAR", "languages": ["en"], "primary_channels": ["facebook", "instagram", "whatsapp", "youtube"], "localization_notes": "English default; localize by province and audience language where evidence supports it."},
    "US": {"country": "United States", "currency": "USD", "languages": ["en"], "primary_channels": ["instagram", "facebook", "youtube", "tiktok", "pinterest"], "localization_notes": "Use US spelling, local pricing and audience-specific proof; validate compliance and fulfilment."},
    "UK": {"country": "United Kingdom", "currency": "GBP", "languages": ["en"], "primary_channels": ["instagram", "facebook", "youtube", "pinterest"], "localization_notes": "Use UK spelling, local pricing and audience-specific proof; validate compliance and fulfilment."},
}

RevenueObjective = Literal["leads", "sales", "traffic"]


def list_country_markets() -> dict[str, Any]:
    """Return the configured launch markets and measurement contract."""
    return {
        "success": True,
        "engine": "country_intelligence",
        "markets": [{"country_code": code, **profile} for code, profile in MARKETS.items()],
        "measurement_principles": [
            "Separate results by country, currency, platform, campaign and reporting period.",
            "Treat audience and demand statements as hypotheses until supported by dated evidence.",
            "Use provider-reported delivery metrics and first-party consented conversion events.",
            "Do not infer sales from views, reach, likes or clicks.",
        ],
        "revenue_funnel": [
            {"stage": "content_published", "event": "content_published"},
            {"stage": "attention", "event": "impression_or_view"},
            {"stage": "qualified_traffic", "event": "tracked_click"},
            {"stage": "lead", "event": "lead_captured"},
            {"stage": "qualified_lead", "event": "lead_qualified"},
            {"stage": "checkout_or_quote", "event": "checkout_started_or_quote_requested"},
            {"stage": "sale", "event": "purchase_confirmed"},
            {"stage": "cash_received", "event": "payment_settled"},
        ],
    }


def build_country_revenue_plan(
    country_codes: list[str],
    offer: str,
    objective: RevenueObjective = "sales",
    channel: str | None = None,
) -> dict[str, Any]:
    """Create localized campaign hypotheses and measurable conversion paths."""
    if not offer.strip():
        raise ValueError("offer must not be empty")
    if not country_codes or len(country_codes) > 6:
        raise ValueError("select between one and six launch countries")
    normalized = list(dict.fromkeys(code.strip().upper() for code in country_codes))
    unknown = [code for code in normalized if code not in MARKETS]
    if unknown:
        raise ValueError(f"unsupported country codes: {', '.join(unknown)}")

    plans = []
    for code in normalized:
        market = MARKETS[code]
        channels = market["primary_channels"]
        selected_channel = channel if channel in channels else channels[0]
        conversion_event = {
            "leads": "lead_captured",
            "sales": "purchase_confirmed",
            "traffic": "tracked_click",
        }[objective]
        plans.append({
            "country_code": code,
            "country": market["country"],
            "currency": market["currency"],
            "language_options": market["languages"],
            "recommended_channels": channels,
            "selected_channel": selected_channel,
            "offer": offer.strip(),
            "objective": objective,
            "campaign_hypotheses": [
                "Identify one clearly defined buyer segment and validate its problem with customer evidence.",
                "Create two distinct, truthful hooks for the same offer and compare qualified actions.",
                "Use a country-specific landing or conversation destination with a tracked campaign identifier.",
            ],
            "content_localization": market["localization_notes"],
            "primary_conversion_event": conversion_event,
            "attribution_requirements": ["country_code", "campaign_id", "content_id", "channel", "event_timestamp", "currency"],
            "status": "planning_hypothesis_not_market_validation",
        })
    return {
        "success": True,
        "engine": "country_intelligence",
        "strategy": "content_to_revenue",
        "plans": plans,
        "guardrails": [
            "No audience size, demand, competitor strength, conversion rate or revenue forecast is asserted without evidence.",
            "Compare monetary results only after retaining each country's original currency and reporting period.",
            "Record lead consent and honor platform terms, privacy law and messaging rules.",
        ],
    }
