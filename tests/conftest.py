"""Shared test fixtures and configuration.

Provides the ``supabase_disabled`` fixture that patches the Supabase client
to ``None`` so that unit tests can run in in-memory fallback mode without a
live Supabase instance. Only tests that explicitly request this fixture are
affected — tests requiring a real or fake Supabase client are left alone.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


@pytest.fixture
def supabase_disabled(monkeypatch):
    """Patch database module so in-memory store fallback paths are used."""
    from app import database as database_module

    monkeypatch.setattr(database_module, "supabase_client", None, raising=False)
    monkeypatch.setattr(
        database_module, "is_supabase_configured", lambda: False, raising=False
    )
    yield
