"""Canonical AEA Affiliate Hub brand knowledge.

This is the reusable source of brand truth for creative studios. Keep verified
product facts separate from creative interpretations and campaign-specific copy.
"""
from __future__ import annotations

from copy import deepcopy


AEA_BRAND_PROFILE = {
    "schema_version": 1,
    "identity": {
        "name": "AEA Affiliate Hub",
        "short_name": "AEA",
        "category": "AI-assisted affiliate content planning and digital business workspace",
        "positioning": (
            "A thoughtful way to plan affiliate content. AI helps prepare; the user reviews, "
            "decides and stays in control."
        ),
        "brand_promise": "A clearer path from idea to approved content.",
        "primary_tagline": "AI-assisted. Human-approved.",
        "supporting_tagline": "Your workflow. Your approval.",
        "homepage_message": (
            "A thoughtful way to plan affiliate content for Pinterest. "
            "AI helps you prepare. You review, decide, and stay in control."
        ),
    },
    "philosophy": [
        "AI assists with preparation; it does not replace the user's judgement.",
        "The user reviews and explicitly approves consequential content or actions.",
        "The user remains informed and in control of the workflow.",
    ],
    "audiences": [
        "affiliate marketers",
        "content creators",
        "entrepreneurs and small-business owners",
    ],
    "verified_capabilities": [
        "AEA Content Studio generates structured, editable content drafts.",
        "AEA Country Intelligence creates country-specific campaign hypotheses and conversion paths; these are not verified demand or revenue forecasts.",
        "Users can save generated content drafts in their authenticated AEA workspace.",
    ],
    "claims_requiring_evidence": [
        "guaranteed sales, income, leads, virality or business growth",
        "customer counts, market share, testimonials or performance statistics",
        "unverified integrations, automatic publishing or autonomous business execution",
        "free trials, prices or guarantees",
    ],
    "voice": {
        "traits": ["thoughtful", "confident", "clear", "professional", "empowering", "human-centred"],
        "avoid": ["hype", "fear-based selling", "guaranteed outcomes", "unexplained technical jargon"],
    },
    "visual_identity": {
        "logo_asset": {
            "filename": "aea-symbol-final-1024.png",
            "status": "user_supplied_reference_not_yet_packaged_for_runtime",
            "usage": (
                "Use the original supplied logo asset unchanged. Never redraw, regenerate, distort, recolour, "
                "crop tightly, or place text over it. Maintain clear space and aspect ratio. Until the original "
                "binary asset is installed in the runtime asset store, reserve a clean logo area; do not invent a substitute."
            ),
        },
        "palette": {
            "deep_navy": "#0A1026",
            "homepage_night": "#05121B",
            "turquoise": "#2CC2B5",
            "mint": "#75D7C6",
            "gold": "#F5B83D",
            "connector_blue": "#587DC4",
            "white": "#FFFFFF",
        },
        "primary_background": "deep navy / midnight blue",
        "primary_accent": "#75D7C6",
        "supporting_colours": ["white", "cool grey", "turquoise", "restrained gold"],
        "mood": "confident, modern, premium, thoughtful and human-centred technology",
        "composition": "strong typography, generous negative space, clear focal point and restrained network motifs",
    },
}


def get_aea_brand_profile() -> dict:
    """Return an isolated copy so callers cannot mutate canonical brand truth."""
    return deepcopy(AEA_BRAND_PROFILE)


def get_aea_brand_context() -> dict:
    """Return brand truth in a concise structure suitable for creative prompts."""
    profile = get_aea_brand_profile()
    return {
        "brand_name": profile["identity"]["name"],
        "identity": profile["identity"],
        "philosophy": profile["philosophy"],
        "audiences": profile["audiences"],
        "verified_capabilities": profile["verified_capabilities"],
        "claims_requiring_evidence": profile["claims_requiring_evidence"],
        "brand_voice": profile["voice"],
        "visual_identity": profile["visual_identity"],
    }
