"""Catalog of vetted market-research source routes and their access limitations.

This is a source directory, not a live data connector. It deliberately labels
sources that require manual report ingestion, platform permissions or alpha access.
"""
from __future__ import annotations

from typing import Any

_SOURCE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "tz_tc ra_statistics".replace(" ", ""),
        "name": "Tanzania Communications Regulatory Authority statistics",
        "publisher": "TCRA",
        "url": "https://www.tcra.go.tz/publications/statistics",
        "market_scope": ["TZ"],
        "source_type": "official",
        "evidence_type": ["quarterly_communications_statistics", "internet_and_mobile_sector"],
        "access_mode": "public_reports_manual_ingestion",
        "live_api_available": False,
        "limitations": "Public statistical reports; not a confirmed public machine-readable API. Extract report period and table/page provenance.",
    },
    {
        "id": "tz_nbs",
        "name": "National Bureau of Statistics Tanzania",
        "publisher": "NBS Tanzania",
        "url": "https://www.nbs.go.tz/",
        "market_scope": ["TZ"],
        "source_type": "official",
        "evidence_type": ["official_statistics", "household_and_business_surveys"],
        "access_mode": "public_publication_or_dataset_review",
        "live_api_available": False,
        "limitations": "Use published datasets and dissemination terms; do not assume every dataset is open or available through an API.",
    },
    {
        "id": "google_trends",
        "name": "Google Trends API",
        "publisher": "Google",
        "url": "https://developers.google.com/search/apis/trends",
        "market_scope": ["global"],
        "source_type": "platform",
        "evidence_type": ["search_interest", "regional_comparison", "time_series"],
        "access_mode": "alpha_access_required",
        "live_api_available": False,
        "limitations": "Official API remains an alpha for selected testers; do not call it a generally available production API. Trends interest is not absolute search volume.",
    },
    {
        "id": "meta_ad_library",
        "name": "Meta Ad Library",
        "publisher": "Meta",
        "url": "https://www.facebook.com/ads/library/",
        "market_scope": ["global"],
        "source_type": "platform",
        "evidence_type": ["advertising_creatives", "advertiser_activity"],
        "access_mode": "public_library_manual_review_or_permitted_api",
        "live_api_available": False,
        "limitations": "Public library research is not equivalent to a general-purpose API for all ads or performance metrics. Respect regional availability and platform terms.",
    },
)

def list_market_research_sources(market_scope: str = "tanzania_and_international") -> dict[str, Any]:
    """Return relevant sources with honest access/readiness metadata."""
    allowed = {
        "tanzania_and_international": {"TZ", "global"},
        "tanzania_only": {"TZ"},
        "international_only": {"global"},
    }.get(market_scope)
    if allowed is None:
        raise ValueError("Unsupported market scope")
    sources = [
        dict(source)
        for source in _SOURCE_CATALOG
        if set(source["market_scope"]) & allowed
    ]
    return {
        "market_scope": market_scope,
        "status": "source_directory_only_no_live_research_performed",
        "sources": sources,
        "notice": "A listed source is not proof that AEA has fetched or verified its data. Store dated evidence and retrieval provenance before using it in market conclusions.",
    }
