"""Security regression tests for Pinterest onboarding ownership checks."""

import pytest
from fastapi import HTTPException

from app.routers import connectors


class _BrokenWorkersQuery:
    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        raise RuntimeError("database unavailable")


class _BrokenClient:
    def table(self, name):
        assert name == "workers"
        return _BrokenWorkersQuery()


@pytest.mark.asyncio
async def test_start_onboarding_fails_closed_when_worker_ownership_cannot_be_verified(monkeypatch):
    def should_not_start(*_args, **_kwargs):
        pytest.fail("Onboarding must not start when worker ownership lookup fails")

    monkeypatch.setattr(connectors, "_get_user_scoped_pinterest_connector", should_not_start)

    request = connectors.OnboardingStartRequest(
        platform="pinterest",
        worker_id="worker-123",
    )

    with pytest.raises(HTTPException) as exc_info:
        await connectors.start_onboarding(
            request=request,
            current_user_id="owner-123",
            client=_BrokenClient(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Could not verify worker ownership"
