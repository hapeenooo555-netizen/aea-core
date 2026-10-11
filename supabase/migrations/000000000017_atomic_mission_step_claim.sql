-- P1-7D hardening: serialize concurrent claims for the same logical side effect.
--
-- SELECT ... FOR UPDATE cannot lock a row that does not exist. Two independent
-- workers could therefore both observe no prior operation_key row and INSERT
-- different attempts concurrently. A transaction-scoped advisory lock on
-- (execution_id, operation_key) serializes both first-claim and stale-recovery
-- paths across app processes/servers. Existing row locks and unique idempotency
-- constraints remain additional protection.
--
-- Non-destructive: replaces only the function body. No rows or policies are
-- deleted; existing function grants are preserved by CREATE OR REPLACE.
BEGIN;

ALTER TABLE public.mission_steps
    ADD COLUMN IF NOT EXISTS operation_key TEXT,
    ADD COLUMN IF NOT EXISTS claim_token TEXT,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_mission_steps_operation_key
    ON public.mission_steps (execution_id, operation_key);
CREATE INDEX IF NOT EXISTS idx_mission_steps_lease
    ON public.mission_steps (lease_expires_at)
    WHERE status = 'in_progress';

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
    IF p_execution_id IS NULL OR p_step_id IS NULL
       OR p_idempotency_key IS NULL OR v_operation_key IS NULL
       OR p_claim_token IS NULL THEN
        RAISE EXCEPTION 'execution, step, idempotency, operation and claim token are required'
            USING ERRCODE = '22023';
    END IF;

    -- Unlike FOR UPDATE on a missing row, this lock also protects first creation.
    -- Transaction scope releases it automatically on success or error.
    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_execution_id::text || ':' || v_operation_key, 0)
    );

    SELECT mission_id INTO v_mission_id
    FROM public.mission_executions
    WHERE execution_id = p_execution_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::jsonb, false;
        RETURN;
    END IF;

    SELECT * INTO v_existing
    FROM public.mission_steps
    WHERE execution_id = p_execution_id
      AND idempotency_key = p_idempotency_key
    LIMIT 1;
    IF FOUND THEN
        -- A repeated idempotency key is a replay, never a fresh claim.
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
            -- Clear the old fencing token before permitting a replacement claim.
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
        id, mission_id, execution_id, step_name, worker_role, status,
        attempt_index, idempotency_key, operation_key, claim_token,
        lease_expires_at, started_at, completed_at, result, retry_category, created_at
    ) VALUES (
        p_step_id, v_mission_id, p_execution_id, 'claimed_step', 'employee', 'in_progress',
        p_attempt_index, p_idempotency_key, v_operation_key, p_claim_token,
        now() + make_interval(secs => GREATEST(COALESCE(p_lease_seconds, 60), 1)),
        now(), NULL, '{}'::jsonb, NULL, now()
    )
    RETURNING * INTO v_inserted;

    RETURN QUERY SELECT to_jsonb(v_inserted), true;
EXCEPTION
    WHEN unique_violation THEN
        -- Idempotency-key uniqueness is a final race-safety backstop.
        SELECT * INTO v_existing
        FROM public.mission_steps
        WHERE execution_id = p_execution_id
          AND idempotency_key = p_idempotency_key
        LIMIT 1;
        IF FOUND THEN
            RETURN QUERY SELECT to_jsonb(v_existing), false;
            RETURN;
        END IF;
        RAISE;
END;
$$;

COMMIT;
