from app.services.market_research_sources import list_market_research_sources


def test_default_catalog_includes_tanzania_and_global_sources_without_claiming_live_fetch():
    result = list_market_research_sources()
    assert result["status"] == "source_directory_only_no_live_research_performed"
    ids = {source["id"] for source in result["sources"]}
    assert "tz_tc ra_statistics".replace(" ", "") in ids
    assert "tz_nbs" in ids
    assert "google_trends" in ids
    assert "meta_ad_library" in ids
    google = next(source for source in result["sources"] if source["id"] == "google_trends")
    assert google["live_api_available"] is False
    assert google["access_mode"] == "alpha_access_required"


def test_country_scope_filters_catalog():
    tanzania = list_market_research_sources("tanzania_only")
    assert {source["id"] for source in tanzania["sources"]} == {
        "tz_tc ra_statistics".replace(" ", ""), "tz_nbs"
    }
    international = list_market_research_sources("international_only")
    assert "tz_nbs" not in {source["id"] for source in international["sources"]}
    assert "google_trends" in {source["id"] for source in international["sources"]}


def test_unknown_scope_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        list_market_research_sources("everywhere")
