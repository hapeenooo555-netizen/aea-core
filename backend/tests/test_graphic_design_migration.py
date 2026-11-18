from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20261118130000_graphic_design_assets.sql"
)


def test_graphic_design_assets_use_private_bucket_and_supported_image_types():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "'aea-design-assets', 'aea-design-assets', false" in sql
    assert "array['image/png','image/jpeg','image/webp']" in sql
    assert "file_size_limit" in sql


def test_graphic_design_jobs_are_owner_scoped_and_rls_enabled():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "alter table public.graphic_design_jobs enable row level security" in sql
    assert "(select auth.uid()) = owner_id" in sql
    assert "owners read own graphic design jobs" in sql


def test_browser_cannot_mark_graphic_design_job_completed_or_write_asset_path():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "owners create own pending graphic design jobs" in sql
    assert "status = 'pending'" in sql
    assert "asset_path is null" in sql
    assert "grant select, insert on public.graphic_design_jobs to authenticated" in sql
    assert "grant all on public.graphic_design_jobs to service_role" in sql


def test_storage_policies_scope_objects_to_authenticated_owner_folder():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "owners read own AEA design assets" in sql
    assert "owners upload own AEA design assets" in sql
    assert "owners update own AEA design assets" in sql
    assert "owners delete own AEA design assets" in sql
    assert "(storage.foldername(name))[1] = (select auth.uid())::text" in sql
