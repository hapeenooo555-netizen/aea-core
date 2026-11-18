"""Cross-domain AEA Prompt Intelligence.

The engine preserves the customer's stated intent and facts while adding
professional execution guidance. It never treats missing professional jargon
as a reason to reject a request, and it does not silently invent facts.
"""
from __future__ import annotations

from typing import Any

DOMAIN_PROFILES: dict[str, dict[str, Any]] = {
    "video": {
        "signals": ("video", "reel", "shorts", "tiktok", "commercial", "documentary", "explainer", "filamu", "matangazo ya video"),
        "roles": ("creative_director", "scriptwriter", "producer", "director", "cinematographer", "storyboard_artist", "sound_designer", "video_editor", "quality_controller"),
        "deliverables": ("creative_concept", "script", "storyboard", "shot_list", "voiceover", "sound_plan", "editing_plan", "export_specifications"),
        "standards": ("storytelling_consistency", "audio_clarity", "visual_quality", "duration", "text_readability", "brand_consistency", "platform_compatibility", "claim_accuracy"),
    },
    "graphic_design": {
        "signals": ("graphic design", "poster", "flyer", "logo", "banner", "infographic", "brochure", "design", "mchoro", "tangazo"),
        "roles": ("creative_director", "graphic_designer", "art_director", "typographer", "brand_designer", "quality_controller"),
        "deliverables": ("creative_concept", "layout", "visual_hierarchy", "copy", "color_palette", "typography", "imagery_direction", "export_specifications"),
        "standards": ("readability", "contrast", "alignment", "brand_consistency", "platform_safe_areas", "factual_accuracy", "accessibility"),
    },
    "marketing": {
        "signals": ("marketing", "campaign", "advert", "advertisement", "lead generation", "kampeni", "masoko"),
        "roles": ("marketing_strategist", "campaign_planner", "copywriter", "audience_researcher", "quality_controller"),
        "deliverables": ("campaign_objective", "audience_hypothesis", "message", "creative_angles", "channel_plan", "cta", "measurement_plan"),
        "standards": ("audience_fit", "claim_accuracy", "channel_fit", "message_clarity", "measurability"),
    },
    "copywriting": {
        "signals": ("copywriting", "caption", "sales copy", "headline", "email", "script", "maandishi"),
        "roles": ("copywriter", "editor", "brand_voice_specialist", "quality_controller"),
        "deliverables": ("headline_options", "body_copy", "cta", "tone_notes", "proofreading_report"),
        "standards": ("clarity", "grammar", "tone_consistency", "claim_accuracy", "readability"),
    },
    "business_documents": {
        "signals": ("proposal", "business plan", "report", "memo", "document", "proposal", "mpango wa biashara", "ripoti"),
        "roles": ("business_analyst", "researcher", "technical_writer", "editor", "quality_controller"),
        "deliverables": ("document_outline", "executive_summary", "evidence_and_assumptions", "recommendations", "references"),
        "standards": ("logical_structure", "evidence_traceability", "factual_accuracy", "consistency", "readability"),
    },
}

def detect_domain(prompt: str, content_type: str = "custom", niche: str = "") -> str:
    """Choose a domain using explicit content type first, then brief keywords."""
    explicit = (content_type or "").casefold()
    if explicit in {"short_video", "long_video", "podcast"}:
        return "video"
    if explicit in {"infographic", "carousel"}:
        return "graphic_design"
    text = " ".join((prompt or "", niche or "")).casefold()
    scores = {
        name: sum(1 for signal in profile["signals"] if signal in text)
        for name, profile in DOMAIN_PROFILES.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"

def build_intelligence_context(*, prompt: str, content_type: str, niche: str,
                               audience: str | None, objective: str,
                               language: str, tone: str, platforms: list[str]) -> dict[str, Any]:
    """Return a safe, structured professional brief without changing user intent."""
    domain = detect_domain(prompt, content_type, niche)
    profile = DOMAIN_PROFILES.get(domain, {})
    return {
        "domain": domain,
        "customer_intent_verbatim": prompt,
        "explicit_constraints": {
            "content_type": content_type, "niche": niche, "audience": audience,
            "objective": objective, "language": language, "tone": tone,
            "platforms": list(platforms),
        },
        "specialist_roles": list(profile.get("roles", ("domain_specialist", "quality_controller"))),
        "expected_deliverables": list(profile.get("deliverables", ("structured_output", "execution_guidance"))),
        "quality_standards": list(profile.get("standards", ("intent_fidelity", "factual_accuracy", "clarity"))),
        "missing_information_policy": (
            "Infer only low-risk creative details and label assumptions. Ask the fewest essential questions "
            "when a material fact cannot safely be inferred. Never invent prices, claims, identity, budget, dates or contact details."
        ),
        "language_style_policy": (\n            "For Tanzanian Swahili social content, use natural language familiar to online audiences. Common terms such as post, link, comment, inbox and follow may remain English when they are clearer. " \n            "For formal business content use professional Kiswahili. Avoid forced slang, literal translations and Swahilized English words such as specifikesheni."\n        ),\n        "intent_preservation_policy": (
            "Preserve the customer's objective, facts, constraints, language, brand identity and requested outcome. "
            "Improve the execution method, not the customer's intent. Do not reject an informal or incomplete prompt "
            "because it lacks professional terminology."
        ),
    }
