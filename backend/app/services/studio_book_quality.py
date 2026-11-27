"""Check visible book content before a natural-language mission can save it."""

from __future__ import annotations

import re
from typing import Any

from .readable_chapters import readable_chapters

_ENGLISH = frozenset("the and for with from that this your when then will are can each their before should".split())
_SWAHILI = frozenset("kwa na za wa ya vya cha katika kwenye ili hii hiyo kila kisha kabla baada ambao unaweza".split())
_BRIEF_WORDS = frozenset("about book ebook course workbook kuhusu kitabu make create write andika tengeneza".split())
_TITLE = re.compile(
    r"(?:^|[.!?]\s+)(?:title\s+(?:it|the\s+book)|call\s+it|(?:kipe|ipe)\s+jina(?:\s+kamili)?)"
    r"\s*:?\s+(.+?)(?:[.!?](?:\s|$)|$)", re.IGNORECASE,
)


def requested_book_title(prompt: str) -> str | None:
    match = _TITLE.search(prompt)
    title = match.group(1).strip(" \"'“”‘’") if match else ""
    return title if 0 < len(title) <= 300 else None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[^\W\d_]+", text.casefold()))


def _anchors(text: str) -> set[str]:
    return {word for word in _words(text) if len(word) >= 4} - _ENGLISH - _SWAHILI - _BRIEF_WORDS


def book_quality_issues(draft: dict[str, Any], topic: str, language: str) -> list[str]:
    chapters = readable_chapters(draft, minimum_length=250)
    if len(chapters) < 3:
        return ["fewer than three substantive chapters"]

    issues = []
    bodies = [chapter["body"] for chapter in chapters]
    normalized_bodies = {" ".join(body.casefold().split()) for body in bodies}
    if len(normalized_bodies) != len(bodies):
        issues.append("repeated chapter bodies")

    expected, alternate = (_SWAHILI, _ENGLISH) if language == "Swahili" else (_ENGLISH, _SWAHILI)
    for position, body in enumerate(bodies, start=1):
        words = _words(body)
        if len(words & expected) < 2 or len(words & alternate) > len(words & expected) + 1:
            issues.append(f"chapter {position} did not match the requested language")

    # The first clause is the subject. Later instructions may supply a title,
    # audience or formatting directives and should not become topic keywords.
    subject = re.split(r"[.!?]\s+|\s+(?:and|na)\s+(?:use|tumia|zingatia)\b", topic, maxsplit=1, flags=re.IGNORECASE)[0]
    focus = _anchors(subject)
    if focus:
        headings_on_topic = sum(bool(_anchors(chapter["title"]) & focus) for chapter in chapters)
        if headings_on_topic * 2 < len(chapters):
            issues.append("too few chapter headings addressed the requested topic")
        min_overlap = min(2, len(focus))
        body_matches = sum(len(_anchors(body) & focus) >= min_overlap for body in bodies)
        if body_matches * 2 < len(chapters):
            issues.append("too few chapter bodies addressed the requested topic")
    return issues
