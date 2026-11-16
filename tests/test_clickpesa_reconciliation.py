from __future__ import annotations

import pytest

from app.services.clickpesa_reconciliation import (
    PaymentReconciliationError,
    select_verified_success,
)


def _payment(**overrides):
    row = {
        "id": "CP-TXN-001",
        "paymentReference": "CP-PAY-001",
        "orderReference": "order-001",
        "collectedAmount": "10000.00",
        "collectedCurrency": "TZS",
        "status": "SUCCESS",
    }
    row.update(overrides)
    return row


def test_success_requires_exact_order_amount_currency_and_reference():
    result = select_verified_success(
        [_payment()], order_reference="order-001", expected_amount=10000
    )
    assert result == {
        "provider": "clickpesa",
        "provider_payment_reference": "CP-PAY-001",
        "order_reference": "order-001",
        "amount": 10000,
        "currency": "TZS",
        "status": "SUCCESS",
    }


@pytest.mark.parametrize("status", ["PROCESSING", "FAILED", "CANCELED", "PENDING"])
def test_non_success_status_never_authorizes_fulfilment(status):
    assert select_verified_success(
        [_payment(status=status)],
        order_reference="order-001",
        expected_amount=10000,
    ) is None


def test_unrelated_order_is_not_matched():
    assert select_verified_success(
        [_payment(orderReference="another-order")],
        order_reference="order-001",
        expected_amount=10000,
    ) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"collectedAmount": "9999"},
        {"collectedAmount": "10000.50"},
        {"collectedCurrency": "USD"},
        {"paymentReference": ""},
    ],
)
def test_success_with_mismatched_or_missing_fields_fails_closed(changes):
    with pytest.raises(PaymentReconciliationError):
        select_verified_success(
            [_payment(**changes)],
            order_reference="order-001",
            expected_amount=10000,
        )


def test_multiple_successful_matches_are_rejected():
    with pytest.raises(PaymentReconciliationError):
        select_verified_success(
            [_payment(), _payment(id="CP-TXN-002", paymentReference="CP-PAY-002")],
            order_reference="order-001",
            expected_amount=10000,
        )


def test_malformed_provider_response_is_rejected():
    with pytest.raises(PaymentReconciliationError):
        select_verified_success(
            ["not-an-object"], order_reference="order-001", expected_amount=10000
        )
