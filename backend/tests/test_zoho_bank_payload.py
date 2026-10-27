"""
Zoho-specific bank payload assertions.

These pin the native Zoho fields that the generic ERP contract test deliberately
does not assert. Run directly with:

    python tests/test_zoho_bank_payload.py
"""

import importlib.util
import os


_BE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_BE, "applicationca/agents/zoho/bank_payload.py")


def _load():
    spec = importlib.util.spec_from_file_location("zoho_bank_payload", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _voucher(voucher_type):
    return {
        "date": "25/06/2026",
        "narration": "Statement row",
        "amount": 125.75,
        "voucher_type": voucher_type,
        "party_ledger": "Sales" if voucher_type == "Receipt" else "Rent",
        "party_ledger_id": "party-1",
        "bank_ledger": "HDFC Current",
        "bank_ledger_id": "bank-1",
        "counterparty": {"id": "contact-1", "name": "ACME", "type": "Customer"},
    }


def test_receipt_posts_as_bank_deposit():
    mod = _load()
    entry_type, payload = mod.build_zoho_bank_payload(
        _voucher("Receipt"),
        bank_account_id="bank-1",
        bank_account_name="HDFC Current",
    )

    assert entry_type == "deposit"
    assert payload["transaction_type"] == "deposit"
    assert payload["from_account_id"] == "party-1"
    assert payload["to_account_id"] == "bank-1"
    assert "account_id" not in payload
    assert "line_items" not in payload


def test_payment_posts_as_expense_paid_through_bank():
    mod = _load()
    entry_type, payload = mod.build_zoho_bank_payload(
        _voucher("Payment"),
        bank_account_id="bank-1",
        bank_account_name="HDFC Current",
    )

    assert entry_type == "expense"
    assert payload["account_id"] == "party-1"
    assert payload["paid_through_account_id"] == "bank-1"
    assert "transaction_type" not in payload
    assert "line_items" not in payload


if __name__ == "__main__":
    test_receipt_posts_as_bank_deposit()
    test_payment_posts_as_expense_paid_through_bank()
    print("Zoho bank payload tests passed")
