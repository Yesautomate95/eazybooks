"""
Xero bank-reconciliation pipeline.

Mirrors the Tally / QBO / Zoho bank-recon flow: parse a bank statement
(PDF / Excel), fetch the Xero transactions that hit the chosen bank account in
the same window, run the auto-matcher, hand the rest off to the user.

Reuses the statement parser and matching algorithm from
`agents.document_specific_functions.bank_reconciliation` so the two flows
stay in lockstep.

Sign convention
---------------
`xero_bank.list_bank_txns` already normalizes each txn so `amount` is signed
positive = inflow into the account (matching the QBO convention). The Tally
matcher works on Dr/Cr columns where:

    bank CREDIT (deposit / inflow)  ↔ ledger DEBIT
    bank DEBIT  (withdrawal / out)  ↔ ledger CREDIT

So a Xero inflow (positive) maps to a "ledger debit" row, and a Xero outflow
(negative) maps to a "ledger credit" row. After matching we relabel
`ledger_idx` → `xero_idx` in the response.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from applicationca.agents.document_specific_functions.bank_reconciliation import (
    compute_reconciliation_summary,
    parse_bank_statement_for_recon,
    run_automatic_matching,
)
from applicationca.services.xero_bank import list_bank_txns

logger = logging.getLogger(__name__)


def _to_iso(d: Any) -> Optional[str]:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, str) and d:
        return d[:10]
    return None


def _xero_txn_to_ledger_shape(txn: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """Convert a normalized Xero txn to the Dr/Cr shape the matcher expects."""
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
        s for s in (txn.get("payee"), txn.get("memo"), txn.get("split_account")) if s
    )

    return {
        "date": parsed_date,
        "description": description,
        "reference": txn.get("doc_number"),
        "debit_amount": debit_amount,
        "credit_amount": credit_amount,
        # Keep the original txn alongside so callers can recover Xero ids.
        "xero_txn": txn,
        "xero_idx": idx,
    }


def _serialize_ledger_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-friendly view of a ledger-shape row (Decimal -> float, date -> iso)."""
    return {
        "date": row["date"].isoformat() if row.get("date") else None,
        "description": row.get("description"),
        "reference": row.get("reference"),
        "debit_amount": float(row["debit_amount"]) if row.get("debit_amount") else None,
        "credit_amount": float(row["credit_amount"]) if row.get("credit_amount") else None,
        "xero_txn": row.get("xero_txn"),
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


async def process_bank_statement(
    file_content: bytes,
    file_name: str,
    *,
    user_id: str,
    tenant_id: str,
    account_id: str,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    xero_closing_balance: Optional[float] = None,
) -> Dict[str, Any]:
    """End-to-end bank reconciliation against a Xero bank account.

    Args:
        file_content: Raw PDF / Excel bytes.
        file_name: Original filename — used to pick the parser.
        user_id, tenant_id: Xero auth scope + active company.
        account_id: Xero AccountID of the bank account being reconciled.
        period_start, period_end: ISO YYYY-MM-DD. If omitted, inferred from
            the statement's transaction range.
        xero_closing_balance: Optional pre-computed Xero closing balance for
            the account on `period_end` — used for the recon difference.
            If not supplied, summary fields that depend on it are zeroed.

    Returns:
        {
          ok, period, statement, xero_txns, matches, summary,
          unmatched_statement, unmatched_xero, needs_review
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

    xero_txns = await list_bank_txns(
        user_id,
        account_id=account_id,
        start_date=period_start,
        end_date=period_end,
        tenant_id=tenant_id,
    )

    xero_ledger_rows = [_xero_txn_to_ledger_shape(t, i) for i, t in enumerate(xero_txns)]

    matches = run_automatic_matching(xero_ledger_rows, statement_txns)

    matched_ledger_idx = {m["ledger_idx"] for m in matches}
    matched_bank_idx = {m["bank_idx"] for m in matches}

    unmatched_xero = [r for i, r in enumerate(xero_ledger_rows) if i not in matched_ledger_idx]
    unmatched_stmt = [r for j, r in enumerate(statement_txns) if j not in matched_bank_idx]

    summary = compute_reconciliation_summary(
        ledger_closing_balance=Decimal(str(xero_closing_balance)) if xero_closing_balance is not None else None,
        bank_closing_balance=statement_closing,
        unmatched_ledger_txns=unmatched_xero,
        unmatched_bank_txns=unmatched_stmt,
    )

    serialized_matches = [
        {
            "xero_idx": m["ledger_idx"],
            "bank_idx": m["bank_idx"],
            "confidence": m["confidence"],
            "reason": m["reason"],
            "xero_txn": xero_ledger_rows[m["ledger_idx"]]["xero_txn"],
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
        "xero_txns": xero_txns,
        "matches": serialized_matches,
        "unmatched_xero": [_serialize_ledger_row(r) for r in unmatched_xero],
        "unmatched_statement": [_serialize_bank_row(r) for r in unmatched_stmt],
        "summary": summary,
        "needs_review": bool(unmatched_xero or unmatched_stmt),
    }


async def manual_match(
    *,
    xero_txn: Dict[str, Any],
    statement_row: Dict[str, Any],
) -> Dict[str, Any]:
    """User-driven pairing. The frontend sends one Xero txn + one statement
    row; we just stamp it as a manual match and let the controller persist."""
    return {
        "xero_txn": xero_txn,
        "statement_row": statement_row,
        "confidence": 1.0,
        "reason": "Manual match",
        "manual": True,
    }


def recompute_summary(
    *,
    unmatched_xero: List[Dict[str, Any]],
    unmatched_statement: List[Dict[str, Any]],
    xero_closing_balance: Optional[float],
    bank_closing_balance: Optional[float],
) -> Dict[str, Any]:
    """Re-derive the recon summary from whatever's currently unmatched. Used
    by manual-match and resolve-pending so the frontend can show live totals
    instead of the frozen extract-time summary.

    `unmatched_*` are the serialized row dicts (debit_amount/credit_amount as
    float | None). compute_reconciliation_summary handles both float and
    Decimal inputs via `Decimal(str(...))`."""
    lcb = Decimal(str(xero_closing_balance)) if xero_closing_balance is not None else None
    bcb = Decimal(str(bank_closing_balance)) if bank_closing_balance is not None else None
    return compute_reconciliation_summary(
        ledger_closing_balance=lcb,
        bank_closing_balance=bcb,
        unmatched_ledger_txns=unmatched_xero or [],
        unmatched_bank_txns=unmatched_statement or [],
    )
