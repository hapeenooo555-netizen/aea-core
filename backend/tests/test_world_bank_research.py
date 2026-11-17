import asyncio
import httpx
import pytest

from app.services.world_bank_research import fetch_world_bank_indicators


class FakeWorldBankResponse:
    def __init__(self, url):
        self.url = httpx.URL(url)

    def raise_for_status(self):
        return None

    def json(self):
        return [
            {"page": 1, "pages": 1},
            [{
                "country": {"value": "Tanzania"},
                "date": "2024",
                "value": 100,
                "unit": "",
            }],
        ]


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        return FakeWorldBankResponse(str(httpx.URL(url).copy_merge_params(params or {})))


def test_world_bank_retrieval_returns_country_and_source_provenance(monkeypatch):
    monkeypatch.setattr("app.services.world_bank_research.httpx.AsyncClient", FakeAsyncClient)
    result = asyncio.run(fetch_world_bank_indicators(["TZ"], ["population"]))
    assert result["status"] == "retrieved"
    observation = result["observations"][0]
    assert observation["country_code"] == "TZ"
    assert observation["year"] == "2024"
    assert observation["source"] == "World Bank Indicators API"
    assert observation["verification_status"] == "retrieved_from_official_api"
    assert result["market_demand_assessment"] == "not_assessed"


def test_world_bank_retrieval_rejects_unknown_countries_and_indicators():
    with pytest.raises(ValueError):
        asyncio.run(fetch_world_bank_indicators(["XX"]))
    with pytest.raises(ValueError):
        asyncio.run(fetch_world_bank_indicators(["TZ"], ["trending_searches"]))
