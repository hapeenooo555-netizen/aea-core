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
