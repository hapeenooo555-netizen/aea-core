from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from app.services import meta_user_connection as meta


def test_token_encryption_round_trip_and_ciphertext_is_not_plaintext(monkeypatch):
    monkeypatch.setenv("META_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = meta.encrypt_access_token("meta-secret-token")
    assert ciphertext != "meta-secret-token"
    assert meta.decrypt_access_token(ciphertext) == "meta-secret-token"


def test_missing_encryption_key_fails_closed(monkeypatch):
    monkeypatch.delenv("META_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(meta.MetaCredentialError):
        meta.encrypt_access_token("secret")


def test_invalid_encryption_key_fails_closed(monkeypatch):
    monkeypatch.setenv("META_CREDENTIAL_ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(meta.MetaCredentialError):
        meta.encrypt_access_token("secret")


def test_oauth_state_is_random_and_only_hash_is_persistable():
    state_a = meta.new_oauth_state()
    state_b = meta.new_oauth_state()
    assert state_a != state_b
    assert len(meta.hash_oauth_state(state_a)) == 64
    assert state_a not in meta.hash_oauth_state(state_a)


@pytest.mark.parametrize("state", ["", "short", "x" * 257, None])
def test_invalid_oauth_state_rejected(state):
    with pytest.raises(meta.MetaCredentialError):
        meta.hash_oauth_state(state)


def test_state_expiry_requires_timezone_aware_clock():
    with pytest.raises(ValueError):
        meta.oauth_state_expiry(datetime(2026, 11, 14))


def test_state_expiry_is_ten_minutes():
    issued = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    expires = datetime.fromisoformat(meta.oauth_state_expiry(issued))
    assert (expires - issued).total_seconds() == 600


def test_status_never_selects_credential_columns(monkeypatch):
    calls = {}

    class Query:
        def select(self, columns):
            calls["columns"] = columns
            return self
        def eq(self, *_):
            return self
        def limit(self, *_):
            return self
        def execute(self):
            class Response:
                data = [{"granted_scopes": ["pages_show_list"], "assets": {"facebook_pages": []}, "token_expires_at": None, "updated_at": "2026-11-14T10:00:00Z"}]
            return Response()

    class Table:
        def __call__(self, *_):
            return self
        def table(self, *_):
            return self
        def select(self, columns):
            return Query().select(columns)

    monkeypatch.setattr(meta, "_service_client", lambda: Table())
    result = meta.connection_status("owner-a")
    assert result["connected"] is True
    assert "encrypted_access_token" not in calls["columns"]
    assert "access_token" not in result


def test_disconnect_is_scoped_to_owner(monkeypatch):
    calls = {}

    class Query:
        def eq(self, key, value):
            calls["filter"] = (key, value)
            return self
        def execute(self):
            return None

    class Table:
        def table(self, name):
            calls["table"] = name
            return self
        def delete(self):
            return Query()

    monkeypatch.setattr(meta, "_service_client", lambda: Table())
    assert meta.disconnect_owner("owner-a") is True
    assert calls == {"table": "meta_user_connections", "filter": ("owner_id", "owner-a")}
