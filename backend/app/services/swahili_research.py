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
    terms = [part.casefold() for part in re.findall(r"[\w'-]+", query) if len(part) > 1]
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        for feed in _FEEDS:
            if country != "GLOBAL" and country not in feed["market_scope"]:
                continue
            try:
                response = await client.get(
                    feed["url"],
                    headers={"Accept": "application/rss+xml, application/xml, text/xml"},
                )
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for node in root.findall(".//item"):
                    title = _plain_text(node.findtext("title"), 300)
                    description = _plain_text(node.findtext("description"))
                    link = (node.findtext("link") or "").strip()
                    if not title or not _host_allowed(link):
                        continue
                    searchable = f"{title} {description}".casefold()
                    if terms and not any(term in searchable for term in terms):
                        continue
                    items.append({
                        "title": title,
                        "publisher": feed["publisher"],
                        "source_id": feed["id"],
                        "source_type": feed["source_type"],
                        "language": "Kiswahili",
                        "country_scope": country,
                        "url": link,
                        "excerpt": description,
                        "published_at": _published(node.findtext("pubDate")),
                        "retrieved_at": retrieved_at,
                        "verification_status": "publisher_feed_metadata_only",
                        "reliability_note": "Chanzo cha habari kinachojulikana; dai mahususi bado linahitaji kuhakikiwa kwa kusoma chanzo na kulinganisha ushahidi.",
                    })
                    if len(items) >= _MAX_ITEMS:
                        break
            except (httpx.HTTPError, ET.ParseError, ValueError):
                errors.append({"source_id": feed["id"], "error": "feed_unavailable_or_invalid"})
    items.sort(key=lambda item: item["published_at"] or "", reverse=True)
    return {
        "provider": "aea_swahili_rss_discovery",
        "status": "retrieved" if items and not errors else ("partial" if items else "unavailable"),
        "query": query,
        "country_code": country,
        "language": "Kiswahili",
        "retrieved_at": retrieved_at,
        "results": items[:limit],
        "errors": errors,
        "notice": "Matokeo haya ni metadata na dondoo fupi kutoka RSS ya mchapishaji. Si uthibitisho huru wa madai, si utafiti wa maoni ya wananchi, wala kipimo cha mahitaji ya soko. Fungua chanzo na linganisha vyanzo kabla ya kutumia dai kama ukweli.",
    }
