"""
Xero bank-side helpers used by the bank-reconciliation flow.

  * `list_bank_accounts` — Bank accounts the user can reconcile.
  * `list_bank_txns` — transactions that hit a chosen account inside a date
    window, normalized to the same flat shape the QBO path produces so the
    matcher and frontend code is shared.

Unlike QBO (which uses a TransactionList report), Xero exposes a dedicated
GET /BankTransactions endpoint filterable via a `where` clause (account +
date range).

VERIFY: confirm the /BankTransactions `where` syntax (BankAccount.AccountID,
Date filter) and the amount sign convention against live docs.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from applicationca.services.xero_connector import xero_connector


# Xero chart-of-accounts types that represent reconcilable bank accounts.
# (Credit cards in Xero are also Type==BANK; the connector already filters to BANK.)
_BANK_ACCOUNT_TYPES = ("bank",)

# BankTransaction Type values that represent money flowing INTO the account.
# RECEIVE = money in (deposits / customer receipts); SPEND = money out.
# VERIFY against the Xero BankTransaction Type enum (RECEIVE / SPEND and the
# overpayment / prepayment variants).
_INFLOW_TYPES = {"RECEIVE", "RECEIVE-OVERPAYMENT", "RECEIVE-PREPAYMENT", "RECEIVE-TRANSFER"}


_XERO_DATE_RE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


def _parse_xero_date(s: Any) -> Optional[str]:
    """Parse a Xero date into YYYY-MM-DD.

    Xero RESPONSE dates look like ``/Date(1599609600000+0000)/`` (epoch-ms).
    Some endpoints also return plain ISO strings (``2020-09-09T00:00:00``); we
    handle both. Returns None when the value can't be parsed.
    """
    if s is None or s == "":
        return None
    text = str(s)
    m = _XERO_DATE_RE.search(text)
    if m:
        epoch_ms = int(m.group(1))
        dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    # Fallback: ISO-8601 (possibly with a time component).
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return text[:10] if len(text) >= 10 else text


async def list_bank_accounts(user_id: str, tenant_id: str) -> List[Dict[str, Any]]:
    """Return Bank accounts, normalized for the picker UI."""
    cards = await xero_connector.list_bank_accounts(user_id, tenant_id)
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
            "account_sub_type": raw.get("BankAccountType"),
            "currency": raw.get("CurrencyCode"),
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
    """Normalize a Xero BankTransaction to the flat shape the matcher expects.

    `amount` is signed positive = inflow into the account (matching the QBO
    convention) so the shared reconciliation matcher behaves identically.
    RECEIVE = + (money in), SPEND = - (money out).
    """
    txn_type = (t.get("Type") or "").upper()
    amount = _to_float(t.get("Total"))
    if amount and txn_type and txn_type not in _INFLOW_TYPES:
        amount = -abs(amount)
    elif amount and txn_type in _INFLOW_TYPES:
        amount = abs(amount)
    contact = t.get("Contact") or {}
    line_items = t.get("LineItems") or []
    memo = None
    if line_items:
        memo = line_items[0].get("Description")
    return {
        "id": t.get("BankTransactionID"),
        "type": t.get("Type"),
        "type_id": t.get("BankTransactionID"),
        "date": _parse_xero_date(t.get("Date")),
        "doc_number": t.get("Reference"),
        "payee": contact.get("Name"),
        "payee_id": contact.get("ContactID"),
        "memo": memo or t.get("Reference"),
        "split_account": (line_items[0].get("AccountCode") if line_items else None),
        "amount": amount,
    }


async def list_bank_txns(
    user_id: str,
    *,
    account_id: str,
    start_date: str,
    end_date: str,
    tenant_id: str,
) -> List[Dict[str, Any]]:
    """Fetch every transaction that hit `account_id` between start_date and
    end_date (YYYY-MM-DD inclusive). Returns a flat list of normalized rows."""
    # VERIFY: Xero `where` clause syntax — filter by bank account GUID and the
    # transaction Date range. Dates in `where` use DateTime(y,m,d).
    sy, sm, sd = start_date.split("-")
    ey, em, ed = end_date.split("-")
    where = (
        'BankAccount.AccountID==Guid("' + account_id + '")'
        + f" AND Date>=DateTime({int(sy)},{int(sm)},{int(sd)})"
        + f" AND Date<=DateTime({int(ey)},{int(em)},{int(ed)})"
    )
    rows = await xero_connector.list_bank_transactions(
        user_id,
        tenant_id,
        params={"where": where},
    )
    return [_normalize_txn(t) for t in rows]


async def get_account_closing_balance(
    user_id: str,
    *,
    account_id: str,
    as_of: str,
    tenant_id: str,
) -> Optional[Decimal]:
    """Derive the Xero book closing balance for `account_id` as of `as_of`
    (YYYY-MM-DD). This is the Xero equivalent of Tally's ledger closing balance /
    QBO's get_account_closing_balance — it's what makes the reconciliation
    difference mean something.

    Xero's API has no clean "account balance as of a date", so we roll the
    account's live balance back over every transaction that hit it strictly
    after `as_of`:

        closing(as_of) = current_balance - net_movement(after as_of)

    net_movement uses the same signed amounts `list_bank_txns` returns
    (positive = inflow), so this reuses the already-trusted BankTransactions
    path instead of parsing a balance report.

    Returns None if the account or its balance can't be resolved, so the caller
    can fall back to a user-supplied override.
    """
    accounts = await list_bank_accounts(user_id, tenant_id)
    acct = next((a for a in accounts if str(a.get("id")) == str(account_id)), None)
    if not acct or acct.get("current_balance") is None:
        return None

    current = Decimal(str(acct["current_balance"]))

    try:
        as_of_date = datetime.strptime(str(as_of)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return current  # can't bound the window; current balance is the best we have

    day_after = (as_of_date + timedelta(days=1)).isoformat()
    post_txns = await list_bank_txns(
        user_id,
        account_id=account_id,
        start_date=day_after,
        end_date="2999-12-31",
        tenant_id=tenant_id,
    )
    net_post = sum((Decimal(str(t.get("amount") or 0)) for t in post_txns), Decimal("0"))
    return current - net_post
