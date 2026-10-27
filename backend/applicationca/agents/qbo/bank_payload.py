"""
QBO bank-voucher payload builder — the single source of truth for turning a
normalized bank voucher into a QuickBooks Online transaction object.

This is the QBO analog of Tally's ``tally_connector.build_bank_vouchers_xml``.
Before this module existed, the React frontend built ``qbo_payload`` objects
itself (and re-built them on every edit), duplicating QBO-schema knowledge across
the FE/BE boundary in two languages — the root cause of Tally<->QB drift. Now the
frontend only sends the normalized contract and the backend owns 100% of the
format, exactly like Tally.

Pure functions, stdlib only — no I/O, no network, no qbo_connector — so it is
trivially unit-testable and importable without the FastAPI app.

Normalized voucher contract (identical to Tally's, plus QB account ids):
    {
        "date":            "DD/MM/YYYY" | "YYYY-MM-DD" | ...,
        "narration":       str,
        "amount":          positive number,            # direction comes from voucher_type
        "voucher_type":    "Receipt" | "Payment" | "Contra" | "Journal",
        "party_ledger":    str,   "party_ledger_id": str,   # the QB Income/Expense account
        "bank_ledger":     str,   "bank_ledger_id":  str,   # (optional override of the batch bank account)
        "reference":       str,   "voucher_number":  str,   # optional
        "counterparty":    {"id": str, "name": str, "type": "Customer"|"Vendor"} | None,  # background-only
    }

Voucher-type -> QBO object mapping (mirrors the Tally voucher types 1:1):
    Receipt -> Deposit          (money in)
    Payment -> Purchase         (money out, PaymentType=Cash)
    Contra  -> Transfer         (bank <-> account, no entity)
    Journal -> JournalEntry     (explicit Debit/Credit lines)
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime
from typing import Any, Dict, Optional, Tuple

# Canonical Tally voucher type -> QBO entry kind (the qbo_connector.create_* suffix).
VOUCHER_TYPE_TO_ENTRY = {
    "Receipt": "deposit",
    "Payment": "purchase",
    "Contra": "transfer",
    "Journal": "journalentry",
}

VALID_VOUCHER_TYPES = tuple(VOUCHER_TYPE_TO_ENTRY.keys())


class BankVoucherError(ValueError):
    """Raised when a normalized voucher cannot be turned into a QBO payload.

    The batch push endpoint catches this per-row and reports it as a skip,
    mirroring how Tally's build_bank_vouchers_xml logs and skips bad rows.
    """


def _to_iso_date(value: Any) -> Optional[str]:
    """Normalize a date to QBO's ``YYYY-MM-DD``. Returns None if unparseable.

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


def _account_ref(name: Optional[str], list_id: Optional[str], *, label: str) -> Dict[str, str]:
    """Build a QBO ``{value, name}`` AccountRef, requiring the id (QBO resolves
    accounts by Id, not name — a name-only ref silently fails to post)."""
    if not list_id:
        raise BankVoucherError(f"missing account id for {label}" + (f" '{name}'" if name else ""))
    ref = {"value": str(list_id)}
    if name:
        ref["name"] = name
    return ref


def _debit_credit(voucher_type: str, bank: Dict[str, str], party: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return (debit_ref, credit_ref) using the SAME rule as Tally's
    build_bank_vouchers_xml: Receipt = Dr Bank/Cr Party; everything else =
    Dr Party/Cr Bank."""
    if voucher_type == "Receipt":
        return bank, party
    return party, bank


def build_qbo_bank_payload(
    voucher: Dict[str, Any],
    *,
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Turn one normalized voucher into ``(entry_type, qbo_payload)``.

    ``entry_type`` is one of deposit/purchase/transfer/journalentry — it tells
    the caller which ``qbo_connector.create_*`` method to hit.

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
    bank = _account_ref(
        voucher.get("bank_ledger") or bank_account_name,
        voucher.get("bank_ledger_id") or bank_account_id,
        label="bank account",
    )
    party = _account_ref(
        voucher.get("party_ledger"),
        voucher.get("party_ledger_id"),
        label="party ledger / account",
    )

    cp = voucher.get("counterparty") or None
    entry_type = VOUCHER_TYPE_TO_ENTRY[voucher_type]

    if entry_type == "deposit":
        line: Dict[str, Any] = {
            "Amount": amount,
            "DetailType": "DepositLineDetail",
            "DepositLineDetail": {"AccountRef": party},
        }
        if narration:
            line["Description"] = narration
        if cp and cp.get("id"):
            line["DepositLineDetail"]["Entity"] = {
                "type": "Customer", "value": str(cp["id"]), "name": cp.get("name"),
            }
        payload: Dict[str, Any] = {
            "DepositToAccountRef": bank,
            "TxnDate": iso_date,
            "Line": [line],
        }
        if narration:
            payload["PrivateNote"] = narration
        return entry_type, payload

    if entry_type == "purchase":
        line = {
            "Amount": amount,
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {"AccountRef": party},
        }
        if narration:
            line["Description"] = narration
        payload = {
            "AccountRef": bank,
            "PaymentType": "Cash",
            "TxnDate": iso_date,
            "Line": [line],
        }
        if cp and cp.get("id"):
            payload["EntityRef"] = {
                "type": "Vendor", "value": str(cp["id"]), "name": cp.get("name"),
            }
        if voucher_number:
            payload["DocNumber"] = voucher_number
        if narration:
            payload["PrivateNote"] = narration
        return entry_type, payload

    if entry_type == "transfer":
        # Contra: money moves FROM bank TO the party account (Dr party / Cr bank).
        payload = {
            "FromAccountRef": bank,
            "ToAccountRef": party,
            "Amount": amount,
            "TxnDate": iso_date,
        }
        if narration:
            payload["PrivateNote"] = narration
        return entry_type, payload

    # journalentry
    debit, credit = _debit_credit(voucher_type, bank, party)

    def _je_line(posting_type: str, ref: Dict[str, str]) -> Dict[str, Any]:
        ln: Dict[str, Any] = {
            "Amount": amount,
            "DetailType": "JournalEntryLineDetail",
            "JournalEntryLineDetail": {"PostingType": posting_type, "AccountRef": ref},
        }
        if narration:
            ln["Description"] = narration
        return ln

    payload = {
        "TxnDate": iso_date,
        "Line": [_je_line("Debit", debit), _je_line("Credit", credit)],
    }
    if voucher_number:
        payload["DocNumber"] = voucher_number
    if narration:
        payload["PrivateNote"] = narration
    return entry_type, payload
