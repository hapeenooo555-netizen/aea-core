"""Conservative Airtel Money Transaction Enquiry response validation."""
from __future__ import annotations

from typing import Any


class AirtelReconciliationError(ValueError):
    """Airtel enquiry response cannot be safely matched to the requested order."""


def classify_transaction_enquiry(payload: Any, expected_transaction_id: str) -> dict[str, str]:
    """Validate identity but do not infer status meanings or payment success.

    The available portal material identifies the transaction fields, but the
    status-code mapping and authoritative amount/currency response schema
    still need confirmation. Every returned status therefore remains
    unverified and cannot trigger payment confirmation or fulfilment.
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
    airtel_money_id = transaction.get("airtel_money_id")
    message = transaction.get("message")
    return {
        "transaction_id": transaction_id,
        "status_code": status.strip(),
        "payment_state": "unverified",
        "airtel_money_id": airtel_money_id if isinstance(airtel_money_id, str) else "",
        "message": message if isinstance(message, str) else "",
    }


__all__ = ["AirtelReconciliationError", "classify_transaction_enquiry"]
