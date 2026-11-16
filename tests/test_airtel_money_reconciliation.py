from __future__ import annotations

import pytest

from app.services.airtel_money_reconciliation import (
    AirtelReconciliationError,
    classify_transaction_enquiry,
)


@pytest.mark.parametrize(("status", "expected"), [
    ("TS", "success_unverified"),
    ("TF", "failed"),
    ("TIP", "pending"),
    ("TA", "ambiguous"),
    ("TE", "expired"),
])
def test_enquiry_statuses_are_classified_without_granting_payment(status, expected):
    payload = {"data": {"transaction": {
        "id": "aea-tx-1", "status": status,
        "airtel_money_id": "airtel-ref-1", "message": "provider message",
    }}}
    result = classify_transaction_enquiry(payload, "aea-tx-1")
    assert result["payment_state"] == expected
    assert result["transaction_id"] == "aea-tx-1"
    assert result["airtel_money_id"] == "airtel-ref-1"


def test_enquiry_rejects_mismatched_transaction_id():
    payload = {"data": {"transaction": {"id": "other", "status": "TS"}}}
    with pytest.raises(AirtelReconciliationError):
        classify_transaction_enquiry(payload, "aea-tx-1")


def test_enquiry_rejects_unknown_status_and_missing_transaction():
    with pytest.raises(AirtelReconciliationError):
        classify_transaction_enquiry({"data": {"transaction": {"id": "aea-tx-1", "status": "PAID"}}}, "aea-tx-1")
    with pytest.raises(AirtelReconciliationError):
        classify_transaction_enquiry({"status": {"success": True}}, "aea-tx-1")

