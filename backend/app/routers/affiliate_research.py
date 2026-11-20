"""Authenticated, read-only affiliate product research API."""
from __future__ import annotations


from fastapi import APIRouter

from app.services.digistore24_user_connection import (
    Digistore24ConnectionError,
    load_connected_api_key,
)
from app.services.digistore24_marketplace import (
    Digistore24MarketplaceClient,
    Digistore24MarketplaceError,
)

router = APIRouter(prefix="/affiliate/research", tags=["affiliate-research"])


__all__ = ["router"]
