"""
Xero bank-voucher payload builder — the single source of truth for turning a
normalized bank voucher into a Xero transaction object.

This is the Xero analog of ``agents/qbo/bank_payload.build_qbo_bank_payload``
(itself the QBO analog of Tally's ``tally_connector.build_bank_vouchers_xml``).
Before this module existed, the React frontend built ``xero_payload`` objects
itself (and re-built them on every edit), duplicating Xero-schema knowledge
across the FE/BE boundary in two languages. Now the frontend only sends the
normalized contract and the backend owns 100% of the format, exactly like Tally
and QBO.

Pure functions, stdlib only — no I/O, no network, no xero_connector — so it is
trivially unit-testable and importable without the FastAPI app.

Normalized voucher contract (identical to Tally's/QBO's, plus Xero codes):
    {
        "date":            "DD/MM/YYYY" | "YYYY-MM-DD" | ...,
        "narration":       str,
        "amount":          positive number,            # direction comes from voucher_type
        "voucher_type":    "Receipt" | "Payment" | "Contra" | "Journal",
        "party_ledger":    str,   "party_ledger_id":   str,    # the Xero income/expense account (AccountID, name)
        "party_ledger_code": str,                              # Xero AccountCode (line items reference this)
        "bank_ledger":     str,   "bank_ledger_id":    str,    # (optional override of the batch bank account)
        "bank_ledger_code":  str,                              # Xero bank AccountCode (Journal/Contra need it)
        "reference":       str,   "voucher_number":    str,    # optional
        "counterparty":    {"id": str, "name": str, "type": "Customer"|"Vendor"} | None,  # background-only
    }

Voucher-type -> Xero object mapping (mirrors the Tally/QBO voucher types 1:1):
    Receipt -> BankTransaction RECEIVE   (money in)
    Payment -> BankTransaction SPEND     (money out)
    Contra  -> BankTransfer              (bank <-> bank, no contact)
    Journal -> ManualJournal             (explicit Debit/Credit lines)

CRITICAL — Xero references accounts by CODE, not GUID:
    BankTransaction LineItems carry ``AccountCode`` (the string Code), and the
    BankAccount itself by ``AccountID`` (GUID). BankTransfer references the two
    bank accounts by ``AccountID``. ManualJournal lines carry ``AccountCode``.
    So the normalized voucher MUST carry the relevant account *code* for the
    party leg (and the bank leg for Journal/Contra) — a name-only or id-only ref
    silently fails to post.
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime
from typing import Any, Dict, Optional, Tuple

# Canonical Tally voucher type -> Xero entry kind (the push dispatch suffix).
VOUCHER_TYPE_TO_ENTRY = {
    "Receipt": "deposit",
    "Payment": "expense",
    "Contra": "banktransfer",
    "Journal": "manualjournal",
}

VALID_VOUCHER_TYPES = tuple(VOUCHER_TYPE_TO_ENTRY.keys())

# Xero BankTransaction Type enum. RECEIVE = money in, SPEND = money out.
_TXN_TYPE_RECEIVE = "RECEIVE"
_TXN_TYPE_SPEND = "SPEND"


class BankVoucherError(ValueError):
    """Raised when a normalized voucher cannot be turned into a Xero payload.

    The batch push endpoint catches this per-row and reports it as a skip,
    mirroring how Tally's build_bank_vouchers_xml logs and skips bad rows.
    """


def _to_iso_date(value: Any) -> Optional[str]:
    """Normalize a date to Xero's ``YYYY-MM-DD``. Returns None if unparseable.

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


def _require(value: Optional[str], *, label: str) -> str:
    """Require a non-empty value (Xero resolves accounts by Code/AccountID —
    a missing ref silently fails to post)."""
    if not value:
        raise BankVoucherError(f"missing {label}")
    return str(value)


def _debit_credit_codes(
    voucher_type: str, bank_code: str, party_code: str
) -> Tuple[str, str]:
    """Return (debit_code, credit_code) using the SAME rule as Tally's
    build_bank_vouchers_xml: Receipt = Dr Bank/Cr Party; everything else =
    Dr Party/Cr Bank."""
    if voucher_type == "Receipt":
        return bank_code, party_code
    return party_code, bank_code


def build_xero_bank_payload(
    voucher: Dict[str, Any],
    *,
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Turn one normalized voucher into ``(entry_type, xero_payload)``.

    ``entry_type`` is one of deposit/expense/banktransfer/manualjournal — it
    tells the caller which Xero create method to hit.

    Raises BankVoucherError on any input that cannot produce a valid payload
    (bad/zero amount, unknown voucher type, unparseable date, missing account
    id / code).
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
    bank_id = _require(
        voucher.get("bank_ledger_id") or bank_account_id, label="bank account id"
    )
    bank_name = voucher.get("bank_ledger") or bank_account_name
    bank_code = voucher.get("bank_ledger_code")

    party_name = voucher.get("party_ledger")
    party_code = voucher.get("party_ledger_code")

    cp = voucher.get("counterparty") or None
    entry_type = VOUCHER_TYPE_TO_ENTRY[voucher_type]

    if entry_type == "deposit":
        # Xero RECEIVE bank transaction. Line books to the party income account
        # (by AccountCode); bank account is referenced by AccountID.
        _require(party_code, label="party account code")
        line: Dict[str, Any] = {
            "Description": narration or None,
            "UnitAmount": amount,
            "Quantity": 1,
            "AccountCode": party_code,
        }
        if voucher.get("party_ledger_id"):
            line["_account_id"] = str(voucher["party_ledger_id"])  # display-only, for history
        if party_name:
            line["_account_name"] = party_name
        payload: Dict[str, Any] = {
            "Type": _TXN_TYPE_RECEIVE,
            "BankAccount": {"AccountID": bank_id},
            "Date": iso_date,
            "LineItems": [line],
        }
        if narration:
            payload["bank_description"] = narration
        if voucher_number:
            payload["Reference"] = voucher_number
        if cp and cp.get("id"):
            payload["Contact"] = {"ContactID": str(cp["id"])}
            payload["customer_name"] = cp.get("name")
        return entry_type, payload

    if entry_type == "expense":
        # Xero SPEND bank transaction. Line books to the party expense account
        # (by AccountCode); bank account is referenced by AccountID.
        _require(party_code, label="party account code")
        line = {
            "Description": narration or None,
            "UnitAmount": amount,
            "Quantity": 1,
            "AccountCode": party_code,
        }
        if voucher.get("party_ledger_id"):
            line["_account_id"] = str(voucher["party_ledger_id"])  # display-only, for history
        if party_name:
            line["_account_name"] = party_name
        payload = {
            "Type": _TXN_TYPE_SPEND,
            "BankAccount": {"AccountID": bank_id},
            "Date": iso_date,
            "LineItems": [line],
        }
        if narration:
            payload["bank_description"] = narration
        if voucher_number:
            payload["Reference"] = voucher_number
        if cp and cp.get("id"):
            payload["Contact"] = {"ContactID": str(cp["id"])}
            payload["vendor_name"] = cp.get("name")
        return entry_type, payload

    if entry_type == "banktransfer":
        # Contra: money moves FROM the batch bank account TO the party (target
        # bank) account. Xero BankTransfer references both legs by AccountID.
        to_id = _require(
            voucher.get("party_ledger_id"), label="transfer destination account id"
        )
        payload = {
            "FromBankAccount": {"AccountID": bank_id},
            "ToBankAccount": {"AccountID": to_id},
            "Amount": amount,
            "Date": iso_date,
        }
        if narration:
            payload["bank_description"] = narration
        if bank_name:
            payload["_from_account_name"] = bank_name
        if party_name:
            payload["_to_account_name"] = party_name
        return entry_type, payload

    # manualjournal — explicit balanced Debit/Credit lines, referenced by Code.
    _require(bank_code, label="bank account code")
    _require(party_code, label="party account code")
    debit_code, credit_code = _debit_credit_codes(voucher_type, bank_code, party_code)

    def _mj_line(line_amount: float, account_code: str) -> Dict[str, Any]:
        # Xero ManualJournal lines: a positive LineAmount = Debit, negative =
        # Credit. We split the single voucher amount into the two legs.
        ln: Dict[str, Any] = {
            "LineAmount": line_amount,
            "AccountCode": account_code,
        }
        if narration:
            ln["Description"] = narration
        return ln

    payload = {
        "Date": iso_date,
        "JournalLines": [
            _mj_line(amount, debit_code),    # Debit  (positive)
            _mj_line(-amount, credit_code),  # Credit (negative)
        ],
    }
    if narration:
        payload["Narration"] = narration
        payload["bank_description"] = narration
    if voucher_number:
        payload["Reference"] = voucher_number
    return entry_type, payload
