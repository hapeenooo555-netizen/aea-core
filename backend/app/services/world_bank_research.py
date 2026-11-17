"""Bounded, allowlisted World Bank indicator retrieval.

World Bank macro indicators provide country context, not direct proof of demand
for a specific product or offer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

_COUNTRIES = {
    "TZ": "TZA", "KE": "KEN", "UG": "UGA", "RW": "RWA", "ZA": "ZAF",
    "NG": "NGA", "GH": "GHA", "US": "USA", "GB": "GBR", "IN": "IND",
    "CA": "CAN", "AU": "AUS", "DE": "DEU", "AE": "ARE",
}
_INDICATORS = {
    "population": "SP.POP.TOTL",
    "internet_users_pct": "IT.NET.USER.ZS",
    "gdp_per_capita_usd": "NY.GDP.PCAP.CD",
}
_API_ROOT = "https://api.worldbank.org/v2"


async def fetch_world_bank_indicators(
    country_codes: list[str],
    indicators: list[str] | None = None,
    per_page: int = 5,
) -> dict[str, Any]:
    """Fetch recent observations for a bounded set of allowlisted countries/indicators."""
    normalized_countries = list(dict.fromkeys(code.strip().upper() for code in country_codes))
    if not normalized_countries or len(normalized_countries) > 5:
        raise ValueError("Provide between 1 and 5 country codes.")
    unknown_countries = [code for code in normalized_countries if code not in _COUNTRIES]
    if unknown_countries:
        raise ValueError("Unsupported country code(s): " + ", ".join(unknown_countries))
    requested_indicators = indicators or ["population", "internet_users_pct"]
    requested_indicators = list(dict.fromkeys(requested_indicators))
    if not requested_indicators or len(requested_indicators) > 3:
        raise ValueError("Provide between 1 and 3 indicator names.")
    unknown_indicators = [name for name in requested_indicators if name not in _INDICATORS]
    if unknown_indicators:
        raise ValueError("Unsupported indicator(s): " + ", ".join(unknown_indicators))
    if not 1 <= per_page <= 10:
        raise ValueError("per_page must be between 1 and 10.")

    retrieved_at = datetime.now(timezone.utc).isoformat()
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
        for country in normalized_countries:
            for indicator_name in requested_indicators:
                indicator_code = _INDICATORS[indicator_name]
                url = f"{_API_ROOT}/country/{_COUNTRIES[country]}/indicator/{indicator_code}"
                try:
                    response = await client.get(
                        url,
                        params={"format": "json", "per_page": per_page},
                        headers={"Accept": "application/json"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
                        raise ValueError("Unexpected World Bank response shape")
                    for item in payload[1]:
                        if not isinstance(item, dict):
                            continue
                        observations.append({
                            "country_code": country,
                            "country_name": (item.get("country") or {}).get("value"),
                            "indicator": indicator_name,
                            "indicator_code": indicator_code,
                            "year": item.get("date"),
                            "value": item.get("value"),
                            "unit": (item.get("unit") or ""),
                            "source": "World Bank Indicators API",
                            "source_url": str(response.url),
                            "retrieved_at": retrieved_at,
                            "verification_status": "retrieved_from_official_api",
                            "interpretation_limit": "Country-level macro context only; not a direct measure of demand for a particular product.",
                        })
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    errors.append({
                        "country_code": country,
                        "indicator": indicator_name,
                        "error": type(exc).__name__,
                    })
    return {
        "provider": "world_bank_indicators_api",
        "status": "retrieved" if observations and not errors else ("partial" if observations else "unavailable"),
        "retrieved_at": retrieved_at,
        "countries_requested": normalized_countries,
        "indicators_requested": requested_indicators,
        "observations": observations,
        "errors": errors,
        "market_demand_assessment": "not_assessed",
        "notice": "These official macro indicators provide context only. They do not establish customer intent, product demand, affordability or conversion.",
    }
