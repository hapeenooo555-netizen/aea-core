"""Normalize chapter content from saved and newly generated Studio assets."""

from typing import Any


def has_substantive_content(content: dict[str, Any]) -> bool:
    core = content.get("core_content")
    if isinstance(core, str) and len(core.strip()) >= 300:
        return True
    return bool(readable_chapters(content, minimum_length=200, minimum_core_length=300))


def readable_chapters(
    content: dict[str, Any],
    title: str = "",
    *,
    minimum_length: int = 1,
    minimum_core_length: int = 1,
) -> list[dict[str, Any]]:
    core = content.get("core_content")
    sources = [content, core] if isinstance(core, dict) else [content]
    chapters = next(
        (
            source[key]
            for source in sources
            for key in ("chapters", "chapter_content", "modules")
            if isinstance(source.get(key), list) and source[key]
        ),
        None,
    )
    if chapters is None and isinstance(core, str) and len(core.strip()) >= minimum_core_length:
        chapters = [{"title": title, "body": core}]
    if chapters is None:
        return []

    result = []
    for number, chapter in enumerate(chapters, start=1):
        chapter_title = ""
        if isinstance(chapter, dict):
            chapter_title = str(chapter.get("title") or "").strip()
            body = next(
                (
                    chapter[key].strip()
                    for key in ("body", "content", "text", "chapter_content")
                    if isinstance(chapter.get(key), str) and chapter[key].strip()
                ),
                "",
            )
        else:
            body = chapter.strip() if isinstance(chapter, str) else ""
        if len(body) < minimum_length:
            if minimum_length > 1:
                return []
            continue
        result.append({"number": number, "title": chapter_title or f"Chapter {number}", "body": body})
    return result
