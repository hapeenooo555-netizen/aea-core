from types import SimpleNamespace

from starlette.requests import Request

from app import database
from app.dependencies import get_user_scoped_client


class FakePostgrest:
    def __init__(self):
        self.token = None

    def auth(self, token):
        self.token = token


class FakeClient:
    def __init__(self):
        self.postgrest = FakePostgrest()
        self.auth = SimpleNamespace(set_session=self._unexpected_session)

    @staticmethod
    def _unexpected_session(**kwargs):
        raise AssertionError("User-scoped API requests must not restore an Auth session")


def test_user_scoped_client_attaches_verified_jwt_to_postgrest(monkeypatch):
    import sys
    from types import ModuleType

    fake_client = FakeClient()
    supabase_module = ModuleType("supabase")
    supabase_module.create_client = lambda url, key: fake_client
    monkeypatch.setitem(sys.modules, "supabase", supabase_module)
    monkeypatch.setattr(database, "get_supabase_url", lambda: "https://example.supabase.co")
    monkeypatch.setattr(database, "get_supabase_anon_key", lambda: "public-test-key")

    token = "verified-user-access-token"
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/workers",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
        "scheme": "http",
    }
    request = Request(scope)

    client = get_user_scoped_client(request, current_user={"id": "user-123"})

    assert client is fake_client
    assert fake_client.postgrest.token == token
