"""
QBO bank-side helpers used by the bank-reconciliation flow.

Two responsibilities:

  * `list_bank_accounts` — return Bank/CreditCard accounts the user can
    reconcile against.

  * `list_bank_txns` — return every transaction that hit a chosen account
    inside a date window, normalized to a flat shape the matcher and the
    frontend can both consume.

We use the QBO `TransactionList` report for the txn fetch. The alternative —
querying each entity type (Purchase, BillPayment, Deposit, Transfer,
JournalEntry, Payment) and joining client-side — is brittle because not every
entity supports filtering by `AccountRef` in QBO's SQL dialect. The report
returns one flat tree per account in a single round-trip.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from applicationca.services.qbo_connector import qbo_connector


_BANK_ACCOUNT_TYPES = ("Bank", "CreditCard")


async def list_bank_accounts(
    user_id: str, realm_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return Bank + CreditCard accounts, normalized for the picker UI."""
    raw = await qbo_connector.list_accounts(user_id, realm_id)
    out: List[Dict[str, Any]] = []
    for a in raw:
        if a.get("AccountType") not in _BANK_ACCOUNT_TYPES:
            continue
        out.append({
            "id": a.get("Id"),
            "name": a.get("Name") or a.get("FullyQualifiedName"),
            "account_type": a.get("AccountType"),
            "account_sub_type": a.get("AccountSubType"),
            "currency": (a.get("CurrencyRef") or {}).get("value"),
            "current_balance": a.get("CurrentBalance"),
        })
    return out


# ----------------------------------------------------------------------
# TransactionList report flattening
# ----------------------------------------------------------------------

# QBO report columns vary by realm/locale, so resolve by ColTitle. Covers both
# TransactionList (single signed Amount) and GeneralLedger (explicit Debit/Credit
# + running Balance) report shapes.
_COL_ALIASES = {
    "date": ("Date", "Txn Date"),
    "type": ("Transaction Type", "Type"),
    "doc_num": ("Num", "Doc Num"),
    "name": ("Name", "Vendor", "Customer", "Payee"),
    "memo": ("Memo/Description", "Memo", "Description"),
    "account": ("Account", "Split"),
    "amount": ("Amount",),
    "debit": ("Debit",),
    "credit": ("Credit",),
    "balance": ("Balance", "Running Balance"),
    "txn_id": ("ID", "Transaction ID"),
}


def _build_col_index(columns: List[Dict[str, Any]]) -> Dict[str, int]:
    """Map our canonical field name -> column index."""
    titles = [(c.get("ColTitle") or "").strip() for c in columns]
    idx: Dict[str, int] = {}
    for field, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if alias in titles:
                idx[field] = titles.index(alias)
                break
    # If NO money column matched (neither a single Amount nor Debit/Credit), every
    # row parses to 0.0 -> zero matches and a wrong closing balance. Surface it
    # loudly with the real titles so it's diagnosable.
    if "amount" not in idx and not ("debit" in idx or "credit" in idx):
        logger.warning(
            "QBO report: no money column matched (Amount / Debit / Credit); "
            "amounts will be 0. Actual column titles: %s", titles
        )
    return idx


def _walk_rows(rows: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Yield every leaf data row from a possibly nested QBO Rows tree."""
    out: List[List[Dict[str, Any]]] = []
    for r in rows or []:
        if r.get("type") == "Section":
            inner = (r.get("Rows") or {}).get("Row") or []
            out.extend(_walk_rows(inner))
        elif r.get("ColData"):
            out.append(r["ColData"])
    return out


def _to_float(s: Any) -> float:
    if s is None or s == "":
        return 0.0
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _normalize_row(col_data: List[Dict[str, Any]], idx: Dict[str, int]) -> Dict[str, Any]:
    def cell(field: str) -> Dict[str, Any]:
        i = idx.get(field)
        if i is None or i >= len(col_data):
            return {}
        return col_data[i] or {}

    txn_cell = cell("txn_id")
    type_cell = cell("type")
    name_cell = cell("name")

    return {
        "id": txn_cell.get("id") or txn_cell.get("value"),
        "type": type_cell.get("value"),
        "type_id": type_cell.get("id"),
        "date": cell("date").get("value"),
        "doc_number": cell("doc_num").get("value"),
        "payee": name_cell.get("value"),
        "payee_id": name_cell.get("id"),
        "memo": cell("memo").get("value"),
        "split_account": cell("account").get("value"),
        "amount": _to_float(cell("amount").get("value")),
    }


async def list_bank_txns(
    user_id: str,
    *,
    account_id: str,
    start_date: str,
    end_date: str,
    realm_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch every transaction that hit `account_id` between start_date and
    end_date (YYYY-MM-DD inclusive). Returns a flat list of normalized rows.

    The returned `amount` is signed exactly as QBO's report renders it
    (positive = inflow into the account from the account's perspective). Match
    against statement-line signed amounts the same way.
    """
    # Don't pass `columns=` — QBO drops Amount when given a custom column list
    # of unrecognized names. Defaults include Amount and we resolve by ColTitle.
    raw = await qbo_connector.report(
        user_id,
        "TransactionList",
        realm_id=realm_id,
        params={
            "account": account_id,
            "start_date": start_date,
            "end_date": end_date,
        },
    )

    columns = ((raw.get("Columns") or {}).get("Column") or [])
    rows = ((raw.get("Rows") or {}).get("Row") or [])
    if not columns or not rows:
        return []

    idx = _build_col_index(columns)
    return [_normalize_row(cd, idx) for cd in _walk_rows(rows)]


# ----------------------------------------------------------------------
# General Ledger fetch (the trustworthy debit/credit source)
# ----------------------------------------------------------------------
#
# Why GL and not TransactionList for reconciliation:
#
# TransactionList's single `Amount` column is signed by transaction TYPE, not by
# how the money moved through the *selected* account — a vendor Bill Payment and a
# customer-refund Expense can both render positive even though one leaves and one
# enters the bank. Mapping that sign to debit/credit silently inverts whole
# classes of entries (e.g. A/P bill payments), which breaks cross-amount matching
# and the closing balance.
#
# The GeneralLedger report is scoped to the account: its Debit / Credit columns
# (and the natural-amount sign) are stated from THAT account's perspective, so a
# debit is unambiguously money into the account and a credit money out. It also
# carries the running Balance, giving us the as-of closing balance in the same
# round-trip — no second full-history fetch.


def _gl_normalize_row(col_data: List[Dict[str, Any]], idx: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """Normalize one GL leaf row to {id,type,date,doc_number,payee,memo,
    split_account,debit,credit,amount,balance}. Returns None for non-transaction
    rows (Beginning/Ending Balance markers carry a balance but no debit/credit).

    Direction is resolved from the explicit Debit/Credit columns when present;
    otherwise from the account-scoped signed natural Amount (positive = debit =
    money into the account). Either way the sign is account-relative, unlike
    TransactionList.
    """
    def cell(field: str) -> Dict[str, Any]:
        i = idx.get(field)
        if i is None or i >= len(col_data):
            return {}
        return col_data[i] or {}

    debit = _to_float(cell("debit").get("value")) if "debit" in idx else 0.0
    credit = _to_float(cell("credit").get("value")) if "credit" in idx else 0.0

    # Fall back to the account-scoped signed natural Amount whenever the row has
    # NEITHER an explicit debit nor credit value. This covers two shapes:
    #   (1) the report exposes only a single signed Amount column, and
    #   (2) the report exposes a Debit column but the Credit column is missing or
    #       renamed (e.g. QBO honored `debt_amt` but not `credt_amt`) — without
    #       this, every credit-only row (all expenses/withdrawals) parses to
    #       (0, 0) below and is silently dropped, leaving the book side
    #       deposits-only. GL's natural amount is account-relative (in = +).
    if not debit and not credit:
        amt = _to_float(cell("amount").get("value"))
        if amt > 0:
            debit = amt
        elif amt < 0:
            credit = abs(amt)

    # Skip pure balance markers (no movement on either side).
    if not debit and not credit:
        return None

    txn_cell = cell("txn_id")
    type_cell = cell("type")
    name_cell = cell("name")
    return {
        "id": txn_cell.get("id") or txn_cell.get("value"),
        "type": type_cell.get("value"),
        "type_id": type_cell.get("id"),
        "date": cell("date").get("value"),
        "doc_number": cell("doc_num").get("value"),
        "payee": name_cell.get("value"),
        "payee_id": name_cell.get("id"),
        "memo": cell("memo").get("value"),
        "split_account": cell("account").get("value"),
        "debit": debit,
        "credit": credit,
        "amount": round(debit - credit, 2),  # account-relative net (in = +)
        "balance": _to_float(cell("balance").get("value")) if "balance" in idx else None,
    }


def _gl_extract_closing_balance(raw: Dict[str, Any], idx: Dict[str, int]) -> Optional[Decimal]:
    """Pull the account's ending balance from the GL section Summary row's
    Balance column (computed server-side for the requested period — no
    out-of-period fetch). Best-effort; returns None if the shape doesn't expose
    it so the caller can fall back."""
    bal_i = idx.get("balance")
    if bal_i is None:
        return None

    found: List[Decimal] = []

    def walk(rows: List[Dict[str, Any]]):
        for r in rows or []:
            summary = r.get("Summary")
            if summary and summary.get("ColData"):
                cols = summary["ColData"]
                if bal_i < len(cols):
                    v = (cols[bal_i] or {}).get("value")
                    if v not in (None, ""):
                        try:
                            found.append(Decimal(str(v).replace(",", "")))
                        except (ValueError, TypeError):
                            pass
            inner = (r.get("Rows") or {}).get("Row") or []
            if inner:
                walk(inner)

    walk(((raw.get("Rows") or {}).get("Row") or []))
    # Outermost section summary is the account total = closing balance.
    return found[-1] if found else None


async def fetch_account_ledger(
    user_id: str,
    *,
    account_id: str,
    start_date: str,
    end_date: str,
    realm_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch the account's General Ledger for [start_date, end_date] (inclusive).

    Returns {"transactions": [...], "closing_balance": Decimal|None}. Each
    transaction carries account-relative `debit`/`credit` (money in / money out),
    so the recon matcher and summary can trust the direction directly.
    """
    base_params = {
        "account": account_id,
        "start_date": start_date,
        "end_date": end_date,
    }

    async def _run(extra: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        try:
            raw = await qbo_connector.report(
                user_id, "GeneralLedger", realm_id=realm_id,
                params={**base_params, **(extra or {})},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("QBO GeneralLedger fetch failed (%s): %s", extra, exc)
            return None
        columns = ((raw.get("Columns") or {}).get("Column") or [])
        rows = ((raw.get("Rows") or {}).get("Row") or [])
        if not columns or not rows:
            return None
        return raw

    # Prefer explicit Debit/Credit (+ running Balance) columns for unambiguous
    # direction. QBO drops unrecognized columns rather than erroring, but if the
    # request comes back empty/errored, retry with the report defaults — GL's
    # default Amount is still ACCOUNT-relative (unlike TransactionList), so signs
    # stay correct via the natural-amount fallback in _gl_normalize_row.
    raw = await _run({
        "columns": "tx_date,txn_type,doc_num,name,memo,split_acc,subt_nat_amount,debt_amt,credt_amt,rbal_nat_amount",
    })
    if raw is None:
        raw = await _run(None)
    if raw is None:
        return {"transactions": [], "closing_balance": None}

    columns = ((raw.get("Columns") or {}).get("Column") or [])
    rows = ((raw.get("Rows") or {}).get("Row") or [])
    idx = _build_col_index(columns)
    leaves = _walk_rows(rows)
    txns = [t for cd in leaves if (t := _gl_normalize_row(cd, idx))]
    closing = _gl_extract_closing_balance(raw, idx)
    # Diagnostic: GL column shape + direction split. If credits==0 while the
    # account clearly has expenses, the Credit column failed to resolve.
    n_dr = sum(1 for t in txns if t["debit"])
    n_cr = sum(1 for t in txns if t["credit"])
    logger.info(
        "QBO GL fetch: titles=%s resolved=%s leaves=%d kept=%d (debit=%d credit=%d dropped=%d)",
        [(c.get("ColTitle") or "") for c in columns], sorted(idx.keys()),
        len(leaves), len(txns), n_dr, n_cr, len(leaves) - len(txns),
    )
    return {"transactions": txns, "closing_balance": closing}


# ----------------------------------------------------------------------
# Book closing balance (as-of period end)
# ----------------------------------------------------------------------

async def get_account_closing_balance(
    user_id: str,
    *,
    account_id: str,
    as_of: str,
    realm_id: Optional[str] = None,
) -> Optional[Decimal]:
    """Derive the QBO book closing balance for `account_id` as of `as_of`
    (YYYY-MM-DD). This is the QB equivalent of Tally's ledger closing balance —
    it's what makes the reconciliation difference mean something.

    QBO's API has no clean "account balance as of a date", so we roll the
    account's live CurrentBalance back over every transaction that hit it
    strictly after `as_of`:

        closing(as_of) = CurrentBalance - net_movement(after as_of)

    net_movement comes from the GeneralLedger fetch (account-relative
    debit/credit), NOT TransactionList — TransactionList's Amount is signed by
    transaction TYPE, which inverts whole classes of entries (the QA-1 bug)
    and would corrupt the roll-back.

    Note: tuned for Bank accounts (asset, positive CurrentBalance = funds on
    hand). CreditCard accounts carry inverted sign semantics; the figure is
    still internally consistent for the difference calc but treat it as
    indicative there.

    Returns None if the account or its balance can't be resolved, so the caller
    can fall back to a user-supplied override.
    """
    accounts = await list_bank_accounts(user_id, realm_id)
    acct = next((a for a in accounts if str(a.get("id")) == str(account_id)), None)
    if not acct or acct.get("current_balance") is None:
        return None

    current = Decimal(str(acct["current_balance"]))

    try:
        as_of_date = datetime.strptime(str(as_of)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return current  # can't bound the window; CurrentBalance is the best we have

    day_after = (as_of_date + timedelta(days=1)).isoformat()
    ledger = await fetch_account_ledger(
        user_id,
        account_id=account_id,
        start_date=day_after,
        end_date="2999-12-31",
        realm_id=realm_id,
    )
    net_post = sum(
        (Decimal(str(t.get("amount") or 0)) for t in ledger.get("transactions") or []),
        Decimal("0"),
    )
    return current - net_post
