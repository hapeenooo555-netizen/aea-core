-- P1-9-FIX: Correct the execution lookup column in claim_mission_step.
--
-- BUG: The deployed claim_mission_step function (migration 011) queries
--   SELECT mission_id INTO v_mission_id
--   FROM public.mission_executions
--   WHERE id = p_execution_id;
--
-- But the application (MissionExecutionService.claim_step) passes
-- execution_id (the caller-supplied UUID stored in the execution_id column)
-- as p_execution_id, NOT the auto-generated id column (gen_random_uuid()).
-- These are different UUIDs, so the lookup always returns NOT FOUND
-- and the function returns (NULL, false), causing every step claim to fail.
--
-- FIX: Change the lookup to use execution_id instead of id.  All other
-- queries in the function body already correctly use execution_id.
--
-- This migration is non-destructive:
--   - CREATE OR REPLACE FUNCTION preserves existing privileges (grants
--     are NOT touched; migration 013 already established the correct
--     privilege model: PUBLIC = no EXECUTE, anon = no EXECUTE,
--     authenticated = EXECUTE).
--   - SECURITY INVOKER is preserved (no SECURITY DEFINER).
--   - RLS and table schemas are untouched.
--   - No schema changes.

BEGIN;

CREATE OR REPLACE FUNCTION public.claim_mission_step(
    p_execution_id UUID,
    p_step_id UUID,
    p_attempt_index INTEGER,
    p_idempotency_key TEXT,
    p_operation_key TEXT,
    p_claim_token TEXT,
    p_lease_seconds INTEGER DEFAULT 60
) RETURNS TABLE (step JSONB, claimed BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
    v_existing public.mission_steps%ROWTYPE;
    v_latest public.mission_steps%ROWTYPE;
    v_inserted public.mission_steps%ROWTYPE;
    v_mission_id UUID;
    v_operation_key TEXT := COALESCE(p_operation_key, p_idempotency_key);
BEGIN
    -- FIX: look up by the execution_id column (caller-supplied UUID),
    -- not the auto-generated id column.  The application passes
    -- execution["execution_id"] (the execution_id column value) as
    -- p_execution_id.
    SELECT mission_id INTO v_mission_id
    FROM public.mission_executions
    WHERE execution_id = p_execution_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::jsonb, false;
        RETURN;
    END IF;

    SELECT * INTO v_existing
    FROM public.mission_steps
    WHERE execution_id = p_execution_id AND idempotency_key = p_idempotency_key
    LIMIT 1;
    IF FOUND THEN
        RETURN QUERY SELECT to_jsonb(v_existing), false;
        RETURN;
    END IF;

    SELECT * INTO v_latest
    FROM public.mission_steps
    WHERE execution_id = p_execution_id
      AND operation_key = v_operation_key
    ORDER BY attempt_index DESC, created_at DESC
    LIMIT 1
    FOR UPDATE;
    IF FOUND THEN
        IF v_latest.status = 'completed' THEN
            RETURN QUERY SELECT to_jsonb(v_latest), false;
            RETURN;
        END IF;
        IF v_latest.status = 'in_progress'
           AND v_latest.lease_expires_at IS NOT NULL
           AND v_latest.lease_expires_at > now() THEN
            RETURN QUERY SELECT to_jsonb(v_latest), false;
            RETURN;
        END IF;
        IF v_latest.status = 'in_progress' THEN
            UPDATE public.mission_steps
            SET status = 'failed',
                retry_category = 'EXECUTION',
                claim_token = NULL,
                result = jsonb_build_object('recovered', 'stale_claim'),
                completed_at = now()
            WHERE id = v_latest.id;
        END IF;
    END IF;

    INSERT INTO public.mission_steps (
        mission_id, execution_id, step_name, worker_role, status,
        attempt_index, idempotency_key, operation_key, claim_token,
        lease_expires_at, started_at, completed_at, result, retry_category, created_at
    ) VALUES (
        v_mission_id, p_execution_id, 'claimed_step', 'employee', 'in_progress',
        p_attempt_index, p_idempotency_key, v_operation_key, p_claim_token,
        now() + make_interval(secs => GREATEST(p_lease_seconds, 1)),
        now(), NULL, '{}'::jsonb, NULL, now()
    )
    RETURNING * INTO v_inserted;

    RETURN QUERY SELECT to_jsonb(v_inserted), true;
EXCEPTION
    WHEN unique_violation THEN
        SELECT * INTO v_existing
        FROM public.mission_steps
        WHERE execution_id = p_execution_id AND idempotency_key = p_idempotency_key
        LIMIT 1;
        IF FOUND THEN
            RETURN QUERY SELECT to_jsonb(v_existing), false;
            RETURN;
        END IF;
        RAISE;
END;
$$;

COMMIT;
