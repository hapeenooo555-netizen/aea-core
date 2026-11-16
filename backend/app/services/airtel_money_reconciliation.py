"""Airtel Money Tanzania Transaction Enquiry response validation."""
from __future__ import annotations

from typing import Any


class AirtelReconciliationError(ValueError):
    """Airtel enquiry response cannot be safely matched to the requested order."""


_STATUS_STATES = {
    "TS": "provider_success",
    "TF": "provider_failed",
    "TIP": "in_progress",
    "TA": "ambiguous",
    "TE": "expired",
}


def classify_transaction_enquiry(payload: Any, expected_transaction_id: str) -> dict[str, str]:
    """Validate identity and classify documented Airtel transaction statuses.

    Airtel's Tanzania Collection API documentation defines TS, TF, TIP, TA,
    and TE. The enquiry response shown in the documentation does not expose
    authoritative amount/currency fields, so these classifications alone
    must never be used to confirm an order or fulfil a product.
    """
    if not isinstance(payload, dict) or not isinstance(expected_transaction_id, str) or not expected_transaction_id:
        raise AirtelReconciliationError("Invalid Airtel enquiry input.")
    data = payload.get("data")
    transaction = data.get("transaction") if isinstance(data, dict) else None
    if not isinstance(transaction, dict):
        raise AirtelReconciliationError("Airtel enquiry is missing transaction data.")
    transaction_id = transaction.get("id")
    if not isinstance(transaction_id, str) or transaction_id != expected_transaction_id:
        raise AirtelReconciliationError("Airtel transaction ID does not match the requested transaction.")
    status = transaction.get("status")
    if not isinstance(status, str) or not status.strip():
        raise AirtelReconciliationError("Airtel enquiry is missing a transaction status.")
    status_code = status.strip().upper()
    airtel_money_id = transaction.get("airtel_money_id")
    message = transaction.get("message")
    return {
        "transaction_id": transaction_id,
        "status_code": status_code,
        "payment_state": _STATUS_STATES.get(status_code, "unverified"),
        "airtel_money_id": airtel_money_id if isinstance(airtel_money_id, str) else "",
        "message": message if isinstance(message, str) else "",
    }


__all__ = ["AirtelReconciliationError", "classify_transaction_enquiry"]
