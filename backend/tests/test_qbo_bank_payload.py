"""
Tests for build_qbo_bank_payload — the normalized-voucher -> QBO payload builder.

Runnable two ways:
    python -m tests.test_qbo_bank_payload       # plain stdlib runner
    pytest tests/test_qbo_bank_payload.py       # if pytest is installed
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from applicationca.agents.qbo.bank_payload import (
    build_qbo_bank_payload,
    BankVoucherError,
    VOUCHER_TYPE_TO_ENTRY,
)

BANK = {"bank_account_id": "35", "bank_account_name": "HDFC Current A/c"}


def _v(**over):
    base = {
        "date": "12/03/2026",
        "narration": "Payment from Acme",
        "amount": 1000,
        "voucher_type": "Receipt",
        "party_ledger": "Sales",
        "party_ledger_id": "57",
    }
    base.update(over)
    return base


# --- Receipt -> Deposit ---------------------------------------------------

def test_receipt_builds_deposit():
    entry_type, p = build_qbo_bank_payload(_v(), **BANK)
    assert entry_type == "deposit", entry_type
    assert p["DepositToAccountRef"] == {"value": "35", "name": "HDFC Current A/c"}, p
    assert p["TxnDate"] == "2026-03-12", p
    line = p["Line"][0]
    assert line["DetailType"] == "DepositLineDetail", line
    assert line["Amount"] == 1000.0, line
    assert line["DepositLineDetail"]["AccountRef"] == {"value": "57", "name": "Sales"}, line


def test_deposit_attaches_customer_when_counterparty_present():
    v = _v(counterparty={"id": "12", "name": "Acme", "type": "Customer"})
    _, p = build_qbo_bank_payload(v, **BANK)
    entity = p["Line"][0]["DepositLineDetail"]["Entity"]
    assert entity == {"type": "Customer", "value": "12", "name": "Acme"}, entity


def test_deposit_omits_entity_without_counterparty():
    _, p = build_qbo_bank_payload(_v(), **BANK)
    assert "Entity" not in p["Line"][0]["DepositLineDetail"], p


# --- Payment -> Purchase --------------------------------------------------

def test_payment_builds_purchase():
    v = _v(voucher_type="Payment", party_ledger="Office Supplies", party_ledger_id="80",
           narration="Staples")
    entry_type, p = build_qbo_bank_payload(v, **BANK)
    assert entry_type == "purchase", entry_type
    assert p["AccountRef"] == {"value": "35", "name": "HDFC Current A/c"}, p
    assert p["PaymentType"] == "Cash", p
    line = p["Line"][0]
    assert line["DetailType"] == "AccountBasedExpenseLineDetail", line
    assert line["AccountBasedExpenseLineDetail"]["AccountRef"] == {"value": "80", "name": "Office Supplies"}, line


def test_purchase_attaches_vendor_at_top_level():
    v = _v(voucher_type="Payment", party_ledger="Office Supplies", party_ledger_id="80",
           counterparty={"id": "9", "name": "Staples", "type": "Vendor"})
    _, p = build_qbo_bank_payload(v, **BANK)
    assert p["EntityRef"] == {"type": "Vendor", "value": "9", "name": "Staples"}, p


def test_purchase_doc_number_maps_to_docnumber():
    v = _v(voucher_type="Payment", party_ledger="Rent", party_ledger_id="80", voucher_number="CHQ-44")
    _, p = build_qbo_bank_payload(v, **BANK)
    assert p["DocNumber"] == "CHQ-44", p


# --- Contra -> Transfer ---------------------------------------------------

def test_contra_builds_transfer_bank_to_party():
    # Contra moves money FROM bank TO the destination account (Dr party / Cr bank).
    v = _v(voucher_type="Contra", party_ledger="Savings A/c", party_ledger_id="40")
    entry_type, p = build_qbo_bank_payload(v, **BANK)
    assert entry_type == "transfer", entry_type
    assert p["FromAccountRef"] == {"value": "35", "name": "HDFC Current A/c"}, p
    assert p["ToAccountRef"] == {"value": "40", "name": "Savings A/c"}, p
    assert p["Amount"] == 1000.0, p
    assert "Line" not in p, p


# --- Journal -> JournalEntry ----------------------------------------------

def test_journal_builds_balanced_debit_credit():
    # Journal follows Tally's rule: Dr party / Cr bank.
    v = _v(voucher_type="Journal", party_ledger="Bank Charges", party_ledger_id="91")
    entry_type, p = build_qbo_bank_payload(v, **BANK)
    assert entry_type == "journalentry", entry_type
    lines = p["Line"]
    assert len(lines) == 2, lines
    by_type = {ln["JournalEntryLineDetail"]["PostingType"]: ln for ln in lines}
    assert by_type["Debit"]["JournalEntryLineDetail"]["AccountRef"]["value"] == "91", by_type
    assert by_type["Credit"]["JournalEntryLineDetail"]["AccountRef"]["value"] == "35", by_type
    # Debits must equal credits.
    assert by_type["Debit"]["Amount"] == by_type["Credit"]["Amount"] == 1000.0, by_type


# --- entry-type map sanity ------------------------------------------------

def test_entry_type_map_covers_four_tally_types():
    assert VOUCHER_TYPE_TO_ENTRY == {
        "Receipt": "deposit",
        "Payment": "purchase",
        "Contra": "transfer",
        "Journal": "journalentry",
    }


# --- date normalization ---------------------------------------------------

def test_iso_date_passes_through():
    _, p = build_qbo_bank_payload(_v(date="2026-03-12"), **BANK)
    assert p["TxnDate"] == "2026-03-12", p


def test_dash_and_dot_date_formats():
    for d in ("12-03-2026", "12.03.2026"):
        _, p = build_qbo_bank_payload(_v(date=d), **BANK)
        assert p["TxnDate"] == "2026-03-12", (d, p)


# --- amount handling ------------------------------------------------------

def test_amount_is_absolute_and_rounded():
    _, p = build_qbo_bank_payload(_v(amount=-1234.567), **BANK)
    assert p["Line"][0]["Amount"] == 1234.57, p


# --- error cases ----------------------------------------------------------

def test_unknown_voucher_type_raises():
    try:
        build_qbo_bank_payload(_v(voucher_type="Sales"), **BANK)
    except BankVoucherError:
        return
    raise AssertionError("expected BankVoucherError for unknown voucher type")


def test_zero_amount_raises():
    try:
        build_qbo_bank_payload(_v(amount=0), **BANK)
    except BankVoucherError:
        return
    raise AssertionError("expected BankVoucherError for zero amount")


def test_bad_date_raises():
    try:
        build_qbo_bank_payload(_v(date="not-a-date"), **BANK)
    except BankVoucherError:
        return
    raise AssertionError("expected BankVoucherError for bad date")


def test_missing_party_account_id_raises():
    try:
        build_qbo_bank_payload(_v(party_ledger_id=None), **BANK)
    except BankVoucherError:
        return
    raise AssertionError("expected BankVoucherError when party account id is missing")


def test_missing_bank_account_id_raises():
    try:
        build_qbo_bank_payload(_v(), bank_account_id=None, bank_account_name="X")
    except BankVoucherError:
        return
    raise AssertionError("expected BankVoucherError when bank account id is missing")


def test_voucher_can_override_bank_account():
    v = _v(bank_ledger="ICICI A/c", bank_ledger_id="99")
    _, p = build_qbo_bank_payload(v, **BANK)
    assert p["DepositToAccountRef"] == {"value": "99", "name": "ICICI A/c"}, p


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}\n{exc}")
    if failed:
        sys.exit(1)
    print(f"\n{len(fns)} passed")
