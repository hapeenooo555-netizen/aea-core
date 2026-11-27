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

### Phase 1 continuation · Structured fallback and Pinterest regressions

[Core #292](https://github.com/hapeenooo555-netizen/aea-core/pull/292) at `d3405792309e4430a7be553dcb0e37bc44f0a29c` reproduced the Swahili failure on the authenticated retry. Render logged `json_validate_failed` on GPT-OSS 120B and 20B. The final JSON Object Mode fallback returned an object missing all six required book fields. No asset was saved. The evidence identifies structure validation failures. The reason both GPT-OSS models rejected the particular output is unknown.

The configured Qwen fallback now receives the same strict schema as the GPT-OSS models, matching [Groq's documented capabilities](https://console.groq.com/docs/structured-outputs#supported-models). A focused test reproduces both initial 400 responses and checks the exact Qwen request. No provider or service was added.

The two Pinterest baseline failures were reproduced. The retry fixture injected a field into metadata that never reached the registered connector. It now injects a connector timeout and verifies bounded retries and a single operation key. The race fixture synchronized the one-worker approval step and waited for a second worker that would never arrive. It now synchronizes the contested status step and verifies one status read and one pending approval. Both suites run in CI. No external publishing was performed.

Next action is to reopen the Swahili QA task after the fallback release, check the actual provider response, then generate fresh Swahili and English drafts from Hub and reopen both in My Store.

### Production verification after structured fallback

[Core #293](https://github.com/hapeenooo555-netizen/aea-core/pull/293), `dd922e43f0623d54d405bd355610dc0b04278412`, deployed to the [API](https://dashboard.render.com/web/srv-dbcde09srm7s738ei680/deploys/dep-dc4ho3g473hc739jshu0) and [worker](https://dashboard.render.com/worker/srv-dbuuihd9fdbs73b204e0/deploys/dep-dc4ho38473hc739jshr0). [CI](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45948793065) passed with the two previously failing Pinterest tests enabled.

- Swahili mission `96c29006-73b9-4fd1-b8a2-2699a8ea833c` completed and persisted private version 1 at `d0a28161-9535-4d3a-9d26-2f223c23a1bc`. Hub reopened all three chapters. The user-specified title was changed and some chapter subjects drifted. Generation and reopen passed. Editorial quality failed.
- English mission `80985977-dda6-472c-8cb2-2e978ac1f927` completed and persisted private version 1 at `5d9a5bf8-cfd4-492a-a62c-39a6e3a6a6d5`. Hub reopened all seven readable chapters. The exact title and stock-check topic were preserved.

### Atlas profiles and e-book quality

Creator preferences use existing `agent_memory_entries` owner-indexed storage. An authenticated endpoint replaces the current language, audience and business objective settings. A matching goal inserts an owner-scoped recall step before generation. The execution checkpoint preserves what was recalled across retries and explicit output language outranks the saved preference. Tests confirm the preference reaches the provider brief, survives resumption and remains isolated from another owner.

Book missions reject obvious language mismatch, repeated chapter bodies and substantial topic drift before saving. If the title was given in a supported explicit form, it is preserved in the draft. These checks do not establish full editorial quality, completeness for every requested scope, or suitability for every audience.

| Phase 1 criterion | State at #293 plus local Atlas and quality tests |
|---|---|
| Bilingual natural-language understanding | Pass for the implemented book intent, with English and Swahili production evidence |
| Bilingual content generation | Pass for fresh private book draft persistence and reopen. The Swahili editorial result failed |
| Context-aware follow-ups | Blocked on implementation of previous asset and unfinished-task resolution |
| Atlas recall influencing execution | Pass locally for explicit preference recall. Authenticated production verification pending |
| Structured planning | Pass for book save and verification. Atlas dependency plan passes locally |
| Intelligent tool selection | Pass for supported book objective and provider capability selection. Paraphrases and compound follow-ups remain open |
| Controlled recovery | Pass for diagnosed structured-output fallback in production. Further timeout and recovery scenarios remain open |
| Quality evaluation | Fail on the first Swahili production sample. New checks pass locally. Production retest and broader quality verification pending |
| Interrupted-task resumption | Durable resume observed for existing Swahili retries. Controlled active-task interruption beyond tests remains open |
| Duplicate prevention | Pass for replay of the prior book mission and duplicate operation-key regression. Product and external-action scenarios remain open |
| Owner-scoped isolation | Production SQL proved book and execution isolation. Atlas endpoint and execution isolation pass locally, with live account testing pending |
| Production functionality | Bilingual book generation and reopen verified on #293. Remaining Phase 1 acceptance criteria are open |

Next actions are to deploy the Atlas and quality changes, run fresh e-book verification, wire saved preferences into Hub, and resolve follow-up goals against the owner's saved assets and unfinished tasks. Pinterest and Meta external publishing remain paused. Supabase full backup and restore verification remains blocked externally.

### Production verification of Atlas and the quality gate

[Core #294](https://github.com/hapeenooo555-netizen/aea-core/pull/294) at `cb20836d5d4e19a5e14fbfc9f09e4cb84f698b5c` and [Hub #195](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/pull/195) at `277d2a6f433b00c8f6470f20a62fb6c178386459` are live. [Core CI](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45949698230) and [Hub CI](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/actions/runs/45949965227) passed on the merged commits.

- Swahili mission `4db7209d-7b64-4da8-a45b-ff4e8843d333` passed the new checks and saved the exact requested title as private asset `2a37bc3d-753e-4b32-9e0f-f41ac5f3c929`. The authenticated reader reopened three readable, on-topic Swahili chapters covering inventory records, counting, and correcting discrepancies.
- Hub saved a temporary profile for Atlas mission `b3f0d2ca-5f97-4859-9551-53b9b58b8ef4`. Its persisted recall step recorded English, the QA audience, and the education objective. The original settings were restored. Retrying resumed the same execution with its stored recall step. The two generation attempts failed topical-heading and chapter-completeness checks respectively, before saving any asset. This verifies recall and checkpoint continuity. It does not verify a successfully generated book using those preferences.
- Production RLS returned one creator profile row under the owner identity and zero under another account identity.

The next supported follow-up locates a readable e-book already saved to the authenticated user's My Store. It accepts a recent-book reference or an exact title, asks how to disambiguate multiple matches, and does not call the generation provider. Local tests cover English and Swahili paraphrases, account isolation, ambiguity and non-duplication. Translation, chapter revision, Pin preparation and commerce follow-ups are still open. The next release check is to submit a search for the exact verified Swahili title through Hub and confirm the same asset ID and version are returned.

### Verified production checkpoint · 27 November 2026

[Core #295](https://github.com/hapeenooo555-netizen/aea-core/pull/295) at `3bfd93233ddcbc015d58cdf95e318de5ecc232eb` and [Hub #196](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/pull/196) at `ea234c2c0a03aa7767ccb4aeb0fb772ca50aa8f8` are live. Backend, web app, SBOM and license checks passed on both merged commits. [Core CI](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45950785133) · [Hub CI](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/actions/runs/45951016908)

Authenticated Hub lookup mission `a7250082-6ab5-47a9-a9b0-c9fa1d3b3567` selected only `studio.open_saved_book`. It returned the existing Swahili e-book `2a37bc3d-753e-4b32-9e0f-f41ac5f3c929`, version 1. SQL confirmed one asset version. Hub displayed the exact title as found and the owner reopened its three chapters. No generation call or new version was part of this lookup.

Atlas article mission `1e21f218-1c22-4544-8328-90786bc65b00` saved private asset `e0496cd7-ed58-49ed-a29b-56f74e0dd996` on the first attempt. The output recorded the recalled English preference, QA audience, and education objective. The account's initial effective preferences were restored. The Atlas e-book control terminated after three quality rejections in execution `3201b4dd-e6e3-46f4-92f6-422dc340c9f5` without saving an asset.

| Remaining Phase 1 gate | State |
|---|---|
| English and Swahili natural-language understanding | Pass for verified generation and saved-book lookup intents. Broader paraphrase testing pending. |
| English and Swahili content generation | Pass for fresh private e-books reopened in Hub. Swahili retest matched the requested title and topic. Atlas article also passed. |
| Context-aware follow-up instructions | Pass for read-only saved-book lookup. Translation, chapter edits, unfinished-task reference, Pin and commerce follow-ups remain open. |
| Atlas recall influencing execution | Pass for a production article and for a persisted retry checkpoint. Broader business and execution-history recall remains open. |
| Structured planning | Pass for implemented create, recall and lookup plans. |
| Intelligent tool selection | Pass for supported workflows and strict-schema provider fallback. Broader and multi-step objectives remain open. |
| Controlled failure recovery | Pass for structured-output failover and bounded retries on the same mission. Full failure-mode coverage remains open. |
| Quality evaluation | Pass for the focused Swahili retest and for withholding failed drafts. Broader editorial and audience coverage remains open. |
| Interrupted task resumption | Pass for failure-and-retry continuity. Controlled interruption during an active production task remains open. |
| Duplicate execution prevention | Pass for captured mission replay and same-asset lookup. Commerce and external-action cases remain open. |
| Owner-scoped memory and execution isolation | Pass for authenticated-role database checks. Two independent signed-in browser sessions remain open. |
| Verified production functionality | Pass for the released slices above. Phase 1 as a whole remains open. |

The next implementation milestone is resolve-and-resume for unfinished work plus translation and chapter edits using validated owner-scoped source revisions. Pinterest and Meta external publishing remain paused. Supabase full backup and restore verification remains an external blocker.

### Existing book revisions · 27 November 2026

Phase 1 implementation adds owner-scoped source lookup, translation and chapter-only revision through the existing Studio tool registry and durable plan. The flow checkpoints the exact source version, validates the result, writes a new private immutable version, and verifies the saved content digest. Chapter edits send only the selected source chapter to the provider. Combined requests still require further planning support. This release adds no dependency or database migration.

Local focused regressions: 168 passed. The tests cover translation to Swahili, constrained edit scope, malformed output recovery, ambiguous references, owner isolation, source prompt injection, and idempotent replay after an insert race. Live authenticated translation/edit and original-version reopen are pending at this checkpoint. If an active task is interrupted, verify under the existing owner identity before marking it recovered.

Next action: run Core PR CI and supply-chain checks, verify the Core API and worker deploy the same commit, then translate a private QA book and edit one chapter through the production Hub. Compare database versions and confirm the latest and original versions remain readable. Persistent conversational references, unfinished-work discovery, private promotional Pin and commercial follow-ups, compound execution and mobile UX remain open.

#### Production book revision evidence

Core [#296](https://github.com/hapeenooo555-netizen/aea-core/pull/296) merged as `dc6a253d8c2dc602c178f48c9d8e60c5addf5091`. The Core API (`dep-dc4ibam0tbcc73d507i0`) and worker (`dep-dc4ibau0tbcc73d508ag`) are live at that SHA. Both merged-commit checks passed, [backend tests](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45953268965) and [CycloneDX SBOM and licenses](https://github.com/hapeenooo555-netizen/aea-core/actions/runs/45953268938). The targeted changed-code credential pattern scan found zero matches. Full vulnerability and secret scans are still outstanding because the GitHub connector declined alert API access and these checks are absent from the release workflow.

Authenticated Hub mission `465fe1a3-a9ff-441f-b91e-cbc3a94eab44` edited chapter two of the private Swahili QA book `2a37bc3d-753e-4b32-9e0f-f41ac5f3c929`. Supabase comparison of versions 1 and 2 showed chapters one and three were unchanged and chapter two changed from 644 to 665 characters. Mission `9b322e56-2796-4a23-81f1-afd90b925b74` translated that same asset to English as version 3. Both missions recorded VERIFIED on their first attempt. My Store showed version 3 as draft and owner-only. The AEA Reader reopened its three English chapters with full prose. Versions 1 and 2 remain private and intact in Supabase. Reopening older versions in My Store remains open.

Hub [#197](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/pull/197) adds the task history surface and restores a selected task's original idempotency key. Six targeted Hub tests passed. The next release check is to verify both PR workflows, observe Render, then reopen an interrupted private QA task via the history panel from a second tab.

#### Production task recovery and phone-width checks

Hub [#197](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/pull/197) merged as `482ef839d31afdc8632bbfff97a72e0df5a2978f` and went live on `dep-dc4ie3l9el5s73biaq1g`. Merged-commit [web checks](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/actions/runs/45953920874) and [CycloneDX SBOM and licenses](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/actions/runs/45953920820) passed.

- Authenticated QA mission `46a22516-ff75-45f3-bdc9-4e22d4a17e41` was submitted, observed from a second tab, and reopened from Your tasks after reloading the originating tab before it returned. Both tabs checked the same task. The resulting private version 4 was verified. Supabase recorded one matching mission, one execution, one attempt, and one version 4 row. The backend worker itself was not stopped or restarted; production recovery from an expired worker lease is still open.
- The owner identity saw all three book versions and both original revision missions at the RLS test point. A second authenticated database identity saw zero of either. A second signed-in browser identity remains unverified.
- My Store's existing version history loaded the original Swahili title and all three original chapter bodies into the editor without writing any data. The reader flow for the current English version remains verified.
- Real production Hub layout was measured at CSS viewport widths of 454 and 340 using Chrome zoom. No horizontal document overflow was found. At 340, the submit button was 44 CSS pixels high and the visible task row was 96 pixels high. Reload recovery used one workspace and three clicks (Your tasks, the task, Check task) with no objective re-entry. This is a layout and workflow smoke test. Android device emulation was unavailable under browser policy, and first-time mobile commerce E2E remains open.
- The active row remained In progress when the result above had already verified completion. Hub [#198](https://github.com/hapeenooo555-netizen/aea-affiliate-hub/pull/198) reconciles the row with the returned mission state and adds a regression assertion. Prioritize its deployment and repeat the live cross-tab smoke.

Next engineering slice after #198: bind follow-up instructions to a verified task reference, revalidate owner and exact source version at execution, and expose a coherent Continue action in Hub. Keep AI-assisted selection bounded by the existing tool contracts before adding private Pin and commerce orchestration. Pinterest and Meta publishing remain paused, and full Supabase backup/restore verification remains an external blocker.

#### Context-bound book follow-ups

A selected task can now supply its prior verified book as the context for a short English or Swahili follow-up. Core accepts the reference only from a completed mission for the authenticated owner, checks that the referenced version remains current, and asks for a title if the reference cannot safely be resolved. Explicit titles take precedence. Local selected regressions: 175 passed, including an ambiguous collection, cross-owner context, a stale source, and a partial result. Pending: Hub follow-up action and authenticated production test.
