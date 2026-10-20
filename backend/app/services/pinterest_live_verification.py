"""Read-only Pinterest account and board verification helpers.

This module must never create or publish Pins. Callers should provide the
server-side access token belonging to the authenticated AEA owner.
"""
from __future__ import annotations

from typing import Any

import httpx


class PinterestVerificationError(RuntimeError):
    """Raised when Pinterest cannot verify the account or list its boards."""


def verify_account_and_boards(access_token: str) -> dict[str, Any]:
    """Return provider-confirmed account and board data without write actions."""
    if not access_token:
        raise PinterestVerificationError("Pinterest is not connected.")

    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        with httpx.Client(timeout=15.0) as client:
            account_response = client.get(
                "https://api.pinterest.com/v5/user_account", headers=headers
            )
            if account_response.status_code == 401:
                raise PinterestVerificationError(
                    "Pinterest rejected the access token; reconnect the account."
                )
            account_response.raise_for_status()
            account = account_response.json()

            boards_response = client.get(
                "https://api.pinterest.com/v5/boards",
                headers=headers,
                params={"page_size": 100},
            )
            if boards_response.status_code == 401:
                raise PinterestVerificationError(
                    "Pinterest rejected the access token while listing boards."
                )
            boards_response.raise_for_status()
            payload = boards_response.json()
    except httpx.TimeoutException as exc:
        raise PinterestVerificationError("Pinterest API request timed out.") from exc
    except httpx.HTTPError as exc:
        raise PinterestVerificationError("Pinterest API request failed.") from exc

    boards = [
        {"id": str(item["id"]), "name": str(item["name"])}
        for item in (payload.get("items") or [])
        if isinstance(item, dict) and item.get("id") and item.get("name")
    ]
    return {
        "success": True,
        "account": {
            key: account[key]
            for key in ("username", "account_type", "business_name")
            if account.get(key) is not None
        },
        "boards": boards,
        "published": False,
    }
