"""Fail-closed USD budget guard for image generation.

The database RPC is atomic across workers. A reservation is taken before any
provider request; actual cost is reconciled after a successful response.
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any


class ImageBudgetExceeded(RuntimeError):
    pass


class ImageBudgetUnavailable(RuntimeError):
    pass


class SupabaseImageBudget:
    def __init__(self, client: Any, *, limit_usd: Decimal | None = None):
        self.client = client
        try:
            self.limit_usd = limit_usd or Decimal(
                os.getenv("AEA_IMAGE_BUDGET_USD", "10.00")
            )
        except InvalidOperation as exc:
            raise ValueError("AEA_IMAGE_BUDGET_USD must be a decimal amount") from exc
        if self.limit_usd <= 0:
            raise ValueError("Image budget must be greater than zero")

    def reserve(self, job_id: str, estimate_usd: Decimal) -> None:
        if estimate_usd <= 0:
            raise ValueError("Reservation must be positive")
        try:
            result = self.client.rpc(
                "aea_reserve_image_budget",
                {
                    "p_job_id": job_id,
                    "p_amount_usd": str(estimate_usd),
                    "p_budget_limit_usd": str(self.limit_usd),
                },
            ).execute()
        except Exception as exc:
            raise ImageBudgetUnavailable("Image budget reservation failed closed") from exc
        data = getattr(result, "data", None)
        if data is False or data == {"reserved": False}:
            raise ImageBudgetExceeded("AEA image-generation trial budget is exhausted")
        if data is None:
            raise ImageBudgetUnavailable("Image budget RPC returned no confirmation")

    def settle(self, job_id: str, actual_usd: Decimal) -> None:
        try:
            result = self.client.rpc(
                "aea_settle_image_budget",
                {"p_job_id": job_id, "p_actual_usd": str(actual_usd)},
            ).execute()
        except Exception as exc:
            raise ImageBudgetUnavailable("Image budget settlement failed") from exc
        if getattr(result, "data", None) is not True:
            raise ImageBudgetUnavailable("Image budget settlement was not confirmed")

    def release(self, job_id: str) -> None:
        try:
            self.client.rpc("aea_release_image_budget", {"p_job_id": job_id}).execute()
        except Exception as exc:
            # Do not hide a failed release: the reservation remains held, which
            # is conservative and cannot cause the trial cap to be exceeded.
            raise ImageBudgetUnavailable("Image budget release failed") from exc
