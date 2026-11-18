"""Allowlisted RSS discovery for public Kiswahili-language research.

Only feed metadata and short publisher-provided descriptions are retained. This
is discovery, not independent fact verification or a measure of market demand.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import httpx

_FEEDS = (
    {
        "id": "bbc_swahili",
        "name": "BBC News Swahili",
        "publisher": "BBC",
        "url": "https://feeds.bbci.co.uk/swahili/rss.xml",
        "language": "sw",
        "market_scope": ["TZ", "KE", "UG", "global"],
        "source_type": "news",
        "access_mode": "business_permission_required",
        "enabled": False,
    },
)
_MAX_ITEMS = 30
_MAX_EXCERPT = 500
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _plain_text(value: str | None, limit: int = _MAX_EXCERPT) -> str:
    text = _TAG.sub(" ", value or "")
    text = _SPACE.sub(" ", text).strip()
    return text[:limit]


def _published(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "bbc.com" or host.endswith(".bbc.com") or host == "bbc.co.uk" or host.endswith(".bbc.co.uk")


async def search_swahili_rss(
    query: str,
    *,
    country_code: str = "TZ",
    limit: int = 10,
) -> dict[str, Any]:
    """Search configured public Swahili RSS feeds; never fetch arbitrary URLs."""
    query = query.strip()
    if len(query) < 2 or len(query) > 200:
        raise ValueError("Query must contain between 2 and 200 characters.")
    country = country_code.strip().upper()
    if country not in {"TZ", "KE", "UG", "RW", "CD", "GLOBAL"}:
        raise ValueError("Unsupported Swahili market code.")
    if not 1 <= limit <= 20:
        raise ValueError("Limit must be between 1 and 20.")

    retrieved_at = datetime.now(timezone.utc).isoformat()
    # BBC terms require permission/licensing for business use of RSS metadata.
    # Keep the connector dormant until AEA has documented that permission.
    return {
        "provider": "aea_swahili_rss_discovery",
        "status": "source_permission_required",
        "query": query,
        "country_code": country,
        "language": "Kiswahili",
        "retrieved_at": retrieved_at,
        "results": [],
        "sources": [
            {
                "source_id": feed["id"],
                "publisher": feed["publisher"],
                "feed_url": feed["url"],
                "access_mode": feed["access_mode"],
                "enabled": feed["enabled"],
            }
            for feed in _FEEDS
            if country == "GLOBAL" or country in feed["market_scope"]
        ],
        "notice": "Utafutaji wa RSS umezimwa hadi AEA ipate ruhusa/leseni ya matumizi ya kibiashara kutoka kwa mchapishaji. Hakuna metadata iliyopakuliwa.",
    }


# Registry entries are discovery metadata only. A source is not fetchable merely
# because it appears here; access_mode and rights_status are enforced separately.
_SOURCES = (
    {
        "source_id": "voa_swahili",
        "publisher": "Voice of America - Kiswahili",
        "publisher_url": "https://www.voaswahili.com/rssfeeds",
        "country_scope": ["TZ", "KE", "UG", "RW", "CD", "GLOBAL"],
        "language": "sw",
        "categories": ["news", "health", "africa", "world", "culture"],
        "source_type": "publisher_rss_directory",
        "access_mode": "public_domain_original_voa_material_only",
        "rights_status": "conditional_public_domain",
        "rights_note": "VOA inaeleza kuwa maudhui yaliyotengenezwa pekee na VOA ni public domain; maudhui yenye leseni za AFP/AP/Reuters au wahusika wengine hayaruhusiwi kunakiliwa bila ruhusa. RSS feed URLs hazijawezeshwa hadi zichunguzwe moja moja.",
        "enabled": False,
        "verification_url": "https://www.voaafrica.com/p/8177.html",
    },
    {
        "source_id": "bbc_swahili",
        "publisher": "BBC News Swahili",
        "publisher_url": "https://www.bbc.com/swahili",
        "country_scope": ["TZ", "KE", "UG", "GLOBAL"],
        "language": "sw",
        "categories": ["news", "africa", "world", "business"],
        "source_type": "publisher_rss",
        "access_mode": "business_permission_required",
        "rights_status": "permission_pending",
        "rights_note": "Usomaji wa RSS hauchukuliwi kama ruhusa ya kutumia maudhui kibiashara.",
        "enabled": False,
        "verification_url": "https://www.bbc.com/usingthebbc/terms",
    },
)


def list_swahili_research_sources(country_code: str = "TZ") -> dict[str, Any]:
    """Return a rights-aware catalog; never fetch source content."""
    country = country_code.strip().upper()
    if country not in {"TZ", "KE", "UG", "RW", "CD", "GLOBAL"}:
        raise ValueError("Unsupported Swahili market code.")
    sources = [
        {
            **source,
            "country_scope": list(source["country_scope"]),
            "categories": list(source["categories"]),
        }
        for source in _SOURCES
        if country == "GLOBAL" or country in source["country_scope"] or "GLOBAL" in source["country_scope"]
    ]
    return {
        "provider": "aea_swahili_source_registry",
        "country_code": country,
        "language": "Kiswahili",
        "source_count": len(sources),
        "enabled_count": sum(1 for source in sources if source["enabled"]),
        "sources": sources,
        "notice": "Kuonekana kwenye registry hakumaanishi ruhusa ya kunakili au kutumia maudhui kibiashara. Hali ya haki na masharti ya kila chanzo lazima yaheshimiwe.",
    }
