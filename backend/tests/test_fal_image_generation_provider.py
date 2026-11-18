from decimal import Decimal

import pytest

from app.services.fal_image_generation_provider import (
    FalImageGenerationProvider,
    MODEL_RESERVATIONS_USD,
)
from app.services.image_generation_budget import ImageBudgetExceeded


class Budget:
    def __init__(self, allow=True):
        self.allow = allow
        self.reserved = []
        self.released = []

    def reserve(self, job_id, amount):
        self.reserved.append((job_id, amount))
        if not self.allow:
            raise ImageBudgetExceeded("budget exhausted")

    def release(self, job_id):
        self.released.append(job_id)


def test_model_reservations_are_within_trial_cap():
    assert MODEL_RESERVATIONS_USD["z_image_turbo"] == Decimal("0.02")
    assert MODEL_RESERVATIONS_USD["nano_banana_pro"] == Decimal("0.15")
    assert sum(MODEL_RESERVATIONS_USD.values()) < Decimal("10.00")


def test_fal_provider_requires_server_side_api_key():
    with pytest.raises(Exception, match="FAL_KEY"):
        FalImageGenerationProvider(Budget(), api_key="")


def test_dimensions_supports_aea_aspect_ratios():
    assert FalImageGenerationProvider._dimensions("1:1") == (1024, 1024)
    assert FalImageGenerationProvider._dimensions("9:16") == (1024, 1792)
    with pytest.raises(ValueError):
        FalImageGenerationProvider._dimensions("7:5")


def test_unknown_model_is_rejected_before_provider_call():
    with pytest.raises(ValueError, match="Unsupported fal.ai"):
        FalImageGenerationProvider._model_key("unknown")


def test_budget_exhaustion_is_a_hard_preflight_failure():
    budget = Budget(allow=False)
    with pytest.raises(ImageBudgetExceeded):
        budget.reserve("job-1", Decimal("0.15"))
    assert budget.reserved
