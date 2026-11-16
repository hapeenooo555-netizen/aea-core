from __future__ import annotations

import pytest

from app.services.airtel_money_reconciliation import AirtelReconciliationError, classify_transaction_enquiry


@pytest.mark.parametrize(("status", "expected_state"), [
    ("TS", "provider_success"),
    ("TF", "provider_failed"),
    ("TIP", "in_progress"),
    ("TA", "ambiguous"),
    ("TE", "expired"),
    ("UNKNOWN", "unverified"),
])
def test_enquiry_classifies_statuses_documented_by_airtel(status, expected_state):
    payload = {"data": {"transaction": {
        "id": "aea-tx-1", "status": status,
        "airtel_money_id": "airtel-ref-1", "message": "provider message",
    }}}
    result = classify_transaction_enquiry(payload, "aea-tx-1")
    assert result["payment_state"] == expected_state
    assert result["status_code"] == status
    assert result["transaction_id"] == "aea-tx-1"
    assert result["airtel_money_id"] == "airtel-ref-1"


def test_enquiry_rejects_mismatched_transaction_id():
    payload = {"data": {"transaction": {"id": "other", "status": "TS"}}}
    with pytest.raises(AirtelReconciliationError):
        classify_transaction_enquiry(payload, "aea-tx-1")


def test_enquiry_rejects_missing_status_or_transaction():
    with pytest.raises(AirtelReconciliationError):
        classify_transaction_enquiry({"data": {"transaction": {"id": "aea-tx-1"}}}, "aea-tx-1")
    with pytest.raises(AirtelReconciliationError):
        classify_transaction_enquiry({"status": {"success": True}}, "aea-tx-1")
