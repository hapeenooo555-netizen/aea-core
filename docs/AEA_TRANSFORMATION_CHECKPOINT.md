# AEA Transformation checkpoint

## Phase 1 · Bilingual content objectives

26 November 2026. Baseline [Core #288](https://github.com/hapeenooo555-netizen/aea-core/pull/288) at `12a2980f03de488e8cae8635c7afca4521d512c6`, and [Hub #193](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/pull/193) at `f52cd5d7d175219e00fe5daf391a8745317bc6df`. The prior release verified e-book reading, Pin image persistence, and Marketplace linkage. [Release verification](https://chatgpt.com/library?file_id=libfile_285a7108a49081919fce6aeef8b8572d&account_id=personal)

### Implementation and acceptance evidence

| Criterion | Evidence at this checkpoint |
|---|---|
| Understand the goal | Tests cover 22 English and Swahili creator instructions. Supported Studio requests preserve the original prompt, content type, topic, and explicit output language. |
| Create a structured plan and select tools | The existing employee runtime validates `generate → save → read back` with named dependencies and registered Studio tools. A forward dependency is rejected. |
| Ask for essential input | A supported draft objective without a topic asks for the topic alone. An authorized topic already supplied with the mission is reused. |
| Retrieve authorized context | Studio readback includes both the asset ID and authenticated owner. Tests deny another owner access to the mission and saved asset. Atlas preference and unfinished-task retrieval remain pending. |
| Detect and recover from failures | Simulated provider rejection preserves failure evidence and retries generation. A mismatched readback retries verification without regenerating or inserting a new asset. |
| Resume and prevent duplicates | A repeated request ID returns the existing mission and draft. A changed objective with the same ID is rejected. The existing execution and step claims preserve checkpoints. Tests cover recovery after a lease expires. |
| Verify the outcome | A matching Studio version and content digest drive completion. If readback cannot be confirmed, exhausted verification reports a partial outcome with the saved asset reference. Unsupported requests do not complete through a log. |

Core files are `backend/app/services/studio_objective.py`, `employee_vertical_slice.py`, `plan_validator.py`, `mission_orchestration.py` and `agent_runtime.py`. The primary entry point is `POST /employee/objective`. The new regression suite is `tests/test_studio_objective_runtime.py`.

Local CI-equivalent regression before submission passed **410 tests**, with **one** opt-in live cross-user RLS smoke skipped. Provider generation, database behavior and interruption in the new tests run against controlled test doubles. Two extra, non-CI legacy tests failed on both the baseline checkout and this branch: `test_retry_limit_is_bounded_for_transient_failures` and `test_full_runtime_two_worker_race_has_one_side_effect`. The production Supabase schema has RLS enabled for missions, executions, steps, Content Studio versions, and Atlas memory. The three mission claim RPCs grant execution to authenticated callers and deny it to anon. This establishes schema and grant configuration. Cross-tenant live behavior for the new workflow still requires a QA session.

### Release gate and next action

PR, Actions, Render deployment and authenticated production verification are tracked in the linked implementation PR. Before claiming this milestone live, verify the merged SHA in Render, submit an English or Swahili QA objective with a stable request ID, repeat the request, and open the resulting private e-book through Hub. Confirm one asset version and persisted step evidence in Supabase. Keep QA drafts private and leave commercial data untouched.

Phase 1 remains in progress. The next Phase 1 milestone should use owner-scoped Atlas context for a specific creator workflow and bind contextual dispatch to real tools for existing assets and unfinished work. Cross-workflow planning, broader output quality evaluation, the Home workspace and Phases 2–9 remain pending. Pinterest and Meta external publishing tests are on hold pending designated QA destinations. Supabase full backup and restore verification remains an outstanding external blocker.

### Production follow-up · Structured generation recovery

| Release | Production commit | Deployment and CI |
|---|---|---|
| [Core #289](https://github.com/hapeenooo555-netizen/aea-core/pull/289) | `37593090cc9f913eabdb405e753f91cfc267caad` | [API](https://dashboard.render.com/web/srv-dbcde09srm7s738ei680/deploys/dep-dc49a2a9lscs73e2vcpg), [worker](https://dashboard.render.com/worker/srv-dbuuihd9fdbs73b204e0/deploys/dep-dc49a2a9lscs73e2vdig), [backend tests](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45890576817), [SBOM](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45890576300) |
| [Hub #194](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/pull/194) | `3c32e8e7480140b4173c20d33a436570a0a1879a` | [Hub](https://dashboard.render.com/web/srv-dbcrjsui0phs73fqcv8g/deploys/dep-dc49dk5bedkc73dc0hfg), [tests, lint and build](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/actions/runs/45891479415), [SBOM](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/actions/runs/45891479408) |

Authenticated production submission through Hub created mission `8cae3a6e-e9c1-4a9d-b01b-f89280a2cbea` and execution `9d14a78b-e74c-4790-b397-dcafb7cf37a4`. Core interpreted the request as a Swahili e-book. The first two fresh provider attempts returned draft objects missing required keys. Supabase recorded both failed attempts in the same execution and the Hub displayed the retry state. The save and verification tools had yet to run.

The recovery change uses an explicit response contract and a narrower brief on later generation attempts. It calls the provider again and preserves the same acceptance checks. Logs identify rejected property names without including draft text. The focused regression and existing Studio and employee suites passed 115 tests.

Next action after the recovery deploy is to use **Check task** on the existing QA mission, verify one freshly generated version in Supabase, and open the chapter text in Hub. The remaining Phase 1 and external blockers above still apply.

### Production follow-up · Groq schema constraint

[Core #290](https://github.com/hapeenooo555-netizen/aea-core/pull/290) deployed `e7f7f496947ba614ee44c078232ae521ff209b6c` on the [API](https://dashboard.render.com/web/srv-dbcde09srm7s738ei680/deploys/dep-dc49gujbc2fs739h61v0) and [video worker](https://dashboard.render.com/worker/srv-dbuuihd9fdbs73b204e0/deploys/dep-dc49gujbc2fs739h61s0). [Backend CI](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45892363645) and the [SBOM and license audit](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45892363596) passed on the merged commit. The third attempt resumed the same mission but Groq returned HTTP 400 from the configured GPT-OSS 120B model. No asset was saved. The logs establish the provider response status. They do not identify the provider's underlying reason for the HTTP 400.

Mission-based book generation now sends a strict JSON response schema on supported GPT-OSS models and still validates chapter body text before saving. Structured creation and other provider callers retain their normal path. The orchestration attempt limit now resolves the final rejected step as a failed execution instead of scheduling another retry. The local focused suite passed 147 tests.

A read-only Supabase check evaluated the deployed RLS policies under the authenticated role with two account identities. The owner could see the QA mission. The other account saw zero matching mission, execution or step rows. A second-browser-session test and an asset-level check after successful generation are pending.

Next action is to run one fresh, private QA e-book through the same Hub workspace after the strict-schema release. Record the new mission, the three dependent steps and their persisted outcomes. Open the result in the Hub reader before marking scenario A verified.

### Phase 1 production evidence and diagnostic follow-up

[Core #291](https://github.com/hapeenooo555-netizen/aea-core/pull/291) at `3371bab164bc4729f0a216963bcfdd1f86339b57` and [Hub #194](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/pull/194) at `3c32e8e7480140b4173c20d33a436570a0a1879a` generated and reopened a fresh seven-chapter English e-book through the authenticated Hub. Replaying the same goal yielded one mission, one execution and one private version. The asset is `805bb53a-a5e2-4ad2-82aa-4b7f23b7b2e2`. Read-only production tests confirmed the second account could not see the draft or the checked execution records.

The matched Swahili goal correctly resolved to the book tool and Swahili. Groq returned HTTP 400 on GPT-OSS 120B. Mission `2592e1af-1fbf-48e2-86b1-c59eb5ae1653` recorded a failed attempt with no saved draft. This evidence does not establish language as the cause.

The diagnostic change records allowlisted provider error codes, provider request identifiers and request shape sizes. It retries with a configured model only on structured-output validation errors or the existing rate-limit path. Explicit provider policy blocks terminate the generation. No raw provider payload or user content is logged. Next action is to retry the exact Swahili objective on the deployed diagnostic adapter and inspect the provider code before changing the generation request.
