"""
Zoho Books bank-voucher payload builder — the single source of truth for turning
a normalized bank voucher into a Zoho Books transaction object.

This is the Zoho analog of ``agents/qbo/bank_payload.build_qbo_bank_payload`` (and,
upstream, Tally's ``tally_connector.build_bank_vouchers_xml``). The frontend sends
only the ERP-agnostic normalized contract; the backend owns 100% of the Zoho JSON,
exactly like the QBO path — so the FE/BE boundary never re-implements Zoho-schema
knowledge in two languages.

Pure functions, stdlib only — no I/O, no network, no zoho_connector — so it is
trivially unit-testable and importable without the FastAPI app.

Normalized voucher contract (identical to QBO's, plus Zoho account ids):
    {
        "date":            "DD/MM/YYYY" | "YYYY-MM-DD" | ...,
        "narration":       str,
        "amount":          positive number,            # direction comes from voucher_type
        "voucher_type":    "Receipt" | "Payment" | "Contra" | "Journal",
        "party_ledger":    str,   "party_ledger_id": str,   # the Zoho income/expense account
        "bank_ledger":     str,   "bank_ledger_id":  str,   # (optional override of the batch bank account)
        "reference":       str,   "voucher_number":  str,   # optional
        "counterparty":    {"id": str, "name": str, "type": "Customer"|"Vendor"} | None,  # background-only
    }

Voucher-type -> Zoho object mapping (mirrors the QBO/Tally voucher types 1:1):
    Receipt -> BankTransaction transaction_type=deposit   (money in)
    Payment -> Expense paid through the selected bank account (money out)
    Contra  -> Bank transfer-fund  (bank <-> account, no entity)  via create_transfer
    Journal -> Journal             (explicit debit/credit lines)  via create_journal
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime
from typing import Any, Dict, Optional, Tuple

# Canonical Tally voucher type -> Zoho entry kind (the build/push dispatch suffix).
VOUCHER_TYPE_TO_ENTRY = {
    "Receipt": "deposit",
    "Payment": "expense",
    "Contra": "transfer",
    "Journal": "journal",
}

VALID_VOUCHER_TYPES = tuple(VOUCHER_TYPE_TO_ENTRY.keys())


class BankVoucherError(ValueError):
    """Raised when a normalized voucher cannot be turned into a Zoho payload.

    The batch push endpoint catches this per-row and reports it as a skip,
    mirroring how QBO's build_qbo_bank_payload / Tally's build_bank_vouchers_xml
    log and skip bad rows.
    """


def _to_iso_date(value: Any) -> Optional[str]:
    """Normalize a date to Zoho's ``YYYY-MM-DD``. Returns None if unparseable.

    Accepts date/datetime objects and the common string formats produced by
    statement extraction and the Tally-style UI (DD/MM/YYYY, YYYY-MM-DD, etc.).
    """
    if isinstance(value, (_date, datetime)):
        return value.strftime("%Y-%m-%d")
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Already ISO (optionally with a time component).
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY (statement / Tally UI default).
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", s)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return _date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def _account_id(name: Optional[str], list_id: Optional[str], *, label: str) -> str:
    """Return a Zoho account id, requiring it (Zoho resolves accounts by id, not
    name — a name-only ref silently fails to post)."""
    if not list_id:
        raise BankVoucherError(f"missing account id for {label}" + (f" '{name}'" if name else ""))
    return str(list_id)


def _debit_credit(voucher_type: str, bank_id: str, party_id: str) -> Tuple[str, str]:
    """Return (debit_account_id, credit_account_id) using the SAME rule as
    QBO/Tally: Receipt = Dr Bank/Cr Party; everything else = Dr Party/Cr Bank."""
    if voucher_type == "Receipt":
        return bank_id, party_id
    return party_id, bank_id


def build_zoho_bank_payload(
    voucher: Dict[str, Any],
    *,
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Turn one normalized voucher into ``(entry_type, zoho_payload)``.

    ``entry_type`` is one of deposit/expense/transfer/journal — it tells the
    caller which zoho_connector method to hit (create_bank_transaction for
    deposit, create_expense for expense, create_transfer for transfer,
    create_journal for journal).

    Raises BankVoucherError on any input that cannot produce a valid payload
    (bad/zero amount, unknown voucher type, unparseable date, missing account id).
    """
    voucher_type = (voucher.get("voucher_type") or "").strip().title()
    if voucher_type not in VOUCHER_TYPE_TO_ENTRY:
        raise BankVoucherError(
            f"unknown voucher_type {voucher.get('voucher_type')!r} "
            f"(expected one of {', '.join(VALID_VOUCHER_TYPES)})"
        )

    try:
        amount = round(abs(float(voucher.get("amount", 0))), 2)
    except (ValueError, TypeError):
        raise BankVoucherError(f"invalid amount {voucher.get('amount')!r}")
    if amount == 0:
        raise BankVoucherError("amount is zero")

    iso_date = _to_iso_date(voucher.get("date"))
    if not iso_date:
        raise BankVoucherError(f"invalid/empty date {voucher.get('date')!r}")

    narration = (voucher.get("narration") or "").strip()
    voucher_number = (voucher.get("voucher_number") or "").strip()

    # The bank account is normally the per-batch account, but a voucher may
    # override it (e.g. the destination leg of a Contra picked in the table).
    bank_name = voucher.get("bank_ledger") or bank_account_name
    bank_id = _account_id(bank_name, voucher.get("bank_ledger_id") or bank_account_id, label="bank account")
    party_name = voucher.get("party_ledger")
    party_id = _account_id(party_name, voucher.get("party_ledger_id"), label="party ledger / account")

    cp = voucher.get("counterparty") or None
    entry_type = VOUCHER_TYPE_TO_ENTRY[voucher_type]

    if entry_type == "deposit":
        # Money in: from the income/counter account into the selected bank.
        payload: Dict[str, Any] = {
            "transaction_type": "deposit",
            "from_account_id": party_id,
            "to_account_id": bank_id,
            "date": iso_date,
            "amount": amount,
        }
        if party_name:
            payload["from_account_name"] = party_name  # display-only; stripped before POST
        if bank_name:
            payload["to_account_name"] = bank_name     # display-only; stripped before POST
        if narration:
            payload["description"] = narration
            payload["bank_description"] = narration    # display-only; used for mapping memory
        if cp and cp.get("id"):
            payload["customer_id"] = str(cp["id"])
            payload["customer_name"] = cp.get("name")
        if voucher_number:
            payload["reference_number"] = voucher_number
        return entry_type, payload

    if entry_type == "expense":
        payload = {
            "account_id": party_id,
            "paid_through_account_id": bank_id,
            "date": iso_date,
            "amount": amount,
        }
        if party_name:
            payload["account_name"] = party_name              # display-only; stripped before POST
        if bank_name:
            payload["paid_through_account_name"] = bank_name  # display-only; stripped before POST
        if narration:
            payload["description"] = narration
            payload["bank_description"] = narration
        if cp and cp.get("id"):
            payload["vendor_id"] = str(cp["id"])
            payload["vendor_name"] = cp.get("name")
        if voucher_number:
            payload["reference_number"] = voucher_number
        return entry_type, payload

    if entry_type == "transfer":
        # Contra: money moves FROM bank TO the party account (Dr party / Cr bank).
        payload = {
            "from_account_id": bank_id,
            "to_account_id": party_id,
            "amount": amount,
            "date": iso_date,
        }
        if bank_name:
            payload["from_account_name"] = bank_name      # display-only; stripped before POST
        if party_name:
            payload["to_account_name"] = party_name       # display-only; stripped before POST
        if narration:
            payload["description"] = narration
        if voucher_number:
            payload["reference_number"] = voucher_number
        return entry_type, payload

    # journal
    debit_id, credit_id = _debit_credit(voucher_type, bank_id, party_id)

    def _je_line(debit_or_credit: str, acct_id: str, acct_name: Optional[str]) -> Dict[str, Any]:
        ln: Dict[str, Any] = {
            "account_id": acct_id,
            "debit_or_credit": debit_or_credit,   # "debit" | "credit"
            "amount": amount,
        }
        if acct_name:
            ln["account_name"] = acct_name        # display-only; stripped before POST
        if narration:
            ln["description"] = narration
        return ln

    debit_name = bank_name if voucher_type == "Receipt" else party_name
    credit_name = party_name if voucher_type == "Receipt" else bank_name

    payload = {
        "journal_date": iso_date,
        "line_items": [
            _je_line("debit", debit_id, debit_name),
            _je_line("credit", credit_id, credit_name),
        ],
    }
    if narration:
        payload["notes"] = narration
    if voucher_number:
        payload["reference_number"] = voucher_number
    return entry_type, payload
