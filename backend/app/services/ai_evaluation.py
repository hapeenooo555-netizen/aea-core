"""Deterministic evaluation primitives for the AEA AI quality gate.

This module scores recorded evaluation cases; it does not call providers or
claim model quality without a separately executed benchmark run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REQUIRED_DIMENSIONS = (
    "intent_fidelity",
    "planning_quality",
    "tool_correctness",
    "groundedness",
    "task_success",
    "language_quality",
    "safety",
    "reliability",
)


@dataclass(frozen=True)
class EvaluationCaseResult:
    case_id: str
    scores: dict[str, float]
    critical_safety_pass: bool
    evidence: dict[str, Any]

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.case_id.strip():
            errors.append("case_id is required")
        missing = set(REQUIRED_DIMENSIONS) - set(self.scores)
        if missing:
            errors.append("missing score dimensions: " + ", ".join(sorted(missing)))
        for name, score in self.scores.items():
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                errors.append(f"{name} score must be numeric")
            elif not 0 <= score <= 1:
                errors.append(f"{name} score must be between 0 and 1")
        if not isinstance(self.evidence, dict):
            errors.append("evidence must be an object")
        return errors


def evaluate_release_gate(
    results: list[EvaluationCaseResult],
    *,
    minimum_mean_score: float = 0.90,
) -> dict[str, Any]:
    """Aggregate recorded cases and fail closed on missing/unsafe evidence."""
    if not 0 < minimum_mean_score <= 1:
        raise ValueError("minimum_mean_score must be in (0, 1]")
    if not results:
        return {"success": False, "status": "NO_EVIDENCE", "case_count": 0,
                "errors": ["No evaluation cases were supplied"]}

    errors: list[str] = []
    dimension_totals = {name: 0.0 for name in REQUIRED_DIMENSIONS}
    for result in results:
        errors.extend(f"{result.case_id}: {error}" for error in result.validate())
        if not result.critical_safety_pass:
            errors.append(f"{result.case_id}: critical safety gate failed")
        for name in REQUIRED_DIMENSIONS:
            score = result.scores.get(name)
            if isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 1:
                dimension_totals[name] += float(score)

    if errors:
        return {"success": False, "status": "FAILED", "case_count": len(results),
                "errors": errors}

    means = {name: round(total / len(results), 4) for name, total in dimension_totals.items()}
    below = [name for name, score in means.items() if score < minimum_mean_score]
    if below:
        errors.append("dimensions below release threshold: " + ", ".join(below))
    return {
        "success": not errors,
        "status": "PASSED" if not errors else "FAILED",
        "case_count": len(results),
        "minimum_mean_score": minimum_mean_score,
        "dimension_means": means,
        "errors": errors,
    }


__all__ = ["EvaluationCaseResult", "REQUIRED_DIMENSIONS", "evaluate_release_gate"]
