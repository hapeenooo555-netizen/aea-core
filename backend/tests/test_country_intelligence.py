import pytest

from app.services.country_intelligence import build_country_revenue_plan, list_country_markets


def test_launch_markets_are_exactly_the_six_selected_countries():
    result = list_country_markets()
    assert [item["country_code"] for item in result["markets"]] == ["TZ", "KE", "NG", "ZA", "US", "UK"]
    assert len(result["revenue_funnel"]) == 8


def test_country_plan_localizes_currency_language_and_channel_without_claiming_demand():
    result = build_country_revenue_plan(["TZ", "KE", "US"], "AI toolkit for small businesses", "sales", "facebook")
    assert [item["currency"] for item in result["plans"]] == ["TZS", "KES", "USD"]
    assert result["plans"][0]["selected_channel"] == "facebook"
    assert result["plans"][0]["language_options"] == ["sw", "en"]
    assert all(item["status"] == "planning_hypothesis_not_market_validation" for item in result["plans"])
    assert "No audience size" in result["guardrails"][0]


def test_country_plan_rejects_unknown_country_and_empty_offer():
    with pytest.raises(ValueError, match="unsupported country"):
        build_country_revenue_plan(["XX"], "Offer")
    with pytest.raises(ValueError, match="offer"):
        build_country_revenue_plan(["TZ"], "  ")


def test_country_plan_deduplicates_country_codes_and_limits_six():
    result = build_country_revenue_plan(["TZ", "TZ", "UK"], "Offer")
    assert [item["country_code"] for item in result["plans"]] == ["TZ", "UK"]
    with pytest.raises(ValueError, match="one and six"):
        build_country_revenue_plan([], "Offer")
