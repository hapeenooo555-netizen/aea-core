-- Restrict the privileged OAuth completion RPC to trusted server-side callers.
-- The live database currently grants EXECUTE to authenticated, while the
-- application server uses service_role for this atomic callback operation.
-- Keep SECURITY DEFINER because the callback is completed without a browser
-- user JWT; its persisted OAuth state and owner binding are validated inside
-- the function. Never expose this RPC to browser roles.
begin;

revoke all on function public.complete_oauth_callback(
    text, text, text, text, integer, text, text, text[], text
) from public, anon, authenticated;

grant execute on function public.complete_oauth_callback(
    text, text, text, text, integer, text, text, text[], text
) to service_role;

-- These legacy/internal tables have no user-facing RLS policies. They are
-- consumed only by trusted server-side code, so remove direct Data API grants
-- instead of adding broad authenticated policies.
revoke all on table public.ai_memory, public.users, public.worker_registry
    from anon, authenticated;

commit;
