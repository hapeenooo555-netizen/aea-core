from __future__ import annotations

import pytest

from app.services.airtel_money_reconciliation import AirtelReconciliationError, classify_transaction_enquiry


@pytest.mark.parametrize("status", ["TS", "TF", "TIP", "TA", "TE", "UNKNOWN"] )
def test_enquiry_status_is_unverified_until_official_mapping_is_confirmed(status):
    payload = {"data": {"transaction": {
        "id": "aea-tx-1", "status": status,
        "airtel_money_id": "airtel-ref-1", "message": "provider message",
    }}}
    result = classify_transaction_enquiry(payload, "aea-tx-1")
    assert result["payment_state"] == "unverified"
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
