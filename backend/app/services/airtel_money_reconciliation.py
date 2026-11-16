"""Conservative Airtel Money Transaction Enquiry response classification."""
from __future__ import annotations

from typing import Any


class AirtelReconciliationError(ValueError):
    """Airtel enquiry response cannot be safely matched to the requested order."""


_STATUS_MAP = {
    "TS": "success_unverified",
    "TF": "failed",
    "TIP": "pending",
    "TA": "ambiguous",
    "TE": "expired",
}


def classify_transaction_enquiry(payload: Any, expected_transaction_id: str) -> dict[str, str]:
    """Classify a response without treating TS alone as proof of payable amount.

    Airtel enquiry fields available in the supplied portal material include
    transaction.id, transaction.airtel_money_id, transaction.message and
    transaction.status. Amount/currency verification is intentionally left
    to a later step until the exact response schema is established.
    """
    if not isinstance(payload, dict) or not isinstance(expected_transaction_id, str) or not expected_transaction_id:
        raise AirtelReconciliationError("Invalid Airtel enquiry input.")
    data = payload.get("data")
    transaction = data.get("transaction") if isinstance(data, dict) else None
    if not isinstance(transaction, dict):
        raise AirtelReconciliationError("Airtel enquiry is missing transaction data.")
    transaction_id = transaction.get("id")
    if not isinstance(transaction_id, str) or transaction_id != expected_transaction_id:
        raise AirtelReconciliationError("Airtel transaction ID does not match the order.")
    status = transaction.get("status")
    if not isinstance(status, str) or status not in _STATUS_MAP:
        raise AirtelReconciliationError("Airtel returned an unknown transaction status.")
    airtel_money_id = transaction.get("airtel_money_id")
    message = transaction.get("message")
    return {
        "transaction_id": transaction_id,
        "status_code": status,
        "payment_state": _STATUS_MAP[status],
        "airtel_money_id": airtel_money_id if isinstance(airtel_money_id, str) else "",
        "message": message if isinstance(message, str) else "",
    }


__all__ = ["AirtelReconciliationError", "classify_transaction_enquiry"]
