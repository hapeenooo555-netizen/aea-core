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


def test_brief_readiness_is_not_misrepresented_as_market_demand():
    result = enrich_content_intelligence(
        {"title": "AI for shops", "hashtags": []},
        {
            "prompt": "Help small shops reply to customers faster using simple automation",
            "niche": "Business automation",
            "audience": "Small shop owners in Tanzania",
            "objective": "leads",
            "content_type": "short_video",
            "platforms": ["facebook", "instagram"],
        },
    )
    intelligence = result["intelligence"]
    assert intelligence["brief_readiness"]["score"] == 100
    assert intelligence["brief_readiness"]["label"] == "brief_completeness_not_market_demand"
    assert intelligence["market_research"]["status"] == "not_performed_no_live_market_sources"
    assert intelligence["opportunity_assessment"]["demand_status"] == "unknown"
    assert intelligence["opportunity_assessment"]["score"] is None


def test_brief_intelligence_identifies_missing_audience_and_platforms():
    result = enrich_content_intelligence(
        {"title": "A guide", "hashtags": []},
        {"prompt": "Create a guide about saving time", "niche": "Productivity"},
    )
    readiness = result["intelligence"]["brief_readiness"]
    assert readiness["score"] < 100
    assert "target_audience_defined" in readiness["missing_inputs"]
    assert "platforms_selected" in readiness["missing_inputs"]


def test_records_supplied_research_sources_without_claiming_verification():
    source = {
        "title": "Small business digital adoption survey",
        "publisher": "Example Research Group",
        "url": "https://example.org/research/digital-adoption",
        "excerpt": "Survey respondents described barriers to adopting digital tools.",
        "published_at": "2026-10-01",
        "retrieved_at": "2026-11-18",
        "source_type": "research",
    }
    result = enrich_content_intelligence(
        {"title": "Digital tools for shops", "hashtags": []},
        {"prompt": "Help shops choose useful digital tools for customer service",
         "niche": "Small business", "audience": "Shop owners",
         "objective": "education", "content_type": "article",
         "platforms": ["facebook"], "research_sources": [source]},
    )
    research = result["intelligence"]["market_research"]
    assert research["status"] == "user_sources_supplied_not_independently_verified"
    assert research["sources"][0]["url"] == source["url"]
    assert research["sources"][0]["verification_status"] == "user_supplied_not_independently_verified"
    assert result["intelligence"]["opportunity_assessment"]["demand_status"] == "unknown"
