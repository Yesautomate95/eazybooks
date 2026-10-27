"""
Zoho Books bank-side helpers used by the bank-reconciliation flow.

  * `list_bank_accounts` — Bank / Credit-card accounts the user can reconcile.
  * `list_bank_txns` — transactions that hit a chosen account inside a date
    window, normalized to the same flat shape the QBO path produces so the
    matcher and frontend code is shared.

Unlike QBO (which uses a TransactionList report), Zoho exposes a dedicated
GET /banktransactions endpoint filterable by account + date range.

VERIFY: confirm the /banktransactions query params (account_id, date_start,
date_end / filter_by) and the amount sign convention against live docs.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from applicationca.services.zoho_connector import zoho_connector


# Zoho chart-of-accounts types that represent reconcilable bank accounts.
_BANK_ACCOUNT_TYPES = ("bank", "credit_card")

# transaction_type values that represent money flowing INTO the account.
# VERIFY against the Zoho banktransactions transaction_type enum.
_INFLOW_TYPES = {
    "deposit", "customer_payment", "sales_without_invoices",
    "interest_income", "other_income", "owner_contribution", "expense_refund",
}


async def list_bank_accounts(user_id: str, organization_id: str) -> List[Dict[str, Any]]:
    """Return Bank + Credit-card accounts, normalized for the picker UI."""
    cards = await zoho_connector.list_bank_accounts(user_id, organization_id)
    out: List[Dict[str, Any]] = []
    for c in cards:
        atype = (c.get("account_type") or "").lower()
        if atype and atype not in _BANK_ACCOUNT_TYPES:
            continue
        raw = c.get("raw") or {}
        out.append({
            "id": c.get("list_id"),
            "name": c.get("name"),
            "account_type": c.get("account_type"),
            "account_sub_type": raw.get("account_type"),
            "currency": raw.get("currency_code"),
            "current_balance": c.get("balance"),
        })
    return out


def _to_float(s: Any) -> float:
    if s is None or s == "":
        return 0.0
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _normalize_txn(t: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a Zoho bank transaction to the flat shape the matcher expects.

    `amount` is signed positive = inflow into the account (matching the QBO
    convention) so the shared reconciliation matcher behaves identically.
    """
    txn_type = (t.get("transaction_type") or "").lower()
    amount = _to_float(t.get("amount"))
    if amount and txn_type and txn_type not in _INFLOW_TYPES:
        amount = -abs(amount)
    elif amount and txn_type in _INFLOW_TYPES:
        amount = abs(amount)
    return {
        "id": t.get("transaction_id") or t.get("banktransaction_id"),
        "type": t.get("transaction_type"),
        "type_id": t.get("transaction_id"),
        "date": t.get("date"),
        "doc_number": t.get("reference_number") or t.get("entry_number"),
        "payee": t.get("payee") or t.get("vendor_name") or t.get("customer_name"),
        "payee_id": t.get("vendor_id") or t.get("customer_id"),
        "memo": t.get("description") or t.get("reference_number"),
        "split_account": t.get("account_name"),
        "amount": amount,
    }


async def list_bank_txns(
    user_id: str,
    *,
    account_id: str,
    start_date: str,
    end_date: str,
    organization_id: str,
) -> List[Dict[str, Any]]:
    """Fetch every transaction that hit `account_id` between start_date and
    end_date (YYYY-MM-DD inclusive). Returns a flat list of normalized rows."""
    rows = await zoho_connector._paged_list(
        user_id,
        "/banktransactions",
        "banktransactions",
        organization_id=organization_id,
        params={
            "account_id": account_id,
            "date_start": start_date,
            "date_end": end_date,
        },
        max_pages=20,
    )
    return [_normalize_txn(t) for t in rows]


# ----------------------------------------------------------------------
# Account ledger (book side of the reconciliation)
# ----------------------------------------------------------------------

async def fetch_account_ledger(
    user_id: str,
    *,
    account_id: str,
    start_date: str,
    end_date: str,
    organization_id: str,
) -> Dict[str, Any]:
    """Fetch the account's book transactions for [start_date, end_date] (inclusive),
    in the same normalized shape ``qbo_bank.fetch_account_ledger`` returns, so the
    shared recon service (process_report) is ERP-agnostic above this seam.

    Returns ``{"transactions": [...], "closing_balance": Decimal|None}``. Each
    transaction carries account-relative ``debit``/``credit`` (money in / money
    out) derived from ``list_bank_txns``'s signed amount (positive = inflow), so
    the recon matcher and summary trust the direction directly.

    Unlike QBO's GeneralLedger report, Zoho's /banktransactions feed carries no
    period-end running balance, so ``closing_balance`` is returned as ``None`` and
    the caller falls back to ``get_account_closing_balance`` (CurrentBalance
    roll-back).
    """
    rows = await list_bank_txns(
        user_id,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        organization_id=organization_id,
    )
    txns: List[Dict[str, Any]] = []
    for t in rows:
        amt = round(_to_float(t.get("amount")), 2)
        if amt == 0:
            continue  # skip zero-movement / balance-marker rows
        debit = amt if amt > 0 else 0.0
        credit = abs(amt) if amt < 0 else 0.0
        txns.append({
            **t,
            "debit": debit,
            "credit": credit,
            "amount": round(debit - credit, 2),  # account-relative net (in = +)
            "balance": None,
        })
    return {"transactions": txns, "closing_balance": None}


# ----------------------------------------------------------------------
# Book closing balance (as-of period end)
# ----------------------------------------------------------------------

async def get_account_closing_balance(
    user_id: str,
    *,
    account_id: str,
    as_of: str,
    organization_id: str,
) -> Optional[Decimal]:
    """Derive the Zoho book closing balance for `account_id` as of `as_of`
    (YYYY-MM-DD). The Zoho analog of qbo_bank.get_account_closing_balance — it's
    what makes the reconciliation difference mean something.

    Zoho's bank-accounts list exposes a live `current_balance`; we roll it back
    over every transaction that hit the account strictly after `as_of`:

        closing(as_of) = current_balance - net_movement(after as_of)

    net_movement uses the same signed amounts `list_bank_txns` returns
    (positive = inflow), so this reuses the already-trusted banktransactions path
    instead of parsing a balance report.

    Returns None if the account or its balance can't be resolved, so the caller
    can fall back to a user-supplied override.
    """
    accounts = await list_bank_accounts(user_id, organization_id)
    acct = next((a for a in accounts if str(a.get("id")) == str(account_id)), None)
    if not acct or acct.get("current_balance") is None:
        return None

    current = Decimal(str(acct["current_balance"]))

    try:
        as_of_date = datetime.strptime(str(as_of)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return current  # can't bound the window; current_balance is the best we have

    day_after = (as_of_date + timedelta(days=1)).isoformat()
    post_txns = await list_bank_txns(
        user_id,
        account_id=account_id,
        start_date=day_after,
        end_date="2999-12-31",
        organization_id=organization_id,
    )
    net_post = sum((Decimal(str(t.get("amount") or 0)) for t in post_txns), Decimal("0"))
    return current - net_post
