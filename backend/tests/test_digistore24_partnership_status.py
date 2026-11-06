import pytest
from fastapi import HTTPException

from app.routers.affiliate_research import digistore24_partnership_status


@pytest.mark.parametrize("approved_ids,product_id,expected", [
    ("123, 456", "123", "approved"),
    ("123, 456", "789", "not_confirmed"),
    ("", "123", "not_confirmed"),
])
@pytest.mark.asyncio
async def test_partnership_status_uses_only_configured_allowlist(monkeypatch, approved_ids, product_id, expected):
    monkeypatch.setenv("DIGISTORE24_APPROVED_PRODUCT_IDS", approved_ids)
    result = await digistore24_partnership_status(product_id=product_id, _owner_id="owner-1")
    assert result["status"] == expected
    assert result["status_is_authoritative"] is (expected == "approved")
    if expected == "not_confirmed":
        assert "does not mean the vendor rejected" in result["note"]


@pytest.mark.asyncio
async def test_partnership_status_rejects_blank_product_id(monkeypatch):
    monkeypatch.setenv("DIGISTORE24_APPROVED_PRODUCT_IDS", "123")
    with pytest.raises(HTTPException) as error:
        await digistore24_partnership_status(product_id="   ", _owner_id="owner-1")
    assert error.value.status_code == 422
