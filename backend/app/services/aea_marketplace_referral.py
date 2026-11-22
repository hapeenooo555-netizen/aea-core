"""Server-side validation for first-party AEA Marketplace referrals."""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return data
    return [data] if isinstance(data, dict) else []


def validate_aea_marketplace_referral(
    *, client: Any, destination_url: str, product_id: str, promoter_id: str,
) -> bool:
    """Validate an AEA referral URL without recording a click event."""
    if client is None or not destination_url or not product_id or not promoter_id:
        return False
    core_base = os.getenv("AEA_CORE_API_URL", "").strip().rstrip("/")
    if not core_base.startswith("https://"):
        return False
    parsed = urlparse(destination_url)
    expected = urlparse(core_base)
    if parsed.scheme != "https" or parsed.netloc.lower() != expected.netloc.lower():
        return False
    prefix = "/store/affiliate/r/"
    if not parsed.path.startswith(prefix):
        return False
    code = parsed.path[len(prefix):]
    if not code or "/" in code or len(code) < 20 or len(code) > 80:
        return False
    if parsed.query or parsed.fragment:
        return False
    try:
        result = client.rpc("validate_creator_affiliate_link", {
            "p_code": code,
            "p_product_id": product_id,
            "p_promoter_id": promoter_id,
        }).execute()
    except Exception:
        return False
    rows = _rows(result)
    if rows and isinstance(rows[0], dict):
        return rows[0].get("is_valid") is True
    value = getattr(result, "data", None)
    return value is True or value == [True]
