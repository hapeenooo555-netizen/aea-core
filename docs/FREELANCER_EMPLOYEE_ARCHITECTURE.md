# AEA Freelancer Employee Platform Architecture

## 1. Product boundary
AEA Hub remains the only customer-facing workspace. AEA Core owns authenticated execution, durable state, AI employee orchestration, provider connectors, approval gates, audit records, and background workers.
Freelancer Employee is a vertical built on the existing employee runtime. It must not become a second orchestration system or duplicate mission, approval, connector, memory, or execution infrastructure.
This document defines the target architecture and delivery plan. It does not claim that all capabilities below are implemented.

## 2. Freelancing capability catalogue
| Vertical | Target services | Automation boundary |
| --- | --- | --- |
| Writing & copywriting | Articles, blogs, product descriptions, ad copy, email campaigns, landing-page copy | Draft and review; fact-check before delivery |
| Graphic & image design | Social posts, banners, flyers, thumbnails, product images, brand assets | Generate variants; user approves final assets |
| Video & audio production | Brief, script, storyboard, scene generation, narration, captions, edit, export | Provider-dependent; human review before delivery |
| Social media operations | Content calendars, captions, scheduling, publishing, moderation queues, reports | Only through connected APIs and granted scopes |
| SEO & digital marketing | Keyword clusters, audits, metadata, campaign plans, analytics summaries | External changes require approval |
| Virtual assistance | Inbox triage, response drafts, scheduling, research, document preparation | External messages and calendar changes gated |
| Web & software development | Requirements, UI/code generation, bug fixes, tests, documentation, deployment plans | Production deploys require explicit approval |
| Data & research | Web research, extraction, cleaning, spreadsheets, analysis, dashboards | Cite sources; validate data quality |
| Translation & transcription | Swahili/English translation, transcription, subtitles, proofreading | Human review for legal/medical/high-stakes content |
| E-commerce operations | Product catalogues, listings, descriptions, inventory summaries, support drafts | Price, inventory and order changes gated |
| Customer support | FAQ answers, ticket triage, response drafts, escalation and summaries | Refunds, account changes and binding commitments gated |
| Lead generation & sales support | Prospect research, qualification, CRM updates, outreach drafts and follow-up queues | Respect consent, platform policies and opt-out; sending gated |
| Documents & presentations | Proposals, reports, ebooks, business plans, pitch decks and slide content | Review claims, numbers and rights |
| Finance administration | Invoice drafts, expense categorization, reconciliation assistance, summaries | No autonomous payments, tax filing or certified accounting |
| Workflow automation | Scripts, API integrations, data sync, scheduled workflows and tests | Secrets isolated; destructive actions gated |

## 3. Employee catalogue
Use specialist profiles, not separate runtimes:
- Research Employee
- Writing & Copy Employee
- Graphic Design Employee
- Video Producer Employee
- Social Media Employee
- SEO & Marketing Employee
- Virtual Assistant Employee
- Web Developer Employee
- Data Analyst Employee
- Translator & Transcription Employee
- E-commerce Employee
- Customer Support Employee
- Lead Generation & Sales Employee
- Document & Presentation Employee
- Finance Admin Employee
- Automation & QA Employee
Each profile declares employee_type, supported capabilities, accepted input schema, required connectors, allowed tools, output/deliverable schema, risk policy, and quality checks. A user may assign multiple specialists to one client project.

## 4. Core execution architecture
Hub request -> Freelancer API -> project + mission -> MissionOrchestrationService -> Employee Runtime -> specialist capability plan -> Tool Registry / Connector Registry -> Approval Gateway -> durable MissionExecutionService -> artifacts and delivery records -> Hub.
Reuse authenticated user-scoped Supabase client and RLS; MissionOrchestrationService, EmployeeEngine / EmployeeVerticalSlice; MissionExecutionService, retry classification and recovery; ToolRegistry, ConnectorRegistry and capability discovery; ApprovalGateway and approval resume; existing content studio and video generation services; existing Meta, Pinterest, email and affiliate connectors where their scopes support the action; private Supabase Storage and signed artifact URLs.
Do not create a parallel generic agent runtime, approval table, or unscoped service-role API.

## 5. Target domain model
Retain the existing freelancer_projects table as the client/project container. Extend it carefully through additive migrations.
- freelancer_clients: client profile, contact details, timezone, preferences, owner_id.
- freelancer_services: service catalogue, description, pricing model, currency, active state.
- freelancer_projects: client brief, source, scope, status, budget, agreed amount, due date, client reference.
- freelancer_proposals: versions, scope, price, timeline, status, approval and submission evidence.
- freelancer_deliverables: project/mission link, artifact path, type, version, review status, checksum.
- freelancer_time_entries: user-approved time records and billable status.
- freelancer_invoices: invoice number, line items, amount, currency, due date, payment status; no payment execution by default.
- freelancer_messages: inbound/outbound message metadata, provider/thread IDs, approval and delivery state; avoid storing unnecessary message bodies.
- freelancer_reviews: client feedback and revision requests.
- freelancer_marketplace_connections: encrypted provider credentials/scopes and connection state, owner-scoped.
- freelancer_activity_events: immutable audit events for state transitions and external actions.
All tenant-owned rows include owner_id, enforce RLS, and use composite ownership checks when referencing related records. Provider secrets must be encrypted server-side and never returned to the Hub. Prefer signed URLs for private deliverables.

## 6. Mission templates and workflows
A. Find and qualify work: user supplies a listing URL/text or connects a permitted source; Research Employee extracts scope, budget, deadlines, requirements, red flags and questions; AEA calculates effort/risk and recommends pursue / clarify / decline; user confirms whether to create a project.
B. Proposal: create truthful profile and portfolio context from user-approved records; draft tailored proposal, scope, milestones, quote and timeline; run quality/compliance checks; user edits and approves; submit only if a marketplace connector and platform terms permit it, persist provider receipt or report manual submission.
C. Project execution: convert approved scope to mission plan and milestones; route subtasks to specialist employees; generate drafts/assets/code/data; run checks, collect approvals and save versioned artifacts; submit a delivery package; record revisions and acceptance.
D. Client communications: classify inbound requests and summarize context; draft replies and change requests; user approves messages, quotes, deadline changes and contractual commitments before send.
E. Operations and finance: track milestones, task status, approved time, expenses and deliverables; generate invoice drafts and payment reminders; reconcile only from connected read-only or authorized evidence; never initiate payments or represent an invoice as paid without verified transaction evidence.

## 7. API surface (target)
Keep current POST /freelancer/analyze and POST /freelancer/proposal-draft endpoints as the initial text-only slice. Extend with authenticated, owner-scoped APIs:
- GET/POST /freelancer/clients
- GET/POST /freelancer/services
- GET/POST /freelancer/projects, GET/PATCH /freelancer/projects/{id}
- POST /freelancer/projects/{id}/missions
- GET/POST /freelancer/projects/{id}/proposals
- POST /freelancer/proposals/{id}/approve
- POST /freelancer/proposals/{id}/submit (connector capability + explicit approval only)
- GET/POST /freelancer/projects/{id}/deliverables
- POST /freelancer/deliverables/{id}/review
- GET/POST /freelancer/projects/{id}/time-entries
- GET/POST /freelancer/projects/{id}/invoices (draft only)
- GET /freelancer/dashboard/summary
- GET /freelancer/activity
All write endpoints validate project ownership. External actions are idempotent, auditable, and approval-gated.

## 8. Provider strategy
Marketplace integrations are optional adapters, not a dependency for core project management. Begin with manual listing import/paste and manual proposal status. Add Upwork, Fiverr, Freelancer.com or other providers only where documented APIs, account access, platform terms and permitted scopes support the requested operation. Never automate browser interaction to bypass platform controls or claim a proposal was sent without provider confirmation.
For work delivery, use existing content/video/document capabilities and add provider adapters only as required. Store OAuth tokens encrypted; apply least-privilege scopes and revocation.

## 9. Risk and approval policy
No approval needed: private analysis, drafting, internal planning, local generation and saving owner-scoped drafts.
Explicit approval required: sending proposals/messages, publishing posts, sharing files externally, changing prices/quotes/deadlines, accepting a contract, issuing invoices, making account changes, deploying code, or modifying client production systems.
Never autonomous: payments/transfers, impersonation, fabricated portfolio/credentials, fake reviews, illegal scraping, bypassing rate limits, or regulated professional decisions.

## 10. Delivery phases
Phase 0 — Architecture and gap audit (this document).
Phase 1 — Project foundation: clients, services, project lifecycle, dashboard, activity and RLS.
Phase 2 — Proposal workspace: portfolio/profile context, proposal versions, quote/milestone drafts and approvals.
Phase 3 — Delivery engine: mission templates, specialist routing, artifact versioning, review and revision workflow.
Phase 4 — Creative service verticals: image, video, documents, presentations and social publishing through existing engines.
Phase 5 — Virtual assistant, research, data, translation, support and CRM integrations.
Phase 6 — Marketplace adapters, only after API/terms/access verification.
Phase 7 — Invoicing, time tracking, financial summaries and client reporting.

## 11. Current implementation status (verified from main branch source review)
Existing first slice: POST /freelancer/analyze analyzes user-pasted job text using transparent keyword heuristics; POST /freelancer/proposal-draft creates a proposal draft and warns against invented experience/commitments; /freelancer/projects supports basic owner-scoped project CRUD and status, proposal text, amount, currency and notes.
Not implemented by this first slice: marketplace discovery or automatic job ingestion; marketplace OAuth or proposal submission; specialist freelancer employee catalogue and dynamic routing; client CRM, portfolio, milestone/task management; deliverable version/review workflow; client messaging integration; time tracking, invoicing and payment reconciliation; freelancer dashboard/analytics.
This architecture is a target plan. Do not describe planned capabilities as live until code, migrations, tests and provider evidence exist.