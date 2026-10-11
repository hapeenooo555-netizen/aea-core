# Durable Claim Staging and Rollback Runbook

Status: **staging procedure documented; live staging NOT VERIFIED**.
This runbook does not authorize a production migration or deployment.

## Scope

Migration `supabase/migrations/000000000017_atomic_mission_step_claim.sql`
replaces the existing `claim_mission_step` function with a transaction-scoped
advisory lock keyed by `execution_id + operation_key`. This closes the
first-claim race that row-level `FOR UPDATE` cannot protect when no row exists.
The migration is additive and does not drop tables or delete data. The matching
mirror lives at `database/migrations/sprint7_2l_atomic_mission_step_claim.sql`.

The application now fails closed in `durable_required=True` mode when the
claim RPC raises or returns no claim. It does not attempt a non-atomic
PostgREST read-then-insert fallback in that mode. The legacy/non-durable mode
retains its fallback for compatibility and must not be used for production
side effects.

## Preconditions

- Use a dedicated staging Supabase project/database and a separate staging
  application service. Never point staging at production.
- Use test-only accounts and a disposable mission/execution owned by the
  authenticated test user.
- Put credentials in the staging runner's secret environment, never in chat,
  repository files, command output, or logs.
- Confirm the target project reference, current migration version, current
  function signature, RLS policies, and a restorable database backup/snapshot.
- Verify that migration 014 is already applied and the seven-argument
  `claim_mission_step(UUID, UUID, INTEGER, TEXT, TEXT, TEXT, INTEGER)`
  function exists before applying migration 017.
- Do not run migration 017 against production as part of this procedure.

## Read-only preflight

Run against the staging database before applying migration 017:

```sql
select current_database(), current_user, current_timestamp;

select version
from supabase_migrations.schema_migrations
order by version desc
limit 10;

select
  n.nspname as schema_name,
  p.proname as function_name,
  pg_get_function_identity_arguments(p.oid) as arguments,
  p.prosecdef as security_definer
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('claim_mission_execution', 'claim_mission_step')
order by p.proname, arguments;

select indexname, indexdef
from pg_indexes
where schemaname = 'public'
  and tablename in ('mission_executions', 'mission_steps')
order by tablename, indexname;
```

If the migration history table differs in the target project, use that project's
documented migration metadata source. Do not infer an applied state from the
presence of a local SQL file. Stop if schema, privileges, or migration state is
ambiguous.

## Apply in staging

1. Capture the preflight output and a database snapshot/backup reference.
2. Apply only migration 017 using the repository's reviewed migration workflow.
3. Re-query `pg_get_functiondef()`, function signature, `prosecdef`, and
   grants. Confirm the function remains SECURITY INVOKER and the established
   least-privilege grants are unchanged.
4. Deploy the application commit containing the durable fail-closed change to
   the staging service only.
5. Run the focused tests and then the complete test suite on that exact commit.

## Two-worker concurrency test

Use two independent authenticated Supabase clients in separate processes (or
separate staging worker containers). Do not use an in-memory fake or a
process-local lock as evidence for this test.

1. Create one disposable mission and one active execution as the same test owner.
2. Generate one shared `operation_key`, two different `step_id` values, two
   different `idempotency_key` values, and two different claim tokens.
3. Release both clients at the same time and call
   `claim_mission_step` with the same execution and operation key, with a
   lease of 60 seconds.
4. Assert exactly one response has `claimed = true`. Both responses must
   identify the same claimed step; the losing caller must not insert a second
   active step.
5. Query `mission_steps` as the test owner. Assert exactly one row for that
   operation key is `in_progress`, and that it has one claim token and one
   future lease.
6. Repeat at least 100 times with fresh disposable execution/operation keys.
   Any duplicate active claim is a hard failure.

### Stale-lease recovery

1. Claim a test operation with a one-second lease.
2. Wait for expiry, then claim the same operation with a new idempotency key
   and token.
3. Assert the old row is marked failed and its claim token is null; the new row
   is the sole active claim.
4. Attempt to update the old row using its old claim token. The update must
   affect zero rows / return failure.
5. Confirm the new claim can complete and that completed operations cannot be
   claimed again.

### Failure-path tests

- Make the claim RPC raise an error and separately return no data. In durable
  mode, both must fail closed and must not issue a table SELECT or INSERT as a
  fallback.
- Confirm an owner mismatch cannot claim another owner's execution.
- Confirm approval-gated side effects remain blocked without approval.
- Confirm retries use the same logical operation key and provider idempotency
  support where available. The database claim alone cannot guarantee exactly
  once delivery to an external provider if a process dies after the provider
  accepts a request but before the result is persisted.

## Rollback / incident procedure

### Application rollback

1. Disable or pause all workers that can trigger external side effects.
2. Preserve execution IDs, step IDs, operation keys, timestamps, and sanitized
   error details. Never log tokens or credentials.
3. Roll the staging application back to the last known-good image/commit.
4. Keep publishing and onboarding disabled until the database/application
   compatibility check passes.
5. Reconcile provider state before retrying any publish, message, or onboarding
   request; do not blindly replay requests that may already have succeeded.

### Database rollback

Migration 017 is a function replacement, so the safest default is to leave it
in place while rolling back application code. It is additive and does not
delete data. Do **not** automatically restore the older function: doing so
would remove cross-worker first-claim serialization.

If restoring the old function is unavoidable, pause all workers first, verify
the target and backup, and have a reviewer restore the exact prior function
definition from migration 014 in a separate reviewed migration. Re-enable
workers only after a new concurrency test passes. Never drop execution/step
rows or tables as part of rollback.

## Go/no-go gates

**GO for staging only** when preflight is unambiguous, migration 017 applies,
the RPC remains SECURITY INVOKER with correct grants, the independent-worker
race passes 100/100 runs, stale-lease recovery passes, the full test suite
passes on the same commit, and rollback has been rehearsed in staging.

**NO-GO for production** until the live staging evidence is recorded, Meta OAuth
and token lifecycle limitations remain gated, and a separate production change
is explicitly reviewed and approved. CI passing is not live database proof.

## Evidence record

Record the commit SHA, staging project reference (not credentials), migration
version, workflow run URL, test counts, race-test iterations/results, rollback
rehearsal result, and reviewer. Mark live checks **NOT VERIFIED** until they
have actually been executed against the isolated staging environment.
