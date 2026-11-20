# AEA Core — Creator Commerce, Content Intelligence & Growth Architecture

## Mission

AEA Core is the secure AI orchestration and execution engine behind AEA Hub. The product outcome is **ethical social traffic acquisition across connected media, qualified lead generation, creator commerce and measurable revenue**. Core researches, plans, generates, validates, adapts, publishes, measures and improves content. Hub is the only user-facing application; do not introduce ChatPlus or a third interface.

## System boundaries

- **AEA Hub:** creator-facing UX, forms, editing, preview, approvals, storefront, marketplace, checkout UI, analytics and settings.
- **AEA Core:** authenticated APIs, AI orchestration, research, content generation, QA, asset/version management, integrations, approvals, scheduling, publication reconciliation, commerce order state, entitlements, lead attribution and analytics normalization.
- **Supabase:** owner-scoped persistent records with RLS.
- **Object storage:** private creator-owned assets and paid digital files.
- **External providers:** LLM/media generation, affiliate networks, social platforms, payment processors and analytics sources. Each integration is capability-gated and provider-specific.

## Core bounded contexts

1. Identity, tenant and creator profile.
2. Research and trend intelligence.
3. Content production and asset library.
4. Affiliate network integrations.
5. Social account connections and publishing.
6. Creator storefront and digital products.
7. Marketplace discovery, checkout and orders.
8. Payment, refunds, seller balances and payout reconciliation.
9. Lead capture, CRM and attribution.
10. Monetization eligibility and revenue reporting.
11. Analytics and optimization experiments.
12. Governance, rights, privacy, audit and safety.

## Content intelligence and research

Research workers gather topic evidence, audience questions, search intent, current developments, competitor/content patterns where access is permitted, platform trends/signals and credible primary sources. Each research item stores canonical URL, publisher, retrieval time, freshness, excerpt/fact, confidence and content claims supported. Distinguish sourced facts, platform-reported trend signals, estimates, hypotheses and creative recommendations. Refresh time-sensitive claims before publication.

Trend ranking should use transparent features such as niche relevance, audience fit, freshness, novelty, search intent, evidence quality, format suitability and past first-party performance. No guaranteed virality, reach, views, impressions, likes or sales. No fake engagement, bot traffic, spam, engagement pods, deceptive clickbait or unauthorized scraping.

## Niche taxonomy and arbitrary topics

Provide suggested categories across education/academics, technology/AI/software, business/entrepreneurship, marketing/sales/e-commerce, finance education, careers/freelancing, health/wellness, beauty/fashion, food/agriculture, travel/tourism, culture/history/languages, home/DIY/construction/real estate, science/math/engineering/environment, parenting/relationships, arts/design/photography/music/film, entertainment/gaming, sports/hobbies/pets, nonprofit/community and local business. This is a suggestion taxonomy only. Accept any lawful custom niche, subject, industry or cross-niche prompt. Apply stronger evidence and review controls for regulated/high-stakes topics.

## Content formats

Typed templates and structured output schemas for:
- Video: YouTube Shorts, Reels, Facebook video, explainers, tutorials, interviews, ads, lessons; hook, script, scenes, storyboard, voiceover, on-screen text, subtitles, thumbnail brief, duration and aspect ratio.
- Writing: articles, blogs, SEO briefs, landing pages, product descriptions, ads, social posts, threads, newsletters and email sequences.
- Storytelling: short/serial stories, case studies, founder stories and narrative scripts.
- Education: courses, modules, lesson plans, scripts, objectives, worksheets, quizzes and study guides.
- E-books: outline, chapters, references, examples, exercises, glossary, cover brief, editable manuscript and export-ready PDF/EPUB/DOCX when export is actually implemented.
- Visual: Pins, carousels, Stories, quote cards, infographics and design briefs.
- Audio: podcasts, episode outlines, narration and voiceover scripts.
- Other custom lawful content types supplied by users.

For every content package generate multiple truthful hooks, platform-native captions, clear CTAs, relevant hashtags/keywords, creative/thumbnail direction, accessibility text/subtitles where relevant, target audience, objective, destination and tracking metadata. Rendered image/video/audio files must be produced by a configured provider and verified in storage; do not claim a script/storyboard is a rendered video.

## Affiliate content context

Keep Meta and Pinterest as the only external platform integrations. Affiliate Pin drafts use product facts and destination URLs supplied by the user. Preserve provenance, required affiliate disclosure and explicit approval before publishing. AEA cannot fabricate vendor approval, tracking links, conversions or commissions.

## Shared content mission lifecycle

1. Intake brief and validate user/owner scope.
2. Research topic/product and store source provenance.
3. Build content strategy and typed production plan.
4. Generate text assets through configured production LLM.
5. Generate media through configured image/video/audio providers when enabled.
6. QA for factual grounding, schema, platform limits, copyright/rights, policy, accessibility, disclosure and destination.
7. Create platform-specific variants.
8. Preview/edit in Hub.
9. Explicit user approval; bind approval to immutable content payload hash.
10. Publish or schedule through authorized destination-specific adapter.
11. Reconcile provider result, object ID, canonical URL and actual status.
12. Ingest supported metrics and propose evidence-based improvements.

Use durable missions, owner-scoped records, idempotency keys, bounded retries and reconciliation for uncertain external outcomes.

## Social distribution adapters

Pinterest, Facebook Pages, Instagram professional accounts, WhatsApp Business and YouTube are distinct destinations with different permissions and formats. A Facebook identity does not prove Page, Instagram, WhatsApp or monetization eligibility. Resolve each destination asset and capabilities independently. Build validators per placement; do not treat WhatsApp as a generic public-feed publishing destination. Publish only to accounts selected and authorized by the owner. Show unsupported capabilities explicitly.

## Monetization model — four independent revenue streams

### A. Native platform creator earnings
Connect account-level monetization status only when the provider exposes it and the user grants access. Show eligibility, invitations, program status, qualified views, estimated/finalized earnings and payouts only from provider data. Platforms control eligibility, country availability, invitations, calculations and payout. Never imply views/likes always pay or promise access.

### B. Affiliate commissions
Attribute click/conversion/commission events only through verified network reports, postbacks or explicitly labeled first-party tracking. Separate pending, approved, reversed and paid commission.

### C. Creator-owned digital commerce
Creators list original or properly licensed e-books, PDFs, guides, courses, templates, workbooks, reports, design assets and content packs. Store seller, ownership/licence declaration, title, description, category, preview, file/course entitlement, price, currency, availability, refund terms and support contact. Marketplace offers discovery, seller pages, product detail, search, checkout, order history, reporting and reviews. Affiliate offers are separate from creator-owned products.

### D. Leads and services
Support consent-based lead magnets, quote requests, booking/service offers, lead qualification, CRM status and recorded sale attribution. A lead is not revenue until a confirmed conversion/payment is recorded.

## Commerce and payment lifecycle

- Seller onboarding: identity/business verification and payout eligibility as required by payment provider and jurisdiction.
- Product listing: ownership/rights, content moderation, licence, price/currency, preview, delivery method, refund terms, fees and seller net calculation.
- Checkout: create server-side order with immutable price/fee snapshot and idempotency key.
- Payment: verify signed provider webhook/event server-side; never trust browser redirect as proof of payment.
- Fulfilment: issue time-limited or authenticated owner/order-scoped download/course entitlement only after confirmed successful payment.
- Refund/chargeback: revoke or adjust entitlement according to disclosed policy; reconcile seller balance and fees.
- Payout: only when provider confirms settlement and payout support for the seller's country/currency. Do not invent rails or show withdrawable balance from pending orders.
- Ledger: append-only financial events; maintain gross, discounts, taxes where applicable, provider fees, AEA fee, refunds, chargebacks, net seller proceeds and payout state.

Payment provider availability, Tanzania support, KYC, currency, settlement and payout rails must be verified before launch. Do not hardcode or imply support without provider confirmation.

## Lead generation and social traffic attribution

Each campaign selects an objective: awareness, reach, engagement, traffic, lead capture, conversion or retention. Define target audience, landing destination, CTA, conversion event and KPI. Generate tracked links/UTMs where appropriate. Support lead forms and consent capture, source/campaign/content IDs, qualification, follow-up and conversion status. Attribute only where tracking/integration evidence exists; avoid double counting and distinguish modeled attribution from confirmed source attribution.

## Analytics and growth loop

Normalize provider metrics while preserving source and timestamps: reach, impressions, views, qualified views, watch time where available, likes, comments, shares, saves, clicks, leads, conversions, orders, gross sales, affiliate commissions, fees, net proceeds and payouts. Display estimated vs provider-confirmed, pending vs finalized vs paid, reporting period, currency and last sync.

Loop: Research → Strategy → Create → QA → Adapt → Approve → Publish → Measure → Learn → Improve. Recommend experiments using actual account data; distinguish correlation from causation. Optimize for qualified traffic and business outcomes, not vanity metrics alone.

## Proposed persistence model

- `creator_profiles`, `brand_profiles`, `niche_preferences`
- `research_projects`, `research_sources`, `trend_signals`
- `content_projects`, `content_briefs`, `content_missions`
- `content_assets`, `content_asset_versions`, `content_variants`, `media_jobs`
- `affiliate_network_connections`, `affiliate_products`, `affiliate_tracking_links`, `affiliate_events`
- `social_destinations`, `publication_jobs`, `publication_records`, `platform_metrics`
- `creator_products`, `product_files`, `product_licences`, `marketplace_orders`, `order_items`
- `payment_events`, `refund_events`, `chargeback_events`, `seller_ledger_entries`, `seller_payouts`
- `lead_magnets`, `leads`, `lead_events`, `conversion_events`
- `platform_monetization_snapshots`, `affiliate_commission_events`, `revenue_reports`
- `content_approvals`, `audit_events`, `rights_reports`, `moderation_cases`

This is a proposed domain model, not a claim that these tables already exist. Review existing schema and migrations before implementation; reuse compatible tables and avoid duplicate sources of truth. Every user-owned table needs owner-scoped RLS and service-role access only in trusted backend paths.

## API surface (proposed; names subject to existing router conventions)

- `POST /content/briefs`, `POST /content/missions`, `GET /content/missions/{id}`
- `POST /content/missions/{id}/generate`, `POST /content/assets/{id}/variants`, `PATCH /content/assets/{id}`
- `POST /content/assets/{id}/approve`, `POST /publishing/jobs`, `GET /publishing/jobs/{id}`
- `GET /social/destinations`, `POST /social/destinations/refresh`
- `GET/POST /store/products`, `PATCH /store/products/{id}`, `POST /store/products/{id}/publish-listing`
- `GET /marketplace/products`, `POST /commerce/checkout-sessions`, `GET /commerce/orders`
- `POST /commerce/webhooks/{provider}`, `GET /commerce/seller/ledger`, `GET /commerce/seller/payouts`
- `POST /leads`, `GET /leads`, `POST /leads/{id}/qualify`, `POST /leads/{id}/convert`
- `GET /monetization/overview`, `GET /monetization/platform-status`, `GET /analytics/content/{id}`

All routes require authenticated owner context except verified provider webhook endpoints; webhook signatures and replay protection are mandatory. The API contract must be reconciled against existing endpoints before coding.

## Security, compliance and reliability

- Never accept owner_id from browser as authority; derive owner from verified JWT.
- RLS for all owner-owned data; test cross-user isolation.
- Encrypt provider tokens at rest; never expose credentials in Hub, URLs, logs or error messages.
- Use signed webhook verification, replay protection, idempotency and immutable audit trails.
- Store paid files privately and issue scoped entitlements after verified payment.
- Include copyright/licensing declarations, reporting/takedown, refunds, privacy/consent and opt-out flows.
- Protect regulated/high-stakes topics with evidence and review controls.
- Require explicit user approval before external publishing and clear confirmation before purchases/refunds/payout actions.
- Redact secrets and personal data in logs; use data minimization and retention policies.

## Phased implementation

1. **Foundation and inventory:** audit current Core/Hub routes, schemas, RLS, integrations and UI; define canonical contracts and migration plan.
2. **Unified Content Studio:** Hub UI and Core typed brief/research/generation/QA APIs; retain affiliate workflow.
3. **Cross-platform distribution:** platform variants, destination discovery, approvals, publishing receipts and metrics.
4. **Creator storefront:** product listings, private files, licences, checkout and verified digital fulfilment using a supported payment provider.
5. **Marketplace and seller operations:** discovery, orders, refunds, ledger, KYC/payout readiness and moderation.
6. **Growth and monetization analytics:** leads, attribution, platform monetization snapshots, affiliate events and experiments.

Each phase requires tests, migrations, security review, CI and Render deployment verification. Do not activate live payment collection or seller payouts until payment-provider and jurisdiction requirements are verified.

## End-state acceptance criteria

- AEA Hub offers one coherent creator workspace and no additional ChatPlus interface.
- Affiliate content and general AI Studio share Core orchestration, asset management, QA, approvals and publishing.
- E-books and broad user-defined niches are first-class.
- Every asset can be adapted into platform-specific hooks, captions, CTAs, hashtags/keywords and destination links.
- Users can sell their own digital products and promote them across authorized social destinations.
- Buyers receive paid files only after server-verified payment.
- Four monetization streams remain separate and evidence-backed.
- Content-to-click-to-lead-to-order funnel is measurable where tracking exists.
- No false claims of virality, platform eligibility, earnings, API capabilities or payment support.
