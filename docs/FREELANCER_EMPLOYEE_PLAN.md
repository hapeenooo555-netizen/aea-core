# Freelancer Employee — implementation plan

## Goal
Add a Freelancer Employee as a business capability inside AEA-Core without replacing the existing Affiliate Employee or breaking the current mission/worker/approval architecture.

## Current baseline observed
- AEA-Core is a FastAPI AI Employee Operating System with mission and worker routers and worker/mission execution services.
- The repository already contains affiliate-specific work, including Pinterest draft/approval flows.
- Existing project documentation explicitly marks some production checks and live integrations as unverified. A passing unit test or a merged PR must not be treated as proof of live marketplace access or successful client delivery.

## Design principles
1. Reuse AEA-Core's Mission Engine, Worker Runtime, Decision/Action services, Tool Registry, durable execution, owner-scoped authorization, and Approval Gateway.
2. Keep business capabilities separate: `freelancer` and `affiliate` are different employee profiles/workflows, not separate core systems.
3. Every task, project, proposal, artifact, and approval must be owner-scoped. Do not expose one user's client data to another.
4. Store secrets only in server-side environment/configuration or the approved secret store. Never put marketplace credentials or access tokens in mission metadata, logs, or generated artifacts.
5. Start with manual/assisted marketplace operations. Do not scrape websites, automate browser clicks, spam proposals, accept contracts, send messages, or commit to prices without explicit user approval and platform authorization.
6. Clearly separate proposed/estimated revenue from funds actually received.

## Employee profile
Suggested stable identifier: `freelancer`.
- Display name: AEA Freelancer Employee
- Mission types: `freelancer.job_analysis`, `freelancer.proposal_draft`, `freelancer.project_plan`, `freelancer.deliverable_create`, `freelancer.quality_review`, `freelancer.delivery_package`
- Initial supported services: writing, social content packages, video scripts, SEO briefs and other well-defined digital deliverables.
- Out of scope for first release: unattended bids, contract acceptance, payments/withdrawals, and unsupported marketplace automation.

## Proposed workflow
1. Operator or client supplies a job description, requirements, budget, deadline and source URL (if any).
2. Job Analysis Worker extracts deliverables, constraints, risks, estimated effort and clarification questions.
3. Proposal Worker drafts a tailored proposal and suggested price/rationale. It does not submit automatically.
4. Human reviews and approves the proposal before any external submission.
5. After the job is won and terms are confirmed, Project Planner creates task steps and acceptance criteria.
6. Execution Workers generate the requested artifacts using only registered tools.
7. QA Worker checks requirements, factual consistency, formatting and missing inputs; failed checks return the work to execution.
8. Delivery Worker prepares a downloadable/shareable package and a draft client message. External delivery requires approval unless a specific authorized connector and policy allow it.
9. Earnings Tracker records quoted amount, agreed amount, platform fees, tool costs, invoiced amount and confirmed received amount as distinct fields.

## Data model guidance
Prefer extending existing mission/execution structures only where appropriate; avoid duplicating the core execution engine.
Potential domain records, if not already represented:
- freelancer_projects: owner_id, source, source_url, client_reference, title, requirements, budget_currency, budget_amount, deadline, status
- freelancer_proposals: project_id, owner_id, proposal_text, price_quote, status, approved_by, submitted_at
- freelancer_deliverables: project_id, owner_id, artifact_reference, version, qa_status, approved_at, delivered_at
Use migrations with owner-scoped RLS, indexes, foreign keys, idempotency where needed, and tests proving cross-owner reads/writes are denied. Before creating tables, inspect existing schema to avoid duplicate concepts.

## API/UI guidance
Add authenticated, owner-scoped endpoints only after inspecting existing router conventions:
- create/analyze a project
- generate/update a proposal draft
- approve a proposal draft (approval records decision; external submission remains separate)
- create a project plan
- run QA and prepare a delivery package
- list project status and earnings
Use the existing ChatPlus/dashboard patterns rather than creating a second independent application. Include explicit statuses: draft, needs_review, approved, in_progress, qa_failed, ready_for_delivery, delivered, cancelled, blocked.

## Marketplace integration policy
- Treat each marketplace as an optional connector with its own capability flags and authorization status.
- If official API access is unavailable or not approved, support manual job details and manual submission without scraping or browser automation.
- Upwork's current published guidance says unauthorized automation can lead to account restrictions and that API access is subject to review and eligibility. Verify current terms and access before implementing any Upwork connector.
- Never claim a proposal was submitted or a client was contacted unless a successful, verifiable response is stored.

## Minimum test plan
- Unit tests for job analysis, proposal generation contract, task planning and QA status transitions.
- API tests for authentication, owner isolation, invalid payloads, duplicate/idempotent submissions and approval gates.
- Tests that proposal creation never calls an external submit action.
- Tests that only approved/authorized connector capabilities can perform external actions.
- Regression tests for existing affiliate/Pinterest routes and approvals.
- CI plus deployment verification before declaring release complete.

## Implementation sequence
1. Inspect existing mission, worker, approval, auth, migration, and UI conventions.
2. Implement typed domain models and a small service layer using existing execution primitives.
3. Add tests first for owner isolation and approval gating.
4. Add the first writing/content deliverable workflow.
5. Add UI to review proposal and deliverable drafts.
6. Add marketplace connectors only after platform permission and API access are confirmed.
7. Verify CI, Render deployment, and a real owner-authenticated smoke test.

## Acceptance criteria
- AEA-Core and the Affiliate Employee remain operational.
- Freelancer work uses existing mission/execution and approval infrastructure.
- Every new record is owner-scoped and covered by tests.
- Proposals and client-facing deliveries are drafts until explicitly approved.
- No unsupported scraping or automatic marketplace interaction is introduced.
- CI passes, deployed health checks pass, and smoke-test evidence is recorded before claiming completion.

## Implementation status — 2026-10-26

The first code slice is now present on `feat/freelancer-employee-foundation`:
- `backend/app/services/freelancer_employee.py`: heuristic analysis of user-provided job descriptions and draft-only proposal generation.
- `backend/app/routers/freelancer.py`: authenticated `POST /freelancer/analyze` and `POST /freelancer/proposal-draft` endpoints.
- `backend/app/main.py`: registers the Freelancer router.
- `tests/test_freelancer_employee.py`: service tests for job analysis, blank input, draft-only status, and avoiding invented experience.

Verification evidence:
- GitHub Actions workflow **Backend tests**, run **#273** (run ID `40356241848`), completed successfully on the PR commit; all listed job steps, including changed-module tests, passed.
- Production smoke testing of the deployed Render service was not confirmed from this environment.
- Marketplace search/submission, persistent freelancer projects, deliverable storage, client delivery, and earnings reconciliation are not implemented in this first slice.
- Keep this pull request in draft until a production smoke test and review of the full diff are complete.
