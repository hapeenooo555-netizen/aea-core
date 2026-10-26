"""Safe, provider-neutral first slice of the AEA Freelancer Employee.

This module analyzes user-supplied job descriptions and drafts proposals.
It never browses marketplaces, submits proposals, contacts clients, or claims
experience/results that the user did not supply.
"""
from __future__ import annotations

from typing import Any


DELIVERABLE_KEYWORDS = (
    "article", "blog", "content", "social media", "script", "video",
    "seo", "keyword", "design", "research", "translation", "data entry",
    "website", "landing page", "email", "copywriting", "copy",
)
CONSTRAINT_KEYWORDS = (
    "deadline", "due date", "within", "by ", "budget", "fixed price",
    "hourly", "revisions", "word count", "words", "pages", "timezone",
)


def analyze_job(description: str, *, source_url: str | None = None) -> dict[str, Any]:
    """Produce a transparent heuristic analysis of a job supplied by the user."""
    text = description.strip()
    if not text:
        raise ValueError("Job description is required")
    lower = text.lower()
    lines = [line.strip(" \t-*•") for line in text.splitlines() if line.strip()]
    deliverable_hints = [keyword for keyword in DELIVERABLE_KEYWORDS if keyword in lower]
    constraint_hints = [keyword for keyword in CONSTRAINT_KEYWORDS if keyword in lower]
    questions = []
    if not any(word in lower for word in ("deadline", "by ", "within", "due")):
        questions.append("What is the required deadline and time zone?")
    if not any(word in lower for word in ("$", "usd", "budget", "hourly", "fixed price")):
        questions.append("What is the budget or preferred pricing model?")
    if not any(word in lower for word in ("deliverable", "format", "file", "docx", "pdf", "words", "pages")):
        questions.append("What exact deliverables and file format are expected?")
    return {
        "success": True,
        "employee": "freelancer",
        "analysis_type": "heuristic_from_user_supplied_text",
        "source_url": source_url,
        "summary": text[:500],
        "requirement_lines": lines[:30],
        "deliverable_hints": deliverable_hints,
        "constraint_hints": constraint_hints,
        "clarification_questions": questions,
        "external_action_taken": False,
        "notice": "This is a first-pass analysis, not a verified interpretation of a marketplace listing.",
    }


def draft_proposal(
    description: str,
    *,
    freelancer_name: str | None = None,
    relevant_experience: str | None = None,
    approach: str | None = None,
    price_quote: str | None = None,
    timeline: str | None = None,
) -> dict[str, Any]:
    """Draft a proposal without inventing credentials, prices, or commitments."""
    text = description.strip()
    if not text:
        raise ValueError("Job description is required")
    greeting = f"Hello, I'm {freelancer_name.strip()}." if freelancer_name and freelancer_name.strip() else "Hello,"
    experience = (
        relevant_experience.strip()
        if relevant_experience and relevant_experience.strip()
        else "[Add a truthful example of relevant experience or a portfolio link.]"
    )
    plan = (
        approach.strip()
        if approach and approach.strip()
        else "I would first confirm the requirements and success criteria, then complete the agreed deliverables and review them against your brief."
    )
    terms = []
    if price_quote and price_quote.strip():
        terms.append(f"Proposed price: {price_quote.strip()} (subject to your confirmation).")
    else:
        terms.append("Price: to be agreed after confirming the scope.")
    if timeline and timeline.strip():
        terms.append(f"Proposed timeline: {timeline.strip()} (subject to scope confirmation).")
    else:
        terms.append("Timeline: to be agreed after confirming the deadline and scope.")
    proposal = (
        f"{greeting}\n\n"
        "I reviewed your project brief and would be interested in helping with this work.\n\n"
        f"My relevant experience: {experience}\n\n"
        f"My proposed approach: {plan}\n\n"
        + "\n".join(terms)
        + "\n\nPlease share any priorities or acceptance criteria I should account for. "
        "I can then confirm the scope before work begins.\n\nBest regards"
    )
    return {
        "success": True,
        "employee": "freelancer",
        "proposal_text": proposal,
        "based_on_job_description": text[:500],
        "status": "draft",
        "requires_human_review": True,
        "external_action_taken": False,
        "warnings": [
            "Review every statement before use.",
            "Replace the experience placeholder with truthful details; do not claim credentials you do not have.",
            "Price and timeline are proposals only and are not commitments until you confirm them.",
            "This draft has not been submitted to any marketplace.",
        ],
    }
