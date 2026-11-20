# AEA Core — Creator Commerce & Content Growth Architecture

## Product mission
AEA Core is the AI, orchestration, security, commerce and integration backend. AEA Hub is the only user-facing product. No ChatPlus or third interface. The primary outcome is ethical social traffic, qualified leads, creator sales and measurable revenue.

## Capabilities
1. Affiliate content using user-supplied product facts and affiliate destination URLs, with Pinterest draft creation, disclosure and explicit publishing approval.
2. AI content production across any lawful niche: research, writing, storytelling, education, e-books, courses, video/audio packages, graphics and social assets.
3. Creator commerce: users list and sell original/licensed digital products in an AEA storefront and marketplace.
4. Social distribution to Pinterest and authorized Meta assets; other platforms via their own integrations.
5. Native platform monetization readiness/earnings intelligence only where official APIs expose authorized data. Platforms control eligibility and payouts.
6. Consent-based leads, CRM, services and conversion attribution.
7. Analytics, experimentation and growth recommendations based on measured outcomes.

## System boundaries
- Hub: Content Creation, affiliate discovery, creator Store, Marketplace, Social Accounts, Monetization, Leads and Analytics.
- Core API: auth, authorization, typed generation, owner-scoped persistence, mission orchestration, integrations and webhooks.
- Core workers: research, generation, QA, media jobs, platform adaptation, scheduling, event reconciliation and analytics.
- Supabase: RLS-protected records and private storage.
- Providers: LLM/media, affiliate networks, social platforms, payment processors. Secrets remain encrypted in server-side vaults.

## Content pipeline
Brief → research → strategy → mission → structured generation → QA → platform variants → monetization destination → human approval → publish/schedule → metrics → learn/improve.

Every content package must include multiple truthful hooks, platform-native captions, objective-matched CTAs, relevant hashtags/keywords, creative direction, title/thumbnail, storyboard/subtitles where relevant, destination URL, tracking IDs and research provenance. No promise of virality or guaranteed reach, views, likes, leads or sales.

Supported types: YouTube Shorts and long-form video packages; Reels; articles, blogs, SEO, landing pages, product copy, newsletters and ad copy; storytelling and case studies; courses, lessons, quizzes and workbooks; e-books, guides, reports, checklists, templates and downloads; Pins, carousels, Stories, infographics and quote cards; podcasts, narration and audio scripts; user-defined formats. Rendered media and export files are claimed only after provider jobs create verifiable stored outputs.

Niche suggestions cover education and academic subjects; technology, AI and software; business, entrepreneurship and productivity; marketing, sales and e-commerce; finance education; careers and freelancing; health/wellness; beauty/fashion; food/agriculture; travel, history, culture and languages; home, construction and real estate; science, mathematics and engineering; environment; parenting and relationships; arts, design, photography, music, film and entertainment; sports, gaming, hobbies and pets; nonprofit and local business. This is not a whitelist: accept any custom lawful niche and cross-niche topic. Apply evidence and human review to high-stakes content.

## Proposed data domains
creator_profiles; content_projects; content_briefs; content_missions; content_assets; content_variants; media_jobs; content_approvals; publication_records; campaigns; tracking_links; leads; creator_products; marketplace_orders; product_entitlements; payment_events; seller_payouts; affiliate_events; platform_monetization_snapshots; analytics_events.

Every record is owner-scoped. Use RLS, server-side authorization, unique provider event IDs and idempotency. Never trust owner IDs supplied by the browser.

## Creator commerce workflow
1. Seller submits original/licensed product, ownership declaration, description, category, preview, price/currency, licence, delivery files/course access and refund/support terms.
2. Core validates seller permissions and files; store privately and moderate listings.
3. Buyer checks out using a configured payment provider.
4. Core verifies signed payment webhooks and reconciles idempotently.
5. Grant buyer/order-scoped download/course entitlement only after confirmed payment.
6. Refund/chargeback updates order and entitlement per disclosed terms.
7. Seller sees gross sales, fees, refunds, chargebacks, net proceeds and payout state.
8. Enable payouts only after payment-provider country coverage, KYC, tax and settlement requirements are verified.

Never store raw card data, trust browser success redirects, expose private file URLs or deliver paid files before confirmed payment. Disclose AEA fees, payout timing, licensing and refund terms.

## Four separate revenue ledgers
A. Native platform earnings: platform-controlled eligibility and creator payouts, shown only from official authorized provider data.
B. Affiliate commissions: network-reported clicks, conversions and pending/approved/reversed/paid commissions.
C. AEA marketplace: orders, gross sales, refunds, chargebacks, fees, net proceeds and seller payouts.
D. Leads/services: qualified leads and revenue only when a transaction is recorded.

Never mix estimates or pending amounts into withdrawable balances. Every metric includes currency, period, source, last sync and estimated/pending/confirmed/paid status.

## Affiliate adapters
Meta and Pinterest are the only supported external platform integrations. Product details and affiliate URLs must be supplied by the user; never fabricate approval, tracking links or commissions. Pinterest publication requires explicit approval.

## Publishing adapters
Pinterest requires board, media, title/description, destination, disclosure and verified Pin ID/URL. Facebook requires authorized Page and supported post type. Instagram requires professional account, media container and permissions. WhatsApp Business uses approved templates and conversation rules; it is not a public-feed destination. YouTube/others require their own authorized API. A connected identity does not imply Page, Instagram, WhatsApp or monetization access. External publishing requires approval, idempotency and provider receipt verification.

## Research and growth
Research audience questions, intent, timely developments, credible sources and platform signals. Store source URLs, dates, freshness and confidence. Recommend pillars, formats, cadence and experiments. No fake engagement, bot traffic, spam, deceptive claims or hashtag stuffing. Distinguish correlation from causation and optimize for qualified traffic, leads and conversions rather than vanity metrics alone.

## Security and trust
RLS on all user data; private storage and short-lived signed URLs; encrypted credentials; verified webhooks; consent/opt-out and anti-spam safeguards; copyright/licensing declarations, reporting and takedown process; stronger evidence/human review for high-stakes areas; sanitized logs without secrets or OAuth/payment credentials.

## Delivery phases
1. Hub surfaces: unified Content Creation (Affiliate + Studio), Store/Marketplace, Social Accounts, Monetization, Leads and Analytics in existing shell.
2. Core content engine: typed brief, research, generation, structured assets, niche taxonomy, e-book chapter workflow, QA, adaptation, persistence and approvals. No silent mock fallback in production.
3. Publishing and growth analytics: Pinterest/Meta adapters, variants, scheduling where supported, receipt reconciliation and metrics.
4. Creator marketplace: listings, storage, moderation, checkout, verified payment webhooks, entitlements, refunds and seller reporting.
5. Affiliate/platform monetization intelligence from provider-confirmed data.
6. Leads, attribution, experiments and measured optimization.

## Acceptance criteria
- Hub is one coherent interface, no ChatPlus/third UI.
- Affiliate and Studio modes coexist on Content Creation page.
- E-books and all listed formats are editable; export/media status is truthful.
- Any custom niche is accepted.
- Outputs include hooks, captions, CTAs, hashtags/keywords, destination and platform variants.
- Users can list/sell owned or licensed products and promote them via connected social accounts.
- Paid assets delivered only after verified payment; orders, entitlements, refunds and payouts auditable.
- Platform earnings, affiliate commissions, marketplace sales and lead revenue remain separate.
- Published posts show provider-confirmed IDs/URLs; analytics only provider-reported or instrumented metrics.
- Tests cover RLS, owner isolation, webhook verification, idempotency, payment delivery, approvals and publishing failures.
