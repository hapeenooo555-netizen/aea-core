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
AS $$
DECLARE
    v_checkpoint public.human_intervention_checkpoints%ROWTYPE;
    v_workflow public.onboarding_workflows%ROWTYPE;
    v_mission public.missions%ROWTYPE;
    v_inner_metadata JSONB;
    v_oauth_state TEXT;
    v_oauth_state_owner TEXT;
    v_oauth_state_workflow TEXT;
    v_workflow_id TEXT;
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

    -- Verify the checkpoint is in processing state.
    -- If already completed, return idempotent result.
    IF v_checkpoint.status = 'completed' THEN
        v_workflow_id := v_checkpoint.metadata->>'workflow_id';
        IF v_workflow_id IS NULL AND v_checkpoint.metadata ? 'metadata' THEN
            v_workflow_id := v_checkpoint.metadata->'metadata'->>'workflow_id';
        END IF;
        RETURN QUERY SELECT true, 'completed', v_workflow_id, 'complete', 'OAuth callback already completed';
        RETURN;
    END IF;

    -- Only processing checkpoints can be completed.
    IF v_checkpoint.status != 'processing' THEN
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
    v_oauth_state_workflow := v_inner_metadata->>'oauth_state_workflow';

    -- Validate the persisted OAuth state as the trust boundary.
    -- Do NOT trust owner/workflow/mission IDs supplied independently.
    IF v_oauth_state IS NULL OR v_oauth_state <> p_oauth_state THEN
        RETURN QUERY SELECT false, 'state_mismatch', NULL::TEXT, NULL::TEXT, 'OAuth state mismatch';
        RETURN;
    END IF;

    IF v_oauth_state_owner IS NOT NULL AND v_oauth_state_owner <> p_owner_id THEN
        RETURN QUERY SELECT false, 'owner_mismatch', NULL::TEXT, NULL::TEXT, 'Owner mismatch';
        RETURN;
    END IF;

    -- Verify workflow_id binding.
    v_workflow_id := v_checkpoint.metadata->>'workflow_id';
    IF v_workflow_id IS NULL AND v_checkpoint.metadata ? 'metadata' THEN
        v_workflow_id := v_checkpoint.metadata->'metadata'->>'workflow_id';
    END IF;

    IF v_workflow_id IS NOT NULL AND p_workflow_id IS NOT NULL AND v_workflow_id <> p_workflow_id THEN
        RETURN QUERY SELECT false, 'workflow_mismatch', NULL::TEXT, NULL::TEXT, 'Workflow ID mismatch';
        RETURN;
    END IF;

    IF v_workflow_id IS NULL THEN
        v_workflow_id := p_workflow_id;
    END IF;

    -- Mark checkpoint as completed and consume the OAuth state.
    UPDATE public.human_intervention_checkpoints
        SET status = 'completed',
            completed_at = v_now,
            metadata = v_checkpoint.metadata || jsonb_build_object(
                'oauth_state_consumed', true,
                'oauth_state_consumed_at', v_now
            )
        WHERE id = v_checkpoint.id;

    -- Look up the associated onboarding workflow.
    IF v_workflow_id IS NOT NULL THEN
        SELECT * INTO v_workflow
            FROM public.onboarding_workflows
            WHERE id = v_workflow_id::UUID
        LIMIT 1;

        IF FOUND THEN
            -- Advance workflow to completed.
            UPDATE public.onboarding_workflows
                SET status = 'completed',
                    updated_at = v_now
                WHERE id = v_workflow.id;
        END IF;
    END IF;

    -- Create or upsert the platform connection (idempotent).
    -- The unique constraint on (owner_id, platform) ensures
    -- no duplicate connections.
    -- Use the secure token_reference, NOT the raw access token.
    INSERT INTO public.platform_connections (
        id, owner_id, platform, status,
        external_account_id, display_name, scopes,
        token_reference, expires_at, created_at, updated_at
    ) VALUES (
        gen_random_uuid(), p_owner_id, 'pinterest', 'connected',
        p_external_account_id, p_display_name, p_scopes,
        p_token_reference,
        v_now + make_interval(seconds := p_expires_in),
        v_now, v_now
    )
    ON CONFLICT (owner_id, platform) DO UPDATE SET
        status = EXCLUDED.status,
        external_account_id = EXCLUDED.external_account_id,
        display_name = EXCLUDED.display_name,
        scopes = EXCLUDED.scopes,
        token_reference = EXCLUDED.token_reference,
        expires_at = EXCLUDED.expires_at,
        updated_at = EXCLUDED.updated_at;

    -- Transition mission waiting_human -> pending.
    -- Find the mission associated with the workflow.
    IF v_workflow_id IS NOT NULL THEN
        SELECT m.* INTO v_mission
            FROM public.missions m
            JOIN public.onboarding_workflows ow ON ow.mission_id = m.id
            WHERE ow.id = v_workflow_id::UUID
        LIMIT 1;

        IF FOUND AND v_mission.status = 'waiting_human' THEN
            UPDATE public.missions
                SET status = 'pending',
                    updated_at = v_now
                WHERE id = v_mission.id
                  AND status = 'waiting_human';
        END IF;
    END IF;

    -- Return success.
    RETURN QUERY SELECT true, 'connected', v_workflow_id, 'complete', 'Pinterest connection established successfully';

END;
$$;

GRANT EXECUTE ON FUNCTION public.complete_oauth_callback(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT[], TEXT
) TO anon, authenticated, service_role;

COMMIT;
