from __future__ import annotations

from app import database


def test_service_client_does_not_fall_back_to_anon_key(monkeypatch) -> None:
    monkeypatch.setattr(database, "supabase_service_client", None)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    assert database.get_supabase_service_client() is None


def test_service_client_uses_only_explicit_service_role_key(monkeypatch) -> None:
    monkeypatch.setattr(database, "supabase_service_client", None)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "server-only-secret")

    captured = {}

    class FakeSupabase:
        pass

    def fake_create_client(url, key):
        captured["url"] = url
        captured["key"] = key
        return FakeSupabase()

    monkeypatch.setitem(__import__("sys").modules, "supabase", type("FakeModule", (), {"create_client": staticmethod(fake_create_client)})())
    client = database.get_supabase_service_client()

    assert isinstance(client, FakeSupabase)
    assert captured == {
        "url": "https://example.supabase.co",
        "key": "server-only-secret",
    }
