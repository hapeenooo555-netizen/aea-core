-- P1-8: Harden function-level privileges for durable execution RPCs.
--
-- AUDIT FINDING: The five P1-8 durable-execution RPC functions are
-- SECURITY INVOKER functions whose bodies rely on auth.uid() and RLS
-- policies to enforce per-owner ownership checks.  However, in
-- migrations 010/011/012 the functions were never revoked from the
-- PostgreSQL PUBLIC role.  Every PostgreSQL role — including the anon
-- role — inherits privileges granted to PUBLIC.  As a result, any
-- anon-scoped Supabase client can EXECUTE all five functions even
-- though migrations 011/012 attempted "REVOKE ALL ... FROM anon".
--
-- Those per-role REVOKEs from anon (migrations 011, 012) are
-- ineffective at closing the PUBLIC inheritance gap, because anon
-- receives EXECUTE through PUBLIC membership, not through a direct
-- grant.  The fix is to REVOKE ALL from PUBLIC itself.
--
-- Privilege model after this migration:
--   PUBLIC          -> NO EXECUTE
--   anon            -> NO EXECUTE
--   authenticated   -> EXECUTE
--   service_role    -> NOT granted (not used by the application;
--                    follows least-privilege convention established
--                    in migration 008_claim_hardening.sql)

BEGIN;

-- ============================================================================
-- Helper: list of the five P1-8 durable-execution functions and their exact
-- signatures.  Each REVOKE/GRANT below mirrors the signature declared in
-- the original CREATE OR REPLACE FUNCTION statement in migrations 010,
-- 011, and 012.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- public.claim_mission_for_orchestration(UUID, TEXT, INTEGER)
--   Source: migration 012_p1_8_orchestration.sql
-- ---------------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.claim_mission_for_orchestration(UUID, TEXT, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.claim_mission_for_orchestration(UUID, TEXT, INTEGER) FROM anon;
REVOKE ALL ON FUNCTION public.claim_mission_for_orchestration(UUID, TEXT, INTEGER) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.claim_mission_for_orchestration(UUID, TEXT, INTEGER) TO authenticated;

-- ---------------------------------------------------------------------------
-- public.claim_mission_execution(UUID, UUID, TEXT, TEXT)
--   Source: migration 010_p1_6_durable_execution.sql
-- ---------------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.claim_mission_execution(UUID, UUID, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.claim_mission_execution(UUID, UUID, TEXT, TEXT) FROM anon;
REVOKE ALL ON FUNCTION public.claim_mission_execution(UUID, UUID, TEXT, TEXT) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.claim_mission_execution(UUID, UUID, TEXT, TEXT) TO authenticated;

-- ---------------------------------------------------------------------------
-- public.claim_mission_step(UUID, UUID, INTEGER, TEXT, TEXT, TEXT, INTEGER)
--   Source: migration 011_p1_7d_execution_safety.sql
--   (overrides the 4-arg version from migration 010)
-- ---------------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.claim_mission_step(UUID, UUID, INTEGER, TEXT, TEXT, TEXT, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.claim_mission_step(UUID, UUID, INTEGER, TEXT, TEXT, TEXT, INTEGER) FROM anon;
REVOKE ALL ON FUNCTION public.claim_mission_step(UUID, UUID, INTEGER, TEXT, TEXT, TEXT, INTEGER) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.claim_mission_step(UUID, UUID, INTEGER, TEXT, TEXT, TEXT, INTEGER) TO authenticated;

-- ---------------------------------------------------------------------------
-- public.update_execution_state(UUID, TEXT, INTEGER, INTEGER, JSONB, TIMESTAMPTZ)
--   Source: migration 010_p1_6_durable_execution.sql
-- ---------------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.update_execution_state(UUID, TEXT, INTEGER, INTEGER, JSONB, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.update_execution_state(UUID, TEXT, INTEGER, INTEGER, JSONB, TIMESTAMPTZ) FROM anon;
REVOKE ALL ON FUNCTION public.update_execution_state(UUID, TEXT, INTEGER, INTEGER, JSONB, TIMESTAMPTZ) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.update_execution_state(UUID, TEXT, INTEGER, INTEGER, JSONB, TIMESTAMPTZ) TO authenticated;

-- ---------------------------------------------------------------------------
-- public.complete_execution(UUID, TEXT, JSONB)
--   Source: migration 010_p1_6_durable_execution.sql
-- ---------------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.complete_execution(UUID, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete_execution(UUID, TEXT, JSONB) FROM anon;
REVOKE ALL ON FUNCTION public.complete_execution(UUID, TEXT, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.complete_execution(UUID, TEXT, JSONB) TO authenticated;

COMMIT;
