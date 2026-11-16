"""Strict ClickPesa payment reconciliation primitives.

A webhook is only a notification. This module validates a fresh provider
query result against the immutable local order before trusted code may mark
the order paid or grant an entitlement.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class PaymentReconciliationError(ValueError):
    """Provider data cannot safely be matched to the local order."""


def _amount_as_minor_units(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaymentReconciliationError("Invalid provider amount.") from exc
    if not amount.is_finite() or amount < 0:
        raise PaymentReconciliationError("Invalid provider amount.")
    # TZS amounts are represented as whole shillings in the commerce schema.
    if amount != amount.to_integral_value():
        raise PaymentReconciliationError("Provider amount has unsupported fractional TZS.")
    return int(amount)


def select_verified_success(
    payment_rows: list[dict[str, Any]],
    *,
    order_reference: str,
    expected_amount: int,
    expected_currency: str = "TZS",
) -> dict[str, Any] | None:
    """Return the unique matching SUCCESS payment, or None if still pending.

    FAILED/PROCESSING/CANCELED transactions never authorize fulfilment.
    Any SUCCESS row with mismatched order, amount, or currency fails closed.
    Multiple SUCCESS rows for one order are rejected for manual investigation.
    """
    if not order_reference or expected_amount <= 0 or expected_currency != "TZS":
        raise PaymentReconciliationError("Invalid local order details.")
    if not isinstance(payment_rows, list):
        raise PaymentReconciliationError("Unexpected ClickPesa payment response.")

    successes: list[dict[str, Any]] = []
    for row in payment_rows:
        if not isinstance(row, dict):
            raise PaymentReconciliationError("Malformed ClickPesa payment record.")
        status = str(row.get("status", "")).strip().upper()
        if status != "SUCCESS":
            continue
        if str(row.get("orderReference", "")).strip() != order_reference:
            continue
        successes.append(row)

    if not successes:
        return None
    if len(successes) != 1:
        raise PaymentReconciliationError("Multiple successful payments match this order.")

    payment = successes[0]
    if _amount_as_minor_units(payment.get("collectedAmount")) != expected_amount:
        raise PaymentReconciliationError("Successful payment amount does not match the order.")
    if str(payment.get("collectedCurrency", "")).strip().upper() != expected_currency:
        raise PaymentReconciliationError("Successful payment currency does not match the order.")

    payment_reference = payment.get("paymentReference") or payment.get("id")
    if not isinstance(payment_reference, str) or not payment_reference.strip():
        raise PaymentReconciliationError("Successful payment is missing its provider reference.")

    return {
        "provider": "clickpesa",
        "provider_payment_reference": payment_reference.strip(),
        "order_reference": order_reference,
        "amount": expected_amount,
        "currency": expected_currency,
        "status": "SUCCESS",
    }
