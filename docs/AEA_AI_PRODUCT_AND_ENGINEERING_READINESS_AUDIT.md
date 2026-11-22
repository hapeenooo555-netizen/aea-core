# AEA AI Product & Engineering Readiness Audit

**Scope:** AEA Core and AEA Affiliate Hub, reviewed against the current `main` source trees and owner-approved product direction available on 2026-11-22.

**Purpose:** establish an evidence-led overhaul plan for AEA as an AI Employee Operating System, not merely a collection of AI-powered forms and generators.

## 1. Owner intent and product thesis

AEA is intended to become an AI Employee Operating System. AEA Core is the authenticated orchestration, intelligence, execution and governance engine. AEA Affiliate Hub is the single user-facing workspace.

The product should help creators, affiliate marketers, entrepreneurs, small businesses and service providers move from an objective to measurable business outcomes. AEA should be able to understand a goal, gather context, research with provenance, plan, select capabilities, create work, operate connected tools, request human approval at the right risk boundary, persist outputs, recover interrupted work and evaluate results.

The Hub should unify, not fragment, these workflows:
- AEA Core / Content Creation, including affiliate and non-affiliate content.
- Graphic Design Studio and Video Production Studio.
- My Store as the shared, intelligently classified asset library, with commerce products kept distinct from production assets.
- Affiliate Marketplace and promotion-application workflows.
- Creator Community, with public/private visibility, age and safety controls, and moderation.
- Freelancing, including projects, clients, services and employee-assisted workflows.
- Leads and CRM, beginning with the currently implemented WhatsApp lead workflow and expanding only as verified channels are added.
- Integrations, approval workflows, publishing and analytics.
- Regional/country intelligence, natural Swahili and international-market support.
- Human oversight, secure multi-tenant data boundaries, cost transparency and measurable outcomes.

Product principle: improve the execution method without replacing the user's actual intent, facts, language, constraints or requested outcome. Clearly distinguish generated drafts, provider-confirmed media, provider-authorized connections, external publication and measured business outcomes.

## 2. Current architecture observed

### AEA Core
- FastAPI backend, Supabase/Postgres persistence and RLS, authenticated owner-scoped API dependencies.
- LLM abstraction with Groq and Mock providers, model registry, prompt intelligence and domain-specific content/design intelligence.
- Mission, worker, employee and orchestration services; durable mission execution, retries, recovery, approvals and human intervention.
- Tool contracts, capability discovery, validation, risk metadata and connector registry.
- Pinterest and Meta integration foundations, OAuth, account discovery, publishing controls and webhook paths.
- Content Studio, graphic design, video production/assembly, creator store/marketplace, creator community, freelancer and marketing/lead modules.
- Country intelligence, revenue attribution, research-source modules, Swahili language quality/research and brand intelligence.
- A broad unit/integration test suite and GitHub Actions for backend tests and SBOM/license checks.

### AEA Affiliate Hub
- TanStack Start, React, TypeScript, Tailwind, Supabase Auth session handling and authenticated Core API client.
- Single user-facing app with routes for dashboard/content and video workspace, graphic design, My Store, marketplace, creator community, freelancing, WhatsApp leads, marketplace admin and integrations.
- Product-specific UI workflows, mobile navigation and accessibility foundations.
- Existing product architecture explicitly says the Hub is the only user-facing interface; Core executes backend workflows.

## 3. Evidence-led assessment: what exists vs what is not yet proven

### Implemented in source (not automatically live-verified)
- Durable mission/execution records and state transitions.
- Approval request persistence, approval/resume services and risk classification.
- Tool contracts, user-scoped capability discovery and validation.
- Owner-scoped API authentication and JWT-bound Supabase clients.
- LLM provider abstraction and Groq model failover on rate limits.
- Structured content generation and explicit asset save endpoints.
- Private graphic-design storage and signed asset URLs.
- Video project/scene generation, recovery and durable assembly queue.
- Pinterest OAuth/connector/publish workflow and Meta OAuth/webhook/account discovery.
- Store, marketplace, community, freelancer and lead API modules.
- Model registry with capabilities for text, image, video, speech, transcription, subtitles and embeddings.
- Numerous focused tests for security, workflow and generation behavior.

### Gaps or limitations visible in source
1. **No single canonical AI execution path.** The code has legacy `EmployeeEngine`/worker runtime, `AgentOrchestrator`, `EmployeeVerticalSlice` and `MissionOrchestrationService`. Some overlap is documented as compatibility, but a single versioned orchestration contract and migration/deprecation plan are needed.
2. **Planning remains partly deterministic.** `PlanningEngine` is keyword-based for echo/count and otherwise creates a log step. `ObjectiveParser` and the bounded employee slice are more advanced but conservative and should not be mistaken for general-purpose autonomous planning.
3. **Tool execution and decision intelligence are not yet a mature dynamic agent platform.** Tool registry is in-memory; contracts/discovery are useful foundations, but production-grade versioned tool catalogues, typed output validation, per-tool policy, telemetry and broad capability coverage are needed.
4. **Memory is not yet modern semantic/episodic learning.** Current `AtlasMemoryEngine` stores owner-scoped entries and searches by keyword after loading records; no embedding/vector retrieval was found in this implementation. It must not be marketed as autonomous learning from outcomes.
5. **Provider configuration can default to Mock.** `LLM_PROVIDER` defaults to `mock`; unknown provider names also resolve to Mock. Health correctly flags mock as degraded, and generation endpoints block mock in some cases, but production boot should fail closed or explicitly mark AI features unavailable. Provider health must include a real, bounded inference probe and model/capability compatibility.
6. **Model registry is not yet a full inference router.** The current registry is owner-scoped and routes by capability, but production routing needs server-governed provider credentials, policy, latency, cost, quality, fallback, data residency and evaluation metadata. Browser/user-supplied model configuration must never hold secrets.
7. **Research provenance is incomplete as a product-wide contract.** Research modules and source directories exist; every claim used in a deliverable needs retrieval timestamp, source URL/provider, source excerpt/hash, confidence and whether it is observed, inferred or hypothetical. A listed source or country macro indicator is not evidence of product demand.
8. **Evaluation and quality measurement are not yet a complete AI evaluation system.** Many unit tests exist, but a versioned benchmark dataset, task success metrics, model/prompt regression evaluations, human preference review, hallucination/grounding measures, language-specific evaluations and production outcome monitoring are needed.
9. **Observability is not yet investor-grade AI operations.** Add end-to-end trace IDs across request, mission, step, tool, model, media provider and publication; structured redacted logs; latency/token/cost/retry/error metrics; SLOs and incident history.
10. **Shared asset lifecycle is incomplete across studios.** Content Studio, Graphic Design and Video have separate persistence models and retrieval paths. A unified owner-scoped asset index, type taxonomy, project/version/variant relations, provenance and private-media lifecycle are required; do not duplicate large binary media unnecessarily.
11. **Hub route and feature maturity are uneven.** Existing routes are present, but there are no dedicated Publishing Calendar or Analytics routes in the inspected tree. Integrations currently verifies Meta and Pinterest, not every requested social platform. WhatsApp Leads is not yet a generalized multi-channel CRM.
12. **Authentication token persistence is a security design trade-off.** Hub currently persists access and refresh tokens in browser localStorage (with sessionStorage migration). This is exposed to JavaScript execution and XSS. Evaluate a BFF/HttpOnly Secure SameSite cookie architecture or Supabase's recommended hardened session strategy; migrate with CSRF protections, refresh rotation and backward-compatible session handling.
13. **Deployment evidence is split from source evidence.** Core Render deployment was confirmed live at the inspected commit. Hub UI tests failed in the current PR due to asset taxonomy normalization (fixed on branch; rerun queued at audit time). No claim of Hub production verification should be made until the corrected checks and live smoke tests pass.
14. **Some API/service paths can hide infrastructure errors.** Defensive fallbacks to empty lists or in-memory stores are useful for tests but must not silently imply durable production success. Production paths should fail closed, expose explicit degraded states and emit actionable diagnostics.

## 4. Target architecture: AEA Intelligence & Employee Platform

Adopt a layered architecture with explicit contracts. Avoid replacing working business modules in a single risky rewrite.

### Layer A — Experience and tenant workspace (Hub)
- One workspace, role-aware navigation and consistent design system.
- User profile, organization/workspace, brand profile, locale, timezone, country and permissions.
- Shared project workspace across text, visual and video studios.
- My Store Asset Library separated from My Products/Commerce.
- Approval inbox, publishing calendar (when supported), integration centre, analytics and audit history.
- Accessible responsive UX, English and natural Tanzanian Swahili first, extensible localization.

### Layer B — AI Gateway and model routing (Core)
- Provider-neutral inference gateway for text, vision, image, video, audio, speech, transcription and embeddings.
- Server-side provider credential vault; model registry is metadata, not a secret store.
- Capability, policy, model version, region/data handling, cost and quality-aware routing.
- Bounded timeout, retry, circuit breaker, fallback and quota budgets.
- Strict structured output schemas, JSON validation and safe refusal/error contracts.
- Explicit production mode: no silent Mock fallback. Mock only in test/development profiles.

### Layer C — Intelligence and context
- Intent understanding: desired outcome, constraints, deliverables, audience, market, language, budget, deadline, risk and success criteria.
- Business/brand context with owner-approved persistent facts and provenance.
- Research planner and source retrieval with evidence records.
- Semantic + keyword retrieval over user-authorized memory and assets; episodic task history and reusable procedural knowledge.
- Country/audience intelligence with explicit distinction between observed analytics, external research, model inference and hypotheses.

### Layer D — Planning and agent runtime
- One canonical objective → plan → validate → execute → observe → evaluate → report state machine.
- Typed DAG/workflow plans with dependencies, budgets, deadlines, checkpoints and bounded iteration.
- Capability discovery based on verified connections, scopes, tenant permissions and current tool health.
- Versioned tool contracts, input/output schemas, timeouts, idempotency, retry classifications and side-effect declarations.
- Durable queue, leases, cancellation, pause/resume, replay protection and exactly-once business effects where feasible.
- Specialized employees (research, content, design, video, affiliate, CRM, publishing, analytics, freelancer) coordinated by a lead orchestrator; specialists are bounded services/agents, not decorative personas.

### Layer E — Trust, safety and governance
- Risk tiers: read-only, reversible internal write, external communication/publication, financial/contractual, destructive/high-impact.
- Explicit approval policies by action, amount, channel, user preference and tenant policy.
- Approval bound to immutable payload hash, tool version, destination and expiry; any material edit invalidates approval.
- Prompt-injection defence for user content, external pages, comments, files and tool output; all retrieved content is untrusted data.
- PII/secret redaction, encryption at rest/in transit, key rotation, tenant isolation, audit events and least privilege.
- Clear distinction between model suggestion, planned action, executed action and externally confirmed result.

### Layer F — Production and business outcomes
- Shared asset lifecycle and private media storage.
- Platform-specific validators and distribution adapters. Never infer platform capability from brand identity OAuth alone.
- Scheduling by target audience timezone and verified platform insights; fallback recommendations explicitly labelled estimates.
- Attribution, leads, conversion, revenue and cost tracking with source confidence.
- Feedback loops based on measured outcomes and user corrections; do not automatically train on private data without explicit governance/consent.

## 5. AI standards and evidence framework

AEA should align its management system and engineering controls to recognized standards/guidance, with a claim-by-claim compliance map:
- ISO/IEC 42001: AI management system, accountable ownership, risk treatment, lifecycle governance and continual improvement.
- ISO/IEC 23894: AI risk management integrated with enterprise risk.
- NIST AI Risk Management Framework 1.0: Govern, Map, Measure, Manage; use the Generative AI Profile where applicable.
- ISO/IEC 27001 / 27017 / 27018 and SOC 2 control mapping for information security, cloud controls and personal data.
- OWASP Top 10 for LLM Applications and API Security Top 10: prompt injection, sensitive information disclosure, improper output handling, excessive agency, insecure plugin/tool design, authentication/authorization and resource exhaustion.
- W3C WCAG 2.2 AA for Hub accessibility.
- OpenTelemetry-compatible traces/metrics/logs for operational observability.

These are target frameworks, not certifications. AEA must not claim certification, conformance or compliance until a qualified independent assessment supports the claim.

## 6. AI evaluation programme — proof that AEA is AI

Build a version-controlled AEA Evaluation Harness. Every release candidate must run:
1. **Intent and instruction fidelity:** preserve user goal, constraints, language, brand facts and requested deliverable.
2. **Planning quality:** plan completeness, dependency correctness, tool selection, feasibility and budget adherence.
3. **Tool correctness:** schema validity, correct capability selection, permissions, destination and output validation.
4. **Groundedness:** unsupported claim rate, source coverage, freshness, citation correctness and uncertainty calibration.
5. **Task success:** end-to-end objective completion, verified outputs, partial failure reporting and recovery success.
6. **Safety:** approval bypass attempts, cross-tenant data access, prompt injection, secret leakage, unsafe external side effects and idempotency.
7. **Language quality:** natural Swahili (Tanzania), English and later locale-specific benchmarks; human-reviewed terminology, grammar and meaning preservation.
8. **Media quality:** visual relevance, text accuracy, brand consistency, aspect ratio, legibility, audio intelligibility, scene continuity and output integrity.
9. **Reliability:** latency percentiles, provider failures, retry rates, queue age, recovery, cost per successful task and uptime.
10. **Human evaluation:** blind pairwise preference, expert scoring, user edit distance and approval/rejection reasons.

Each benchmark records dataset version, test case, expected criteria, model/provider/version, prompt/tool version, score, failure examples and reviewer. Use held-out evaluation sets and regression thresholds. Do not optimize solely for aggregate scores; publish per-task/per-language/per-risk breakdowns.

Initial release gates (proposed, calibrate using baseline):
- 100% pass on critical authorization, secret leakage and approval-bypass tests.
- 100% structured-output schema validity after bounded repair or explicit failure.
- >=95% correct tool/capability selection on curated supported-task benchmark.
- >=90% completion of defined golden workflows without unsafe side effects.
- 0 fabricated provider success IDs or publication claims.
- 100% source/provenance labels on research-grounded outputs.
- Cost and latency budgets defined per task class and enforced.
These are proposed internal gates, not externally certified AI standards.

## 7. Investor due-diligence package

Prepare a truthful, reproducible data room:
- Product thesis, target users, use cases and differentiation.
- Architecture diagrams and data-flow/tenant-boundary diagrams.
- Feature maturity matrix: implemented, unit-tested, integration-tested, live-verified, limited beta, roadmap.
- Benchmark methodology, versioned results and known limitations.
- Security threat model, data retention, consent, privacy, incident response and access-control evidence.
- Provider/model dependency map, fallback plan, rate limits and cost economics.
- Deployment history, CI, test reports, uptime/latency/cost dashboards.
- Real pilot case studies with baseline, intervention, measured outcomes and customer permission.
- Unit economics: cost per successful workflow, gross margin by task, retention, activation and conversion.
- IP ownership, third-party licenses, data rights, media-generation rights and vendor contracts.
- Roadmap, staffing needs, risk register and capital allocation.
Never present synthetic demos or generated sample metrics as customer traction.

## 8. Prioritized overhaul roadmap

### P0 — Establish truth and release safety
- Complete source inventory, route/API/data ownership map, service dependency map and deployed-version map for both repositories.
- Fix failing tests before merging existing Hub asset taxonomy work.
- Separate production, staging and test provider configuration; fail closed on missing/unknown providers.
- Create capability/feature status endpoint that reports configured, reachable, tested and live-verified separately.
- Review auth storage, admin authorization, RLS, service-role usage, secrets, CORS and webhook validation.

### P1 — Canonical AI runtime
- Select one canonical orchestration API and state machine; maintain compatibility adapters during migration.
- Add a versioned tool catalogue and dynamic capability discovery.
- Add structured plan schema, DAG validation, task budget and success criteria.
- Add execution trace/telemetry and immutable audit events.
- Add durable semantic memory and provenance, with tenant/user consent and deletion policies.

### P2 — AI evaluation and quality
- Build golden task datasets from real AEA use cases, including Swahili, English, Tanzania, Kenya, Nigeria, South Africa, US and UK scenarios.
- Add offline benchmark and CI regression gates.
- Add independent human review workflow and quality dashboards.
- Add production monitoring, model drift and cost/quality evaluation.

### P3 — Unified production and asset lifecycle
- Canonical project/asset/version/variant schema.
- Connect Content Studio, Graphic Design and Video to the same owner-scoped library.
- Separate asset library from commerce products and listings.
- Add preview, edit, version, export, approval and provenance.
- Ensure video and media output is confirmed from storage/provider before marking complete.

### P4 — Business operations and distribution
- Common integration adapter contract with per-provider capability/permission verification.
- Publishing Calendar and scheduler with timezone and audience insight provenance.
- Expand publishing only after provider-specific API permissions and end-to-end tests.
- CRM source abstraction; preserve current WhatsApp flow as one verified channel.
- Analytics/attribution and closed-loop recommendations based on actual data.

### P5 — Enterprise and investor readiness
- ISO 42001 / NIST AI RMF control mapping and independent gap assessment.
- Security testing, penetration test and privacy impact assessment.
- SLOs, incident management, backups, disaster recovery and support operations.
- Pilot evidence, customer references, unit economics and investor data room.

## 9. Non-negotiable engineering principles

- One Hub user experience; Core remains the backend execution engine.
- One canonical execution state machine; no competing mission truth sources.
- Tenant/owner isolation enforced server-side and at the database.
- No silent mock responses or false success.
- No publishing, messaging, purchases, applications or irreversible external action without the required verified permissions and approval.
- No invented market facts, audience activity, integrations, provider IDs, revenue or customer outcomes.
- Every agent action is attributable, auditable, bounded and recoverable.
- Every AI capability has a benchmark and observable production quality/cost.
- Every feature has a maturity label and documented limitations.
- Preserve existing working user journeys during incremental migration.

## 10. Audit limitations

This is a source-code and repository-level architecture review, not a complete independent security audit, penetration test, live customer validation, database production inspection or formal standards certification. Live behavior, secrets, account permissions, database policies and deployment settings require separate controlled verification. All findings must be updated as code and deployments change.
