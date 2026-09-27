-- Sprint 7.2-F: Atomic complete_oauth_callback RPC.
--
-- This function handles the complete OAuth callback flow on the
-- database side. It is called from the production callback after
-- successful Pinterest token exchange.
--
-- The function performs ALL durable operations in ONE PostgreSQL
-- transaction:
--   1. Verify the checkpoint is in 'processing' state
--   2. Validate the persisted OAuth state as the trust boundary
--   3. Create/update platform_connections (idempotent)
--   4. Advance onboarding workflow exactly once
--   5. Transition mission waiting_human -> pending
--   6. Transition checkpoint processing -> completed
--   7. Set oauth_state_consumed = true
--
-- If retried after successful completion, returns the existing
-- completed result without duplicating any operations.
--
-- Schema-qualified names are used throughout to avoid search_path
-- surprises.
BEGIN;

-- Self-contained dependency setup for sprint7_2f.
-- Ensures platform_connections.owner_id_uuid exists and the
-- unique constraint on (owner_id, platform) is present before
-- the RPC is created. These are idempotent and follow the
-- same patterns as sprint7_2g and sprint7_2k.

-- Add owner_id_uuid column if missing (compatible with sprint7_2g).
ALTER TABLE public.platform_connections
    ADD COLUMN IF NOT EXISTS owner_id_uuid UUID;

-- Ensure unique constraint on (owner_id, platform) for idempotent upsert.
-- Duplicate detection follows sprint7_2k pattern: fail safely if duplicates exist.
DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT owner_id, platform
        FROM public.platform_connections
        GROUP BY owner_id, platform
        HAVING COUNT(*) > 1
    ) dup;

    IF dup_count > 0 THEN
        RAISE EXCEPTION 'Duplicate (owner_id, platform) rows exist in platform_connections. Resolve duplicates before applying sprint7_2f.';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ux_platform_connections_owner_platform'
          AND conrelid = 'public.platform_connections'::regclass
    ) THEN
        ALTER TABLE public.platform_connections
            ADD CONSTRAINT ux_platform_connections_owner_platform
            UNIQUE (owner_id, platform);
    END IF;
END $$;

COMMENT ON CONSTRAINT ux_platform_connections_owner_platform ON public.platform_connections IS
    'Ensures at most one platform connection per owner per platform. Repeated connections use upsert semantics.';

CREATE OR REPLACE FUNCTION public.complete_oauth_callback(
    p_oauth_state TEXT,
    p_owner_id TEXT,
    p_workflow_id TEXT,
    p_token_type TEXT,
    p_expires_in INTEGER,
    p_external_account_id TEXT,
    p_display_name TEXT,
    p_scopes TEXT[],
    p_token_reference TEXT
) RETURNS TABLE (success BOOLEAN, status TEXT, workflow_id TEXT, next_step TEXT, message TEXT)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
    v_checkpoint public.human_intervention_checkpoints%ROWTYPE;
    v_workflow public.onboarding_workflows%ROWTYPE;
    v_mission public.missions%ROWTYPE;
    v_inner_metadata JSONB;
    v_oauth_state TEXT;
    v_oauth_state_owner TEXT;
    v_workflow_id TEXT;
    v_owner_id UUID;
    v_now TIMESTAMPTZ := now();
BEGIN
    -- Find the checkpoint by OAuth state.
    -- Support both flat metadata.oauth_state and legacy
    -- nested metadata.metadata.oauth_state.
    SELECT * INTO v_checkpoint
        FROM public.human_intervention_checkpoints
        WHERE metadata->>'oauth_state' = p_oauth_state
           OR metadata->'metadata'->>'oauth_state' = p_oauth_state
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'checkpoint_not_found', NULL::TEXT, NULL::TEXT, 'No checkpoint found for this OAuth state';
        RETURN;
    END IF;

    -- Only processing checkpoints can be completed, while completed
    -- checkpoints may return the idempotent result after validation.
    IF v_checkpoint.status NOT IN ('processing', 'completed') THEN
        RETURN QUERY SELECT false, 'invalid_state', NULL::TEXT, NULL::TEXT,
            'Checkpoint is in ' || COALESCE(v_checkpoint.status, 'null') || ' state; expected processing';
        RETURN;
    END IF;

    -- Extract and validate state metadata from the checkpoint's metadata JSONB.
    v_inner_metadata := v_checkpoint.metadata;
    -- If metadata is nested (metadata.metadata), unwrap it.
    IF v_inner_metadata ? 'metadata' THEN
        v_inner_metadata := v_inner_metadata->'metadata';
    END IF;

    v_oauth_state := v_inner_metadata->>'oauth_state';
    v_oauth_state_owner := v_inner_metadata->>'oauth_state_owner';

    -- Validate the persisted OAuth state as the trust boundary.
    -- Do NOT trust owner/workflow/mission IDs supplied independently.
    IF v_oauth_state IS NULL OR v_oauth_state <> p_oauth_state THEN
        RETURN QUERY SELECT false, 'state_mismatch', NULL::TEXT, NULL::TEXT, 'OAuth state mismatch';
        RETURN;
    END IF;

    IF v_oauth_state_owner IS NULL THEN
        RETURN QUERY SELECT false, 'owner_binding_missing', NULL::TEXT, NULL::TEXT, 'Persisted OAuth state owner is missing';
        RETURN;
    END IF;

    IF v_oauth_state_owner <> p_owner_id THEN
        RETURN QUERY SELECT false, 'owner_mismatch', NULL::TEXT, NULL::TEXT, 'Owner mismatch';
        RETURN;
    END IF;

    IF p_owner_id IS NULL
       OR p_owner_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN QUERY SELECT false, 'invalid_owner_id', NULL::TEXT, NULL::TEXT, 'Owner ID is not a valid UUID';
        RETURN;
    END IF;

    v_owner_id := p_owner_id::UUID;

    IF v_owner_id IS DISTINCT FROM auth.uid() THEN
        RETURN QUERY SELECT false, 'owner_mismatch', NULL::TEXT, NULL::TEXT, 'Owner does not match the authenticated user';
        RETURN;
    END IF;

    -- Verify workflow_id binding.
    v_workflow_id := v_inner_metadata->>'workflow_id';

    IF v_workflow_id IS NULL THEN
        RETURN QUERY SELECT false, 'workflow_binding_missing', NULL::TEXT, NULL::TEXT, 'Persisted workflow ID is missing';
        RETURN;
    END IF;

    IF v_workflow_id IS DISTINCT FROM p_workflow_id THEN
        RETURN QUERY SELECT false, 'workflow_mismatch', NULL::TEXT, NULL::TEXT, 'Workflow ID mismatch';
        RETURN;
    END IF;

    -- Validate workflow_id format before casting (consistent with mission_id validation).
    IF v_workflow_id IS NULL
       OR v_workflow_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN QUERY SELECT false, 'invalid_workflow_id', NULL::TEXT, NULL::TEXT, 'Persisted workflow ID is not a valid UUID';
        RETURN;
    END IF;

    -- If already completed, return idempotent result after validating ownership.
    IF v_checkpoint.status = 'completed' THEN
        RETURN QUERY SELECT true, 'completed', v_workflow_id, 'complete', 'OAuth callback already completed';
        RETURN;
    END IF;

    -- Resolve the persisted workflow and its mission before any durable write.
    SELECT * INTO v_workflow
        FROM public.onboarding_workflows
        WHERE id = v_workflow_id::UUID
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'workflow_not_found', NULL::TEXT, NULL::TEXT, 'Persisted onboarding workflow not found';
        RETURN;
    END IF;

    IF v_workflow.mission_id IS NULL
       OR v_workflow.mission_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN QUERY SELECT false, 'invalid_mission_id', NULL::TEXT, NULL::TEXT, 'Persisted mission ID is not a valid UUID';
        RETURN;
    END IF;

    SELECT m.* INTO v_mission
        FROM public.missions m
        WHERE m.id = v_workflow.mission_id::UUID
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'mission_not_found', NULL::TEXT, NULL::TEXT, 'Mission for persisted onboarding workflow not found';
        RETURN;
    END IF;

    -- Advance the persisted onboarding workflow.
    UPDATE public.onboarding_workflows
        SET status = 'completed',
            updated_at = v_now
        WHERE id = v_workflow.id;

    -- Create or upsert the platform connection (idempotent).
    -- The unique constraint on (owner_id, platform) ensures
    -- no duplicate connections.
    -- Use the secure token_reference, NOT the raw access token.
    INSERT INTO public.platform_connections (
        id, owner_id, owner_id_uuid, platform, status,
        external_account_id, display_name, scopes,
        token_reference, expires_at, created_at, updated_at
    ) VALUES (
        gen_random_uuid(), p_owner_id, v_owner_id, 'pinterest', 'connected',
        p_external_account_id, p_display_name, p_scopes,
        p_token_reference,
        v_now + make_interval(seconds := p_expires_in),
        v_now, v_now
    )
    ON CONFLICT (owner_id, platform) DO UPDATE SET
        owner_id = EXCLUDED.owner_id,
        owner_id_uuid = EXCLUDED.owner_id_uuid,
        status = EXCLUDED.status,
        external_account_id = EXCLUDED.external_account_id,
        display_name = EXCLUDED.display_name,
        scopes = EXCLUDED.scopes,
        token_reference = EXCLUDED.token_reference,
        expires_at = EXCLUDED.expires_at,
        updated_at = EXCLUDED.updated_at;

    -- Transition the mission resolved from the persisted workflow.
    IF v_mission.status = 'waiting_human' THEN
        UPDATE public.missions
            SET status = 'pending',
                updated_at = v_now
            WHERE id = v_mission.id
              AND status = 'waiting_human';
    END IF;

    -- Mark checkpoint completion and consume OAuth state only after all
    -- other durable completion operations have succeeded.
    UPDATE public.human_intervention_checkpoints
        SET status = 'completed',
            completed_at = v_now,
            metadata = v_checkpoint.metadata || jsonb_build_object(
                'oauth_state_consumed', true,
                'oauth_state_consumed_at', v_now
            )
        WHERE id = v_checkpoint.id
          AND status = 'processing';

    -- Return success.
    RETURN QUERY SELECT true, 'connected', v_workflow_id, 'complete', 'Pinterest connection established successfully';

END;
$$;

REVOKE ALL ON FUNCTION public.complete_oauth_callback(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT[], TEXT
) FROM PUBLIC, anon, service_role;

GRANT EXECUTE ON FUNCTION public.complete_oauth_callback(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT[], TEXT
) TO authenticated;

COMMIT;
