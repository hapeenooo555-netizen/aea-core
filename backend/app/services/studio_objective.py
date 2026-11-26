"""Interpret bounded creator objectives and reuse the existing Studio operations."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from .p1_7_contracts import ToolContract, sanitize_payload
from .tool_registry import ToolRegistry

STUDIO_GENERATION_LEASE_SECONDS = 600

_TYPES = {
    "ebook": r"e[\s-]?books?|kitabu(?: cha (?:kielektroniki|kidijitali))?",
    "course": r"courses?|kozi",
    "workbook": r"workbooks?|kitabu cha mazoezi",
    "article": r"articles?|makala",
    "blog": r"blog(?: posts?)?|blogu",
    "newsletter": r"newsletters?|jarida",
    "social_post": r"social(?: media)? posts?|chapisho la mitandao ya kijamii",
}
_CREATE = re.compile(
    r"^(?:(?:please|tafadhali)\s+|(?:can|could|would) you\s+|i want (?:you )?to\s+)*"
    r"(?P<verb>create|write|draft|generate|make|compose|tengeneza|nitengenezee|andika|niandikie|"
    r"unda|niundie|andaa|niandalie)\s+(?P<request>.+)",
    re.IGNORECASE | re.DOTALL,
)
_STUDIO_TYPE = re.compile(
    r"^(?:(?:me|an?|my|the|new|short|complete|full|rasimu ya)\s+)*"
    + "(?:" + "|".join(
        f"(?P<{kind}>{pattern})" for kind, pattern in
        sorted(_TYPES.items(), key=lambda item: item[0] != "workbook")
    ) + r")\b",
    re.IGNORECASE,
)
_FOLLOW_UP = re.compile(
    r"(?:\b(?:and(?: then)?|then|kisha|halafu|na)\s+(?:publish|post|list|sell|send|upload|delete|pay|"
    r"advertise|chapisha|ichapishe|uza|iuze|tuma|futa|lipa|tangaza)\b"
    r"|\b(?:for sale|ready to sell|kwa ajili ya kuuza|kwa mauzo|for (?:the )?marketplace)\b)",
    re.IGNORECASE,
)
_SW_VERBS = {"tengeneza", "nitengenezee", "andika", "niandikie", "unda", "niundie", "andaa", "niandalie"}


def interpret_studio_goal(goal: str, inputs: dict[str, Any], locale: str) -> dict[str, Any] | None:
    """Extract a supported deliverable and topic from a direct English or Swahili request."""
    match = _CREATE.match(goal.strip())
    if not match:
        return None
    request = match["request"]
    content_type = _STUDIO_TYPE.match(request)
    if content_type is None:
        return None
    if _FOLLOW_UP.search(request) or re.search(
        r"\b(?:and|na)\s+(?:an?\s+)?(?:e[\s-]?book|course|workbook|article|newsletter|video|poster|pin|"
        r"kozi|makala|kitabu|picha)\b", request[content_type.end():], re.IGNORECASE,
    ):
        return {"intent": "studio_compound", "content_type": content_type.lastgroup}

    topic = request[content_type.end():].strip(" \n\t:,.!?—-")
    topic = re.sub(
        r"^(?:(?:about|on|covering|regarding|kuhusu|juu ya|inayohusu|kinachohusu)\b\s*)",
        "", topic, count=1, flags=re.IGNORECASE,
    ).strip(" \n\t:,.!?—-")
    topic = re.sub(
        r"\s*(?:\bin (?:english|swahili|kiswahili)|\b(?:kwa|in) kiswahili|\bkwa kiingereza)\s*$",
        "", topic, flags=re.IGNORECASE,
    ).strip(" \n\t:,.!?—-")
    if not topic:
        topic = next(
            (inputs[key].strip() for key in ("topic", "niche")
             if isinstance(inputs.get(key), str) and inputs[key].strip()),
            "",
        )
    return {
        "intent": "create_studio_draft",
        "content_type": content_type.lastgroup,
        "topic": topic or None,
        "language": (
            "English" if re.search(r"\b(?:in english|kwa kiingereza)\b", goal, re.IGNORECASE)
            else "Swahili" if (
                re.search(r"\b(?:in (?:swahili|kiswahili)|kwa kiswahili)\b", goal, re.IGNORECASE)
                or match["verb"].lower() in _SW_VERBS
                or locale.lower().split("-")[0] == "sw"
            )
            else "English"
        ),
    }


def register_studio_tools(registry: ToolRegistry) -> None:
    for name, description, properties in (
        ("studio.generate_draft", "Generate and validate a new Studio draft", ("prompt", "content_type", "topic", "language")),
        ("studio.save_draft", "Save the generated draft as an owner-scoped asset", ()),
        ("studio.verify_draft", "Read the saved draft back for its owner", ()),
    ):
        registry.register_contract(ToolContract(
            tool_name=name,
            description=description,
            input_schema={
                "type": "object",
                "properties": {key: {"type": "string"} for key in properties},
                "required": list(properties),
                "additionalProperties": False,
            },
            risk_level="WRITE_INTERNAL" if name == "studio.save_draft" else "READ_ONLY",
            idempotency_behavior="operation_key" if name == "studio.save_draft" else "not_applicable",
        ))


def studio_plan(goal: str, intent: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "step_name": "generate_studio_draft",
            "tool_name": "studio.generate_draft",
            "input": {
                "prompt": goal,
                "content_type": intent["content_type"],
                "topic": intent["topic"],
                "language": intent["language"],
            },
        },
        {
            "step_name": "save_studio_draft",
            "tool_name": "studio.save_draft",
            "input": {},
            "depends_on": ["generate_studio_draft"],
        },
        {
            "step_name": "verify_studio_draft",
            "tool_name": "studio.verify_draft",
            "input": {},
            "depends_on": ["save_studio_draft"],
        },
    ]


def execute_studio_step(
    tool: str,
    payload: dict[str, Any],
    dependencies: dict[str, dict[str, Any]],
    *,
    owner_id: str,
    client: Any,
    operation_key: str | None,
) -> dict[str, Any]:
    # The Studio routes expose synchronous operations with explicit owner and
    # user-scoped client arguments. Reuse their provider and storage validation.
    from app.routers.content_studio import (
        StudioAssetCreateRequest,
        StudioGenerateRequest,
        create_studio_asset,
        generate_studio_draft,
        list_studio_asset_versions,
    )

    if client is None:
        return {"success": False, "error": "Authenticated Studio storage is unavailable", "retryable": False}
    try:
        if tool == "studio.generate_draft":
            result = generate_studio_draft(StudioGenerateRequest(
                prompt=payload["prompt"],
                niche=payload["topic"][:160],
                content_type=payload["content_type"],
                language=payload["language"],
            ), owner_id)
            draft = sanitize_payload(result["draft"])
            title = draft.get("title")
            if not isinstance(title, str) or not title.strip() or not draft.get("core_content"):
                return {"success": False, "error": "Generated draft has no usable title or content", "retryable": True}
            return {
                "success": True,
                "status": "generated",
                "draft": draft,
                "prompt": payload["prompt"],
                "content_type": payload["content_type"],
                "topic": payload["topic"][:160],
                "verification": "STUDIO_DRAFT_VALIDATED",
            }
        if tool == "studio.save_draft":
            generated = dependencies["generate_studio_draft"]
            draft = generated["draft"]
            title = draft["title"]
            if not operation_key:
                return {"success": False, "error": "Draft persistence requires an operation key", "retryable": False}
            saved = create_studio_asset(StudioAssetCreateRequest(
                idempotency_key=operation_key,
                title=title.strip()[:300],
                content_type=generated["content_type"],
                niche=generated["topic"],
                prompt=generated["prompt"],
                content=draft,
            ), owner_id, client)["asset"]
            return {
                "success": True,
                "status": "saved",
                "asset_id": saved["asset_id"],
                "content_type": generated["content_type"],
                "version": saved["version"],
                "content_digest": _draft_digest(draft),
                "verification": "STUDIO_SAVE_ACKNOWLEDGED",
            }
        saved = dependencies["save_studio_draft"]
        versions = list_studio_asset_versions(UUID(saved["asset_id"]), owner_id, client)["versions"]
        for version in versions:
            if (
                str(version.get("asset_id")) == str(saved["asset_id"])
                and version.get("owner_id") == owner_id
                and version.get("version") == saved["version"]
                and version.get("content_type") == saved["content_type"]
                and _draft_digest(version.get("content")) == saved["content_digest"]
            ):
                return {
                    "success": True,
                    "status": "saved_draft_verified",
                    "asset_id": saved["asset_id"],
                    "version": saved["version"],
                    "content_type": saved["content_type"],
                    "verification": "OWNER_SCOPED_STUDIO_READBACK",
                }
        return {"success": False, "error": "Saved draft could not be verified by owner-scoped readback", "retryable": True}
    except HTTPException as exc:
        return {
            "success": False,
            "error": str(exc.detail),
            "retryable": exc.status_code in {429, 500, 502, 504},
        }
    except (KeyError, ValueError):
        return {"success": False, "error": "Studio returned invalid draft data", "retryable": False}
    except Exception:
        # Provider/database exception details may contain private data.
        return {"success": False, "error": "Studio operation failed unexpectedly", "retryable": False}


def _draft_digest(draft: Any) -> str:
    payload = json.dumps(draft, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
