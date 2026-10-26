from app.services.freelancer_employee import analyze_job, draft_proposal


def test_analyze_job_returns_questions_and_does_not_take_external_action():
    result = analyze_job("Write 3 blog articles about home gardening")
    assert result["success"] is True
    assert result["employee"] == "freelancer"
    assert "article" in result["deliverable_hints"]
    assert result["clarification_questions"]
    assert result["external_action_taken"] is False


def test_analyze_job_rejects_blank_description():
    try:
        analyze_job("   ")
    except ValueError as exc:
        assert "required" in str(exc).lower()
    else:
        raise AssertionError("blank descriptions must be rejected")


def test_proposal_is_draft_and_does_not_invent_experience():
    result = draft_proposal("Create a social media content calendar")
    assert result["status"] == "draft"
    assert result["requires_human_review"] is True
    assert result["external_action_taken"] is False
    assert "[Add a truthful example" in result["proposal_text"]
    assert "not been submitted" in " ".join(result["warnings"])


def test_proposal_includes_only_user_supplied_price_and_timeline():
    result = draft_proposal(
        "Write an SEO brief",
        relevant_experience="I have a sample SEO brief in my portfolio.",
        price_quote="USD 40",
        timeline="3 days",
    )
    assert "USD 40" in result["proposal_text"]
    assert "3 days" in result["proposal_text"]
    assert "sample SEO brief" in result["proposal_text"]
