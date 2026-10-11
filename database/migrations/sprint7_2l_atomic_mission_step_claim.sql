-- Sprint 7.2-L: Atomic distributed mission-step claim.
-- Mirror of supabase/migrations/000000000017_atomic_mission_step_claim.sql.
-- Serializes claims by execution_id + operation_key, including first creation.
-- Additive and non-destructive; existing grants are preserved.
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
    IF p_execution_id IS NULL OR p_step_id IS NULL
       OR p_idempotency_key IS NULL OR v_operation_key IS NULL
       OR p_claim_token IS NULL THEN
        RAISE EXCEPTION 'execution, step, idempotency, operation and claim token are required'
            USING ERRCODE = '22023';
    END IF;

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
