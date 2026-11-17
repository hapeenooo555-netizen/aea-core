from app.services.content_intelligence import enrich_content_intelligence


def test_filters_generic_duplicate_hashtags_and_discloses_no_live_trend_data():
    draft = {"title": "AI for Small Business", "hashtags": ["#AI", "#SmallBusiness", "#viral", "#FYP", "#AI"]}
    result = enrich_content_intelligence(draft, {"niche": "AI automation", "audience": "Small business owners", "prompt": "Help Tanzanian small businesses automate customer follow-up"})
    assert result["hashtags"] == ["#AI", "#SmallBusiness"]
    assert result["hashtag_strategy"]["trend_status"] == "unverified_no_live_trend_source"
    assert "No live hashtag volume" in result["hashtag_strategy"]["data_limitations"]
    assert result["shareability_angle"]
    assert result["hook_rationale"]


def test_handles_missing_or_malformed_hashtags():
    result = enrich_content_intelligence({"hashtags": "not-a-list"}, {"niche": "Education", "prompt": "Create a learning guide"})
    assert result["hashtags"] == []
    assert result["hashtag_strategy"]["groups"]["location"] == []
