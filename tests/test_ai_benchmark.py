from app.services.ai_benchmark import DATASET_VERSION, GOLDEN_TASKS, list_golden_tasks


def test_golden_dataset_is_versioned_and_has_bilingual_cases():
    assert DATASET_VERSION
    assert len(GOLDEN_TASKS) >= 8
    assert {case.locale for case in GOLDEN_TASKS} >= {"sw-TZ", "en"}


def test_external_side_effect_cases_are_explicitly_risk_labeled():
    cases = {case.case_id: case for case in GOLDEN_TASKS}
    assert cases["tz-sw-approval-001"].risk_tier == "EXTERNAL_SIDE_EFFECT"
    assert "require_publish_approval" in cases["tz-sw-approval-001"].expected_invariants


def test_prompt_injection_case_requires_untrusted_input_boundary():
    cases = {case.case_id: case for case in GOLDEN_TASKS}
    assert "treat_page_as_untrusted_data" in cases["en-injection-001"].expected_invariants
    assert "do_not_reveal_secrets" in cases["en-injection-001"].expected_invariants


def test_dataset_filtering_preserves_case_ids_and_version():
    swahili = list_golden_tasks(locale="sw-TZ")
    assert swahili
    assert all(case["locale"] == "sw-TZ" for case in swahili)
    assert all(case["dataset_version"] == DATASET_VERSION for case in swahili)
