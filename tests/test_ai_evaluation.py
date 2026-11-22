from app.services.ai_evaluation import (
    EvaluationCaseResult,
    REQUIRED_DIMENSIONS,
    evaluate_release_gate,
)


def _case(case_id="golden-001", score=0.95, safety=True):
    return EvaluationCaseResult(
        case_id=case_id,
        scores={dimension: score for dimension in REQUIRED_DIMENSIONS},
        critical_safety_pass=safety,
        evidence={"dataset_version": "0.1.0", "reviewer": "test", "model": "test-model", "provider": "fixture", "prompt_version": "test-v1", "run_id": "unit-test-run"},
    )


def test_release_gate_passes_only_with_complete_high_quality_evidence():
    result = evaluate_release_gate([_case()])
    assert result["success"] is True
    assert result["status"] == "PASSED"
    assert result["dimension_means"]["groundedness"] == 0.95


def test_release_gate_fails_closed_when_no_cases_exist():
    result = evaluate_release_gate([])
    assert result["success"] is False
    assert result["status"] == "NO_EVIDENCE"


def test_release_gate_rejects_critical_safety_failure_even_with_high_scores():
    result = evaluate_release_gate([_case(safety=False)])
    assert result["success"] is False
    assert any("critical safety gate failed" in error for error in result["errors"])


def test_release_gate_rejects_missing_dimensions_and_invalid_scores():
    case = EvaluationCaseResult(
        case_id="bad-case",
        scores={"intent_fidelity": 1.2},
        critical_safety_pass=True,
        evidence={},
    )
    result = evaluate_release_gate([case])
    assert result["success"] is False
    assert any("missing score dimensions" in error for error in result["errors"])
    assert any("between 0 and 1" in error for error in result["errors"])


def test_release_gate_enforces_per_dimension_threshold():
    case = _case()
    scores = dict(case.scores)
    scores["language_quality"] = 0.70
    result = evaluate_release_gate([EvaluationCaseResult(
        case_id=case.case_id,
        scores=scores,
        critical_safety_pass=True,
        evidence=case.evidence,
    )])
    assert result["success"] is False
    assert any("language_quality" in error for error in result["errors"])
