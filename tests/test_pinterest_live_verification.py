"""Tests for read-only Pinterest account and board verification."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.pinterest_live_verification import (
    PinterestVerificationError,
    verify_account_and_boards,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("provider error", request=None, response=None)


class FakeClient:
    responses = []
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def get(self, url, **kwargs):
        return self.responses.pop(0)


def test_verification_returns_provider_account_and_board_data(monkeypatch):
    import httpx
    FakeClient.responses = [
        FakeResponse(200, {"username": "test-user", "account_type": "BUSINESS"}),
        FakeResponse(200, {"items": [{"id": "board-1", "name": "AI Tools"}]}),
    ]
    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = verify_account_and_boards("fake-token")
    assert result["account"]["username"] == "test-user"
    assert result["boards"] == [{"id": "board-1", "name": "AI Tools"}]
    assert result["published"] is False


def test_verification_rejects_missing_token():
    with pytest.raises(PinterestVerificationError, match="not connected"):
        verify_account_and_boards("")


def test_verification_reports_expired_or_invalid_token(monkeypatch):
    import httpx
    FakeClient.responses = [FakeResponse(401, {})]
    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(PinterestVerificationError, match="reconnect"):
        verify_account_and_boards("invalid-token")
