from decimal import Decimal

import pytest

from app.services.image_generation_budget import (
    ImageBudgetExceeded,
    ImageBudgetUnavailable,
    SupabaseImageBudget,
)


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return Response(self.data)


class Client:
    def __init__(self, data=True, fail=False):
        self.data = data
        self.fail = fail
        self.calls = []

    def rpc(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            raise RuntimeError("database unavailable")
        return Query(self.data)


def test_budget_reservation_uses_atomic_rpc_and_ten_dollar_ceiling():
    client = Client()
    budget = SupabaseImageBudget(client)
    budget.reserve("00000000-0000-0000-0000-000000000001", Decimal("0.15"))
    name, args = client.calls[0]
    assert name == "aea_reserve_image_budget"
    assert args["p_budget_limit_usd"] == "10.00"


def test_budget_denial_stops_generation():
    budget = SupabaseImageBudget(Client(data=False))
    with pytest.raises(ImageBudgetExceeded):
        budget.reserve("00000000-0000-0000-0000-000000000002", Decimal("0.15"))


def test_budget_database_failure_fails_closed():
    budget = SupabaseImageBudget(Client(fail=True))
    with pytest.raises(ImageBudgetUnavailable):
        budget.reserve("00000000-0000-0000-0000-000000000003", Decimal("0.15"))


def test_budget_limit_cannot_be_configured_above_ten_by_migration():
    assert SupabaseImageBudget(Client()).limit_usd == Decimal("10.00")
