from app.services.affiliate_product_research import (
    rank_marketplace_products,
    score_marketplace_product,
)


def product(**overrides):
    record = {
        "product_id": "p-101",
        "name": "AI Tools Starter",
        "source_url": "https://example.com/marketplace/p-101",
        "verified": True,
        "category": "AI tools",
        "description": "Software for digital marketing teams",
        "commission_rate": 50,
        "earnings_per_click": 2.4,
        "conversion_rate": 4.5,
        "cancellation_rate": 3,
    }
    record.update(overrides)
    return record


def test_scores_verified_product_and_requires_approval():
    result = score_marketplace_product(product())
    assert result["eligible"] is True
    assert result["score"] > 0
    assert result["requires_human_approval"] is True
    assert result["published"] is False
    assert result["matched_niches"] == ["AI tools", "digital marketing"]


def test_rejects_unverified_product_and_missing_metrics():
    assert score_marketplace_product(product(verified=False))["reason"] == "marketplace_record_not_verified"
    result = score_marketplace_product(product(earnings_per_click=None))
    assert result["reason"] == "missing_performance_metrics"
    assert "earnings_per_click" in result["missing"]


def test_rejects_bad_source_and_out_of_range_metrics():
    assert score_marketplace_product(product(source_url="javascript:alert(1)"))["reason"] == "missing_verifiable_source_url"
    assert score_marketplace_product(product(conversion_rate=101))["reason"] == "metrics_out_of_range"


def test_ranking_is_deterministic_and_never_invents_products():
    low = product(product_id="p-2", name="AI tools basic", earnings_per_click=0.2)
    high = product(product_id="p-1", name="AI tools premium", earnings_per_click=5.0)
    result = rank_marketplace_products([low, high, product(verified=False)])
    assert result["candidates"][0]["product_id"] == "p-1"
    assert result["eligible_count"] == 2
    assert result["rejected_count"] == 1
    assert result["live_marketplace_fetch_performed"] is False
    assert result["published"] is False


def test_empty_catalog_returns_needs_verified_data_not_fake_recommendations():
    result = rank_marketplace_products([])
    assert result["status"] == "needs_verified_catalog_data"
    assert result["candidates"] == []
