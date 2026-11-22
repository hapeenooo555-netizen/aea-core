-- Make Pinterest publication evidence server-written only.
-- Authenticated users may read their own publication records, but cannot
-- create, alter, or delete evidence that a provider returned a Pin ID.
BEGIN;

DROP POLICY IF EXISTS published_pins_insert_own ON public.published_pins;
DROP POLICY IF EXISTS published_pins_update_own ON public.published_pins;
DROP POLICY IF EXISTS published_pins_delete_own ON public.published_pins;
DROP POLICY IF EXISTS published_pins_select_own ON public.published_pins;

CREATE POLICY published_pins_select_own
    ON public.published_pins
    FOR SELECT
    TO authenticated
    USING (owner_id = (SELECT auth.uid()));

REVOKE ALL ON TABLE public.published_pins FROM anon;
REVOKE ALL ON TABLE public.published_pins FROM authenticated;
REVOKE ALL ON TABLE public.published_pins FROM PUBLIC;

GRANT SELECT ON TABLE public.published_pins TO authenticated;
GRANT ALL ON TABLE public.published_pins TO service_role;

COMMIT;
