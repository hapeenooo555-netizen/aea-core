import pytest

from app.services.swahili_research import _host_allowed, _plain_text, _published


def test_plain_text_removes_markup_and_bounds_excerpt():
    assert _plain_text("<p>Habari <b>za</b> leo</p>") == "Habari za leo"
    assert len(_plain_text("x" * 700)) == 500


def test_only_bbc_publisher_hosts_are_allowed():
    assert _host_allowed("https://www.bbc.com/swahili/habari")
    assert _host_allowed("https://bbc.co.uk/swahili")
    assert not _host_allowed("https://bbc.com.attacker.example/story")
    assert not _host_allowed("https://example.com/story")


def test_published_date_is_normalized_and_invalid_date_is_null():
    assert _published("Tue, 18 Nov 2025 10:00:00 GMT").endswith("+00:00")
    assert _published("not-a-date") is None


@pytest.mark.asyncio
async def test_search_rejects_invalid_query_and_country():
    from app.services.swahili_research import search_swahili_rss

    with pytest.raises(ValueError):
        await search_swahili_rss("x")
    with pytest.raises(ValueError):
        await search_swahili_rss("biashara", country_code="XX")


@pytest.mark.asyncio
async def test_search_does_not_fetch_bbc_rss_without_business_permission():
    from app.services.swahili_research import search_swahili_rss

    result = await search_swahili_rss("biashara", country_code="TZ")
    assert result["status"] == "source_permission_required"
    assert result["results"] == []
    assert result["sources"][0]["enabled"] is False
    assert result["sources"][0]["access_mode"] == "business_permission_required"
