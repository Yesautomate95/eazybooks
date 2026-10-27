"""
SAP S/4HANA bank-reconciliation pipeline.

Mirrors the Tally / QBO / Zoho / Xero bank-recon flow: parse a bank statement
(PDF / Excel), fetch the SAP open items that hit the chosen bank account in the
same window, run the auto-matcher, hand the rest off to the user.

Reuses the statement parser and matching algorithm from
`agents.document_specific_functions.bank_reconciliation` so the two flows stay
in lockstep.

SAP data-model note
-------------------
Unlike Zoho/Xero (which expose a dedicated /banktransactions endpoint that
already normalizes one signed `amount` per txn), SAP FI has no bank-transaction
feed. We approximate the "transactions that hit the bank" by pulling the open
A/R and A/P items for the company code (sap_connector.list_invoices /
list_bills, both backed by list_open_items) and normalizing each raw
`A_OperationalAcctgDocItemCube` row to the same flat shape the matcher expects.

Sign convention
---------------
We normalize each SAP open item so `amount` is signed positive = inflow into
the bank account (matching the QBO/Zoho/Xero convention). The Tally matcher
works on Dr/Cr columns where:

    bank CREDIT (deposit / inflow)  <-> ledger DEBIT
    bank DEBIT  (withdrawal / out)  <-> ledger CREDIT

So an open A/R item (customer owes us -> expected inflow) maps to a positive
amount -> "ledger debit" row, and an open A/P item (we owe a vendor -> expected
outflow) maps to a negative amount -> "ledger credit" row. After matching we
relabel `ledger_idx` -> `sap_idx` in the response.

Persistence is handled by `sap_recon_store` (save_report / get_report /
list_reports / delete_report); these agent functions stay stateless transforms
the controller wires to those stores, exactly as the Zoho/Xero/QBO agents do.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from applicationca.agents.document_specific_functions.bank_reconciliation import (
    compute_reconciliation_summary,
    parse_bank_statement_for_recon,
    run_automatic_matching,
)
from applicationca.services.sap_connector import sap_connector

logger = logging.getLogger(__name__)


def _to_iso(d: Any) -> Optional[str]:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, str) and d:
        # Handle OData V2 epoch dates: /Date(1672531200000)/
        if d.startswith("/Date("):
            try:
                ms = int(d[len("/Date("):].split(")")[0].split("+")[0])
                return datetime.utcfromtimestamp(ms / 1000.0).strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                return None
        return d[:10]
    return None


def _to_float(s: Any) -> float:
    if s is None or s == "":
        return 0.0
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _normalize_sap_open_item(row: Dict[str, Any], fin_acct_type: str) -> Dict[str, Any]:
    """Normalize a raw `A_OperationalAcctgDocItemCube` open-item row to the flat
    shape the matcher / ledger conversion expect.

    `amount` is signed positive = inflow into the bank account:
      * A/R open item (FinancialAccountType 'D' -> customer owes us) -> +ve.
      * A/P open item (FinancialAccountType 'K' -> we owe a supplier) -> -ve.

    We fall back to the cube's own `DebitCreditCode` (S debit / H credit) if the
    financial-account-type heuristic is unavailable. Field names are taken
    defensively since the cube exposes several near-synonyms across releases.
    """
    raw_amount = (
        row.get("AmountInCompanyCodeCurrency")
        or row.get("AmountInTransactionCurrency")
        or row.get("AbsoluteAmountInCoCodeCrcy")
        or 0
    )
    amount = abs(_to_float(raw_amount))

    # Decide direction. Customer (D) receivable -> inflow; supplier (K) payable
    # -> outflow. Otherwise lean on the document's debit/credit code.
    if fin_acct_type == "D":
        signed = amount
    elif fin_acct_type == "K":
        signed = -amount
    else:
        dc = (row.get("DebitCreditCode") or "").upper()
        signed = amount if dc == "S" else -amount if dc == "H" else amount

    party = (
        row.get("Customer")
        or row.get("Supplier")
        or row.get("BusinessPartner")
        or row.get("GLAccount")
    )
    doc_number = (
        row.get("AccountingDocument")
        or row.get("OriginalReferenceDocument")
        or row.get("DocumentReferenceID")
    )
    reference = (
        row.get("DocumentReferenceID")
        or row.get("AccountingDocumentHeaderText")
        or doc_number
    )
    memo = (
        row.get("DocumentItemText")
        or row.get("AccountingDocumentHeaderText")
        or row.get("AssignmentReference")
    )

    return {
        "id": doc_number or row.get("AccountingDocumentItem"),
        "type": "open_item",
        "fin_acct_type": fin_acct_type,
        "date": _to_iso(
            row.get("PostingDate")
            or row.get("DocumentDate")
            or row.get("NetDueDate")
        ),
        "doc_number": doc_number,
        "reference": reference,
        "payee": party,
        "payee_id": party,
        "memo": memo,
        "company_code": row.get("CompanyCode"),
        "amount": signed,
        "raw": row,
    }


def _sap_txn_to_ledger_shape(txn: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """Convert a normalized SAP open item to the Dr/Cr shape the matcher expects."""
    amount = txn.get("amount") or 0.0
    debit_amount: Optional[Decimal] = None
    credit_amount: Optional[Decimal] = None
    if amount > 0:
        debit_amount = Decimal(str(amount))
    elif amount < 0:
        credit_amount = Decimal(str(-amount))

    parsed_date: Optional[date] = None
    iso = _to_iso(txn.get("date"))
    if iso:
        try:
            parsed_date = datetime.strptime(iso, "%Y-%m-%d").date()
        except ValueError:
            parsed_date = None

    description = " ".join(
        str(s) for s in (txn.get("payee"), txn.get("memo"), txn.get("reference")) if s
    )

    return {
        "date": parsed_date,
        "description": description,
        "reference": txn.get("doc_number") or txn.get("reference"),
        "debit_amount": debit_amount,
        "credit_amount": credit_amount,
        # Keep the original txn alongside so callers can recover SAP ids.
        "sap_txn": txn,
        "sap_idx": idx,
    }


def _serialize_ledger_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-friendly view of a ledger-shape row (Decimal -> float, date -> iso)."""
    return {
        "date": row["date"].isoformat() if row.get("date") else None,
        "description": row.get("description"),
        "reference": row.get("reference"),
        "debit_amount": float(row["debit_amount"]) if row.get("debit_amount") else None,
        "credit_amount": float(row["credit_amount"]) if row.get("credit_amount") else None,
        "sap_txn": row.get("sap_txn"),
    }


def _serialize_bank_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": row["date"].isoformat() if isinstance(row.get("date"), (date, datetime)) else row.get("date"),
        "description": row.get("description"),
        "reference": row.get("reference"),
        "debit_amount": float(row["debit_amount"]) if row.get("debit_amount") else None,
        "credit_amount": float(row["credit_amount"]) if row.get("credit_amount") else None,
        "is_opening_balance": row.get("is_opening_balance", False),
        "is_closing_balance": row.get("is_closing_balance", False),
    }


def _infer_period(transactions: List[Dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    dates = [t.get("date") for t in transactions if isinstance(t.get("date"), date)]
    if not dates:
        return None, None
    return min(dates).isoformat(), max(dates).isoformat()


def _within_window(iso_date: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    """SAP open-item reads aren't date-filterable here, so we window client-side."""
    if not iso_date:
        return True
    if start and iso_date < start:
        return False
    if end and iso_date > end:
        return False
    return True


async def process_bank_statement(
    *,
    file_content: bytes,
    file_name: str,
    user_id: str,
    company_code: str,
    account_id: str,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    sap_closing_balance: Optional[float] = None,
) -> Dict[str, Any]:
    """End-to-end bank reconciliation against a SAP S/4HANA bank account.

    Args:
        file_content: Raw PDF / Excel bytes.
        file_name: Original filename — used to pick the parser.
        user_id, company_code: SAP auth scope + active Company Code.
        account_id: SAP GL account (bank) being reconciled. Retained for the
            report header; SAP open items aren't filterable by bank account in
            this read path.
        period_start, period_end: ISO YYYY-MM-DD. If omitted, inferred from
            the statement's transaction range.
        sap_closing_balance: Optional pre-computed SAP closing balance for the
            account on `period_end` — used for the recon difference. If not
            supplied, summary fields that depend on it are zeroed.

    Returns:
        {
          ok, period, statement, sap_txns, matches, summary,
          unmatched_statement, unmatched_sap, needs_review
        }
    """
    parsed = parse_bank_statement_for_recon(file_content, file_name)
    statement_txns = parsed.get("transactions") or []
    statement_opening = parsed.get("opening_balance")
    statement_closing = parsed.get("closing_balance")

    if not statement_txns:
        return {
            "ok": False,
            "stage": "extract",
            "error": "Could not extract any transactions from statement",
        }

    inferred_start, inferred_end = _infer_period(statement_txns)
    period_start = period_start or inferred_start
    period_end = period_end or inferred_end

    if not period_start or not period_end:
        return {
            "ok": False,
            "stage": "period",
            "error": "Could not determine reconciliation period",
        }

    # SAP has no bank-txn feed; approximate via open A/R + A/P items for the
    # company code, then window client-side to the statement period.
    ar_rows = await sap_connector.list_invoices(user_id, company_code)
    ap_rows = await sap_connector.list_bills(user_id, company_code)

    sap_txns = (
        [_normalize_sap_open_item(r, "D") for r in ar_rows]
        + [_normalize_sap_open_item(r, "K") for r in ap_rows]
    )
    sap_txns = [t for t in sap_txns if _within_window(t.get("date"), period_start, period_end)]

    sap_ledger_rows = [_sap_txn_to_ledger_shape(t, i) for i, t in enumerate(sap_txns)]

    matches = run_automatic_matching(sap_ledger_rows, statement_txns)

    matched_ledger_idx = {m["ledger_idx"] for m in matches}
    matched_bank_idx = {m["bank_idx"] for m in matches}

    unmatched_sap = [r for i, r in enumerate(sap_ledger_rows) if i not in matched_ledger_idx]
    unmatched_stmt = [r for j, r in enumerate(statement_txns) if j not in matched_bank_idx]

    summary = compute_reconciliation_summary(
        ledger_closing_balance=Decimal(str(sap_closing_balance)) if sap_closing_balance is not None else None,
        bank_closing_balance=statement_closing,
        unmatched_ledger_txns=unmatched_sap,
        unmatched_bank_txns=unmatched_stmt,
    )

    serialized_matches = [
        {
            "sap_idx": m["ledger_idx"],
            "bank_idx": m["bank_idx"],
            "confidence": m["confidence"],
            "reason": m["reason"],
            "sap_txn": sap_ledger_rows[m["ledger_idx"]]["sap_txn"],
            "statement_row": _serialize_bank_row(statement_txns[m["bank_idx"]]),
        }
        for m in matches
    ]

    return {
        "ok": True,
        "period": {"start": period_start, "end": period_end},
        "statement": {
            "opening_balance": float(statement_opening) if statement_opening else None,
            "closing_balance": float(statement_closing) if statement_closing else None,
            "transactions": [_serialize_bank_row(t) for t in statement_txns],
        },
        "sap_txns": sap_txns,
        "matches": serialized_matches,
        "unmatched_sap": [_serialize_ledger_row(r) for r in unmatched_sap],
        "unmatched_statement": [_serialize_bank_row(r) for r in unmatched_stmt],
        "summary": summary,
        "needs_review": bool(unmatched_sap or unmatched_stmt),
    }


async def manual_match(
    *,
    sap_txn: Dict[str, Any],
    statement_row: Dict[str, Any],
) -> Dict[str, Any]:
    """User-driven pairing. The frontend sends one SAP txn + one statement
    row; we just stamp it as a manual match and let the controller persist."""
    return {
        "sap_txn": sap_txn,
        "statement_row": statement_row,
        "confidence": 1.0,
        "reason": "Manual match",
        "manual": True,
    }


def recompute_summary(
    *,
    unmatched_sap: List[Dict[str, Any]],
    unmatched_statement: List[Dict[str, Any]],
    sap_closing_balance: Optional[float],
    bank_closing_balance: Optional[float],
) -> Dict[str, Any]:
    """Re-derive the recon summary from whatever's currently unmatched. Used
    by manual-match and resolve-pending so the frontend can show live totals
    instead of the frozen extract-time summary.

    `unmatched_*` are the serialized row dicts (debit_amount/credit_amount as
    float | None). compute_reconciliation_summary handles both float and
    Decimal inputs via `Decimal(str(...))`."""
    try:
        lcb = Decimal(str(sap_closing_balance)) if sap_closing_balance is not None else None
        bcb = Decimal(str(bank_closing_balance)) if bank_closing_balance is not None else None
    except (InvalidOperation, ValueError):
        lcb = bcb = None
    return compute_reconciliation_summary(
        ledger_closing_balance=lcb,
        bank_closing_balance=bcb,
        unmatched_ledger_txns=unmatched_sap or [],
        unmatched_bank_txns=unmatched_statement or [],
    )
