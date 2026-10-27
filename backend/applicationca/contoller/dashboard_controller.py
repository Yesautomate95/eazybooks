"""
Unified analytics dashboard controller.

Aggregates live financial data across the four supported accounting
platforms — Tally, QuickBooks Online, Zoho Books, Xero — into the single
JSON shape consumed by the frontend `/dashboard/analytics` page.

Endpoints (all under /ca/dashboard):
  GET    /connections                   — unified connection status
  GET    /summary                       — aggregated KPIs, charts, lists
  POST   /disconnect/{platform}         — drop tokens for one platform

Design notes
------------
* Data is fetched live from each platform's connector on every request.
  The frontend's `useSyncedData` hook caches the response client-side and
  only re-hits this endpoint every 60s + on window focus, so live fetches
  are tolerable for v1. A server-side cache or background sync would slot
  in here behind the same JSON shape.
* Per-platform fetches run in parallel via asyncio.gather. A single
  platform failing returns its error inside the `errors` list — other
  platforms still contribute their data.
* Tally currently contributes connection status only. Real Tally
  aggregation requires a backend DB sync from the desktop bridge (the
  cloud server cannot reach a user's local Tally directly), which is
  scoped as separate follow-up work.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from applicationca.contoller.websocket_controller import manager as ws_manager
from applicationca.services.qbo_connector import (
    delete_tokens as qbo_delete_tokens,
    is_user_connected as qbo_is_user_connected,
    list_user_realms as qbo_list_realms,
    qbo_connector,
    revoke_token as qbo_revoke_token,
    load_tokens as qbo_load_tokens,
)
from applicationca.services.zoho_connector import (
    delete_tokens as zoho_delete_tokens,
    is_user_connected as zoho_is_user_connected,
    revoke_token as zoho_revoke_token,
    load_tokens as zoho_load_tokens,
    zoho_connector,
)
from applicationca.services.xero_connector import (
    delete_tokens as xero_delete_tokens,
    is_user_connected as xero_is_user_connected,
    list_connections as xero_list_tenants,
    revoke_token as xero_revoke_token,
    load_tokens as xero_load_tokens,
    xero_connector,
)
from utils.auth_utils import verify_jwt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
security = HTTPBearer()


# ─────────────────────────────────────────────────────────────────────────────
# Auth helper
# ─────────────────────────────────────────────────────────────────────────────

def _verify(credentials: HTTPAuthorizationCredentials) -> Dict[str, Any]:
    user = verify_jwt(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Date-range helpers
# ─────────────────────────────────────────────────────────────────────────────

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class DateRange:
    start: date
    end: date
    prev_start: date
    prev_end: date
    # Last 12 calendar months for trend charts — independent of `range`.
    trend_start: date
    trend_end: date
    # Window we actually query from the provider: the union of the trend window
    # and the selected period, so a historical custom range still gets fetched.
    fetch_start: date
    fetch_end: date

    def iso(self) -> Tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()


def _resolve_range(range_str: str, start_iso: Optional[str] = None,
                   end_iso: Optional[str] = None) -> DateRange:
    """Map a frontend range key (month/quarter/year/custom) to absolute dates.

    For `range=custom` the caller passes explicit `start_iso`/`end_iso`
    (YYYY-MM-DD); if either is missing or unparseable we fall back to the last
    90 days. The previous-period comparison mirrors the custom span.
    """
    today = date.today()
    if range_str == "year":
        start = date(today.year, 1, 1)
        end = today
        prev_end = start - timedelta(days=1)
        prev_start = date(prev_end.year, 1, 1)
    elif range_str == "quarter":
        q = (today.month - 1) // 3
        start = date(today.year, q * 3 + 1, 1)
        end = today
        prev_end = start - timedelta(days=1)
        prev_q = (prev_end.month - 1) // 3
        prev_start = date(prev_end.year, prev_q * 3 + 1, 1)
    elif range_str == "custom":
        s = _parse_iso_date(start_iso)
        e = _parse_iso_date(end_iso)
        if s and e:
            start, end = (s, e) if s <= e else (e, s)
        else:
            end = today
            start = end - timedelta(days=90)
        # Previous period = the equal-length window immediately before it.
        span = max((end - start).days, 1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span)
    else:  # "month"
        start = date(today.year, today.month, 1)
        end = today
        # previous calendar month
        if today.month == 1:
            prev_start = date(today.year - 1, 12, 1)
            prev_end = date(today.year - 1, 12, 31)
        else:
            prev_start = date(today.year, today.month - 1, 1)
            prev_end = start - timedelta(days=1)

    # Trend charts always show last 12 months regardless of `range`.
    trend_end = today
    y, m = today.year, today.month - 11
    while m <= 0:
        m += 12
        y -= 1
    trend_start = date(y, m, 1)

    # Fetch the union of the trend window and the selected period so a custom
    # range outside the trailing 12 months is still pulled from the provider.
    fetch_start = min(trend_start, start)
    fetch_end = max(trend_end, end)

    return DateRange(start, end, prev_start, prev_end,
                     trend_start, trend_end, fetch_start, fetch_end)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _twelve_month_keys(rng: DateRange) -> List[Tuple[str, str]]:
    """Return [(key, label)] for the trend window, oldest first."""
    out = []
    y, m = rng.trend_start.year, rng.trend_start.month
    for _ in range(12):
        out.append((f"{y:04d}-{m:02d}", MONTH_LABELS[m - 1]))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _parse_iso_date(value: Any) -> Optional[date]:
    """Tolerant ISO/QBO/Zoho date parser. Returns None on failure."""
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    # Xero sometimes returns /Date(1700000000000+0000)/
    if s.startswith("/Date(") and s.endswith(")/"):
        try:
            ms = int(s[6:].split("+")[0].split("-")[0])
            return datetime.utcfromtimestamp(ms / 1000).date()
        except Exception:
            return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:len(fmt)], fmt).date()
        except ValueError:
            continue
    # Last try: take only the leading YYYY-MM-DD chunk
    try:
        return datetime.fromisoformat(s.split("T")[0]).date()
    except Exception:
        return None


def _safe_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Per-platform aggregation container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlatformData:
    platform: str
    income: float = 0.0
    purchases: float = 0.0  # bills total = vendor purchases
    expenses: float = 0.0   # bills + other expense entries (overlaps purchases for v1)
    bank_balance: float = 0.0
    closing_stock: float = 0.0
    income_prev: float = 0.0
    expenses_prev: float = 0.0
    purchases_prev: float = 0.0
    monthly_income: Dict[str, float] = field(default_factory=dict)   # key=YYYY-MM
    monthly_expenses: Dict[str, float] = field(default_factory=dict)
    expense_categories: Dict[str, float] = field(default_factory=dict)
    income_streams: Dict[str, float] = field(default_factory=dict)
    top_customers: List[Dict[str, Any]] = field(default_factory=list)  # {id,name,value}
    top_vendors: List[Dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# QuickBooks Online aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _pnl_section_totals(report: Dict[str, Any]) -> Dict[str, float]:
    """Extract top-level section totals from a single-period ProfitAndLoss
    report, keyed by QBO's `group` attribute (Income, COGS, Expenses, …).

    The report is a Header/Columns/Rows tree; each top-level section row carries
    a `group` and a Summary row whose last ColData cell is the money total for
    the (single) period column. Returns {} for an empty/garbled report so the
    caller can fall back to transaction sums.
    """
    out: Dict[str, float] = {}
    rows = (((report or {}).get("Rows") or {}).get("Row")) or []
    for row in rows:
        group = row.get("group")
        if not group:
            continue
        coldata = ((row.get("Summary") or {}).get("ColData")) or []
        if coldata:
            out[group] = _safe_float(coldata[-1].get("value"))
    return out


def _pnl_monthly(report: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """From a ProfitAndLoss report summarised by Month, return
    {'YYYY-MM': {'income': x, 'expense': y}} using the Income and Expenses
    section summary rows.

    The Columns array carries one money column per month (with a StartDate in
    its MetaData) plus a leading Account column and a trailing Total column
    (neither has a StartDate, so they're skipped). Each section's Summary.ColData
    aligns positionally with Columns, so we map cell index → month via the
    columns' StartDate. Returns {} on an empty/garbled report for clean fallback.
    """
    cols = (((report or {}).get("Columns") or {}).get("Column")) or []
    idx_to_month: Dict[int, str] = {}
    for i, col in enumerate(cols):
        md = {m.get("Name"): m.get("Value") for m in (col.get("MetaData") or [])}
        d = _parse_iso_date(md.get("StartDate"))
        if d is not None:
            idx_to_month[i] = _month_key(d)

    out: Dict[str, Dict[str, float]] = {}
    section = {"Income": "income", "Expenses": "expense"}
    rows = (((report or {}).get("Rows") or {}).get("Row")) or []
    for row in rows:
        bucket = section.get(row.get("group"))
        if not bucket:
            continue
        coldata = ((row.get("Summary") or {}).get("ColData")) or []
        for i, cell in enumerate(coldata):
            mkey = idx_to_month.get(i)
            if mkey is None:
                continue
            out.setdefault(mkey, {"income": 0.0, "expense": 0.0})
            out[mkey][bucket] += _safe_float(cell.get("value"))
    return out


def _pnl_expense_accounts(report: Dict[str, Any]) -> Dict[str, float]:
    """Per-account expense totals from the Expenses section of a single-period
    ProfitAndLoss report. Drives the expense-by-category chart so it stays
    consistent with the expense KPI and isn't empty in a period whose expenses
    came from Purchases/Checks rather than Bills. Recurses sub-groups; skips
    Section/Summary rows (those have no leaf ColData)."""
    out: Dict[str, float] = {}

    def _walk(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            cd = row.get("ColData")
            if cd and len(cd) >= 2 and row.get("type") != "Section":
                name = (cd[0].get("value") or "").strip()
                val = _safe_float(cd[-1].get("value"))
                if name and val:
                    out[name] = out.get(name, 0.0) + val
            sub = ((row.get("Rows") or {}).get("Row")) or []
            if sub:
                _walk(sub)

    rows = (((report or {}).get("Rows") or {}).get("Row")) or []
    for row in rows:
        if row.get("group") == "Expenses":
            _walk(((row.get("Rows") or {}).get("Row")) or [])
    return out


def _report_entity_totals(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a summary report (VendorExpenses / CustomerIncome) into
    [{id, name, value}] — one entry per entity Data row (ColData[0] = name+id,
    last column = amount). Requiring an entity id cleanly excludes the grand-total
    and section rows. Returns [] for an empty/garbled report so the caller can
    fall back to transaction-derived totals."""
    out: List[Dict[str, Any]] = []

    def _walk(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            cd = row.get("ColData")
            if cd and len(cd) >= 2 and cd[0].get("id"):
                name = (cd[0].get("value") or "").strip()
                val = _safe_float(cd[-1].get("value"))
                if name and val:
                    out.append({"id": cd[0].get("id"), "name": name, "value": abs(val)})
            sub = ((row.get("Rows") or {}).get("Row")) or []
            if sub:
                _walk(sub)

    _walk((((report or {}).get("Rows") or {}).get("Row")) or [])
    return out


async def _fetch_qbo(user_id: str, rng: DateRange) -> PlatformData:
    pd = PlatformData(platform="quickbooks")
    realms = qbo_list_realms(user_id)
    if not realms:
        return pd
    # Aggregate across every connected realm — most users have one.
    for realm in realms:
        realm_id = realm["realm_id"]
        try:
            await _fetch_qbo_realm(user_id, realm_id, rng, pd)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[dashboard] QBO realm {realm_id} failed: {exc}")
    pd.top_customers = _top_n(pd.top_customers, 10)
    pd.top_vendors = _top_n(pd.top_vendors, 10)
    return pd


async def _fetch_qbo_realm(user_id: str, realm_id: str, rng: DateRange, pd: PlatformData) -> None:
    # Pull invoices and bills covering the fetch window (the union of the trend
    # window and the selected period — so a historical custom range that predates
    # the trailing 12 months is still covered). Paginated so >1000 rows aren't
    # silently dropped.
    inv_q = (
        f"SELECT Id, TxnDate, TotalAmt, CustomerRef, Line "
        f"FROM Invoice "
        f"WHERE TxnDate >= '{rng.fetch_start.isoformat()}' "
        f"AND TxnDate <= '{rng.fetch_end.isoformat()}'"
    )
    bill_q = (
        f"SELECT Id, TxnDate, TotalAmt, VendorRef, Line "
        f"FROM Bill "
        f"WHERE TxnDate >= '{rng.fetch_start.isoformat()}' "
        f"AND TxnDate <= '{rng.fetch_end.isoformat()}'"
    )

    # ProfitAndLoss reports are the authoritative source for the income / COGS /
    # expense KPIs: they net sales tax, include every transaction type (not just
    # Invoice/Bill), and honour the period. One call for the selected period and
    # one for the previous period (for the delta).
    pnl_cur_task = qbo_connector.report(
        user_id, "ProfitAndLoss", realm_id=realm_id,
        params={"start_date": rng.start.isoformat(), "end_date": rng.end.isoformat()},
    )
    pnl_prev_task = qbo_connector.report(
        user_id, "ProfitAndLoss", realm_id=realm_id,
        params={"start_date": rng.prev_start.isoformat(), "end_date": rng.prev_end.isoformat()},
    )
    # Month-by-month P&L over the trend window drives the income-vs-expenses and
    # monthly-expense charts, so the trend lines/bars match the report-based KPIs
    # (income net of tax, expenses distinct from purchases) rather than raw sums.
    pnl_monthly_task = qbo_connector.report(
        user_id, "ProfitAndLoss", realm_id=realm_id,
        params={
            "start_date": rng.trend_start.isoformat(),
            "end_date": rng.trend_end.isoformat(),
            "summarize_column_by": "Month",
        },
    )
    # Expenses-by-Vendor for the selected period — covers Bills, Purchases,
    # Checks and CC charges, so top vendors isn't empty when the period's vendor
    # spend wasn't recorded as Bills.
    vendorexp_task = qbo_connector.report(
        user_id, "VendorExpenses", realm_id=realm_id,
        params={"start_date": rng.start.isoformat(), "end_date": rng.end.isoformat()},
    )

    invoices_task = qbo_connector.query_all(user_id, inv_q, "Invoice", realm_id)
    bills_task = qbo_connector.query_all(user_id, bill_q, "Bill", realm_id)
    accounts_task = qbo_connector.list_accounts(user_id, realm_id)
    customers_task = qbo_connector.list_customers(user_id, realm_id)
    vendors_task = qbo_connector.list_vendors(user_id, realm_id)

    (inv_res, bill_res, accounts, customers, vendors,
     pnl_cur_res, pnl_prev_res, pnl_month_res, vendorexp_res) = await asyncio.gather(
        invoices_task, bills_task, accounts_task,
        customers_task, vendors_task,
        pnl_cur_task, pnl_prev_task, pnl_monthly_task, vendorexp_task,
        return_exceptions=True,
    )

    invoices = inv_res if isinstance(inv_res, list) else []
    bills = bill_res if isinstance(bill_res, list) else []
    accounts = accounts if isinstance(accounts, list) else []
    customers = customers if isinstance(customers, list) else []
    vendors = vendors if isinstance(vendors, list) else []
    pnl_cur = _pnl_section_totals(pnl_cur_res) if isinstance(pnl_cur_res, dict) else None
    pnl_prev = _pnl_section_totals(pnl_prev_res) if isinstance(pnl_prev_res, dict) else None
    pnl_monthly = _pnl_monthly(pnl_month_res) if isinstance(pnl_month_res, dict) else None

    # Transaction-summed totals — used ONLY as a fallback when the ProfitAndLoss
    # report is unavailable (these are tax-inclusive and accrual-only, which is
    # exactly why the report is preferred below). monthly dicts feed the trend
    # charts; scalars feed the KPIs.
    txn_income = txn_income_prev = 0.0
    txn_purchases = txn_purchases_prev = 0.0
    txn_expenses = txn_expenses_prev = 0.0
    txn_monthly_income: Dict[str, float] = defaultdict(float)
    txn_monthly_expenses: Dict[str, float] = defaultdict(float)
    txn_expense_cats: Dict[str, float] = defaultdict(float)

    # Invoices → income fallback, monthly trend, top customers, income streams
    customer_totals: Dict[str, float] = defaultdict(float)
    customer_names: Dict[str, str] = {}
    for inv in invoices:
        amt = _safe_float(inv.get("TotalAmt"))
        d = _parse_iso_date(inv.get("TxnDate"))
        if d is None:
            continue
        in_period = rng.start <= d <= rng.end
        key = _month_key(d)
        txn_monthly_income[key] += amt
        if in_period:
            txn_income += amt
        elif rng.prev_start <= d <= rng.prev_end:
            txn_income_prev += amt

        # Top customers rank by revenue invoiced in the SELECTED period only, so
        # the list tracks the range selector (the fetch window is wider — it also
        # feeds the 12-month trend chart).
        if in_period:
            cust_ref = inv.get("CustomerRef") or {}
            cust_id = cust_ref.get("value")
            if cust_id:
                customer_totals[cust_id] += amt
                customer_names.setdefault(cust_id, cust_ref.get("name") or cust_id)

            # Income streams from invoice line ItemRef (selected period only)
            for line in inv.get("Line", []) or []:
                detail = line.get("SalesItemLineDetail") or {}
                item_ref = detail.get("ItemRef") or {}
                stream_name = item_ref.get("name") or "Sales"
                line_amt = _safe_float(line.get("Amount"))
                pd.income_streams[stream_name] = pd.income_streams.get(stream_name, 0.0) + line_amt

    # Use the Customer list only to enrich display names — ranking is purely by
    # period revenue (no outstanding-balance entries, which would mix AR balance
    # into a "revenue contribution" list).
    cust_by_id = {c.get("Id"): c for c in customers}
    for cust_id, total in customer_totals.items():
        c = cust_by_id.get(cust_id, {})
        name = c.get("DisplayName") or customer_names.get(cust_id) or cust_id
        pd.top_customers.append({"id": f"qbo-{cust_id}", "name": name, "value": total})

    # Bills → purchases, expenses, monthly trend, top vendors, expense categories
    vendor_totals: Dict[str, float] = defaultdict(float)
    vendor_names: Dict[str, str] = {}
    for bill in bills:
        amt = _safe_float(bill.get("TotalAmt"))
        d = _parse_iso_date(bill.get("TxnDate"))
        if d is None:
            continue
        in_period = rng.start <= d <= rng.end
        key = _month_key(d)
        txn_monthly_expenses[key] += amt
        if in_period:
            txn_purchases += amt
            txn_expenses += amt
        elif rng.prev_start <= d <= rng.prev_end:
            txn_purchases_prev += amt
            txn_expenses_prev += amt

        # Top vendors rank by amount billed in the SELECTED period only.
        if in_period:
            v_ref = bill.get("VendorRef") or {}
            v_id = v_ref.get("value")
            if v_id:
                vendor_totals[v_id] += amt
                vendor_names.setdefault(v_id, v_ref.get("name") or v_id)

            # Expense categories (fallback) from bill line AccountRef
            for line in bill.get("Line", []) or []:
                detail = line.get("AccountBasedExpenseLineDetail") or {}
                acc_ref = detail.get("AccountRef") or {}
                cat = acc_ref.get("name") or "Uncategorised"
                line_amt = _safe_float(line.get("Amount"))
                txn_expense_cats[cat] += line_amt

    # Top vendors: prefer the Expenses-by-Vendor report (covers every spend type,
    # net of tax) so the list reflects all period vendor activity, not just Bills.
    # Fall back to Bill-derived totals if the report is unavailable.
    vendor_rows = _report_entity_totals(vendorexp_res) if isinstance(vendorexp_res, dict) else []
    if vendor_rows:
        for r in vendor_rows:
            pd.top_vendors.append({"id": f"qbo-{r['id']}", "name": r["name"], "value": r["value"]})
    else:
        vendor_by_id = {v.get("Id"): v for v in vendors}
        for v_id, total in vendor_totals.items():
            v = vendor_by_id.get(v_id, {})
            name = v.get("DisplayName") or vendor_names.get(v_id) or v_id
            pd.top_vendors.append({"id": f"qbo-{v_id}", "name": name, "value": total})

    # Income / Purchases (COGS) / Expenses — prefer the authoritative
    # ProfitAndLoss report; fall back to the transaction sums above only if the
    # report call failed. "Purchases" maps to Cost of Goods Sold; expenses are
    # operating expenses, now distinct from purchases.
    if pnl_cur is not None:
        pd.income += pnl_cur.get("Income", 0.0)
        pd.purchases += pnl_cur.get("COGS", 0.0)
        pd.expenses += pnl_cur.get("Expenses", 0.0)
    else:
        pd.income += txn_income
        pd.purchases += txn_purchases
        pd.expenses += txn_expenses
    if pnl_prev is not None:
        pd.income_prev += pnl_prev.get("Income", 0.0)
        pd.purchases_prev += pnl_prev.get("COGS", 0.0)
        pd.expenses_prev += pnl_prev.get("Expenses", 0.0)
    else:
        pd.income_prev += txn_income_prev
        pd.purchases_prev += txn_purchases_prev
        pd.expenses_prev += txn_expenses_prev

    # Monthly trend (income-vs-expenses + monthly-expense charts) — prefer the
    # month-summarised P&L so the trend matches the KPIs; fall back to the
    # tax-inclusive transaction sums if that report failed.
    if pnl_monthly is not None:
        for mkey, vals in pnl_monthly.items():
            pd.monthly_income[mkey] = pd.monthly_income.get(mkey, 0.0) + vals.get("income", 0.0)
            pd.monthly_expenses[mkey] = pd.monthly_expenses.get(mkey, 0.0) + vals.get("expense", 0.0)
    else:
        for mkey, v in txn_monthly_income.items():
            pd.monthly_income[mkey] = pd.monthly_income.get(mkey, 0.0) + v
        for mkey, v in txn_monthly_expenses.items():
            pd.monthly_expenses[mkey] = pd.monthly_expenses.get(mkey, 0.0) + v

    # Expense categories (expense-by-category chart) — prefer the P&L Expenses
    # section accounts so it matches the expense KPI and isn't empty when expenses
    # weren't recorded as Bills; fall back to bill-line accounts.
    pnl_exp_accts = _pnl_expense_accounts(pnl_cur_res) if isinstance(pnl_cur_res, dict) else {}
    exp_cats = pnl_exp_accts or txn_expense_cats
    for name, val in exp_cats.items():
        pd.expense_categories[name] = pd.expense_categories.get(name, 0.0) + val

    # Bank balance from accounts of type Bank (point-in-time; each account's own
    # CurrentBalance, so parent/sub accounts aren't double-counted).
    for acc in accounts:
        if (acc.get("AccountType") or "").lower() == "bank":
            pd.bank_balance += _safe_float(acc.get("CurrentBalance"))

    # Closing stock = QBO Inventory Asset account balance (authoritative; ties to
    # the books at QBO's average cost). Inventory posts to an Other Current Asset
    # account with AccountSubType "Inventory".
    for acc in accounts:
        if (acc.get("AccountSubType") or "").lower() == "inventory":
            pd.closing_stock += _safe_float(acc.get("CurrentBalance"))

# ─────────────────────────────────────────────────────────────────────────────
# Zoho Books aggregation
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_zoho(user_id: str, rng: DateRange) -> PlatformData:
    pd = PlatformData(platform="zoho")
    orgs = await zoho_connector.list_organizations(user_id)
    if not orgs:
        return pd
    for org in orgs:
        org_id = str(org.get("organization_id"))
        if not org_id:
            continue
        try:
            await _fetch_zoho_org(user_id, org_id, rng, pd)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[dashboard] Zoho org {org_id} failed: {exc}")
    pd.top_customers = _top_n(pd.top_customers, 10)
    pd.top_vendors = _top_n(pd.top_vendors, 10)
    return pd


async def _fetch_zoho_org(user_id: str, org_id: str, rng: DateRange, pd: PlatformData) -> None:
    params = {
        "date_start": rng.trend_start.isoformat(),
        "date_end": rng.trend_end.isoformat(),
    }
    invoices_task = zoho_connector.list_invoices(user_id, org_id, params)
    bills_task = zoho_connector.list_bills(user_id, org_id, params)
    accounts_task = zoho_connector.list_accounts(user_id, org_id)
    customers_task = zoho_connector.list_customers(user_id, org_id)
    vendors_task = zoho_connector.list_vendors(user_id, org_id)
    items_task = zoho_connector.list_items(user_id, org_id)

    invoices, bills, accounts, customers, vendors, items = await asyncio.gather(
        invoices_task, bills_task, accounts_task,
        customers_task, vendors_task, items_task,
        return_exceptions=True,
    )
    invoices = invoices if isinstance(invoices, list) else []
    bills = bills if isinstance(bills, list) else []
    accounts = accounts if isinstance(accounts, list) else []
    customers = customers if isinstance(customers, list) else []
    vendors = vendors if isinstance(vendors, list) else []
    items = items if isinstance(items, list) else []

    cust_totals: Dict[str, float] = defaultdict(float)
    cust_names: Dict[str, str] = {}
    for inv in invoices:
        amt = _safe_float(inv.get("total"))
        d = _parse_iso_date(inv.get("date"))
        if d is None:
            continue
        key = _month_key(d)
        pd.monthly_income[key] = pd.monthly_income.get(key, 0.0) + amt
        if rng.start <= d <= rng.end:
            pd.income += amt
        elif rng.prev_start <= d <= rng.prev_end:
            pd.income_prev += amt
        cid = inv.get("customer_id")
        if cid:
            cust_totals[cid] += amt
            cust_names.setdefault(cid, inv.get("customer_name") or cid)
        # Income streams — Zoho invoices carry a `line_items` array
        if d and rng.start <= d <= rng.end:
            for line in inv.get("line_items") or []:
                stream = line.get("name") or "Sales"
                pd.income_streams[stream] = pd.income_streams.get(stream, 0.0) + _safe_float(line.get("item_total"))

    for cid, total in cust_totals.items():
        pd.top_customers.append({
            "id": f"zoho-{cid}",
            "name": cust_names.get(cid, cid),
            "value": total,
        })
    # Add customers with outstanding balance even if not invoiced in window
    invoiced = set(cust_totals.keys())
    for c in customers:
        cid = c.get("list_id") or c.get("contact_id")
        bal = _safe_float(c.get("balance"))
        if cid and cid not in invoiced and bal > 0:
            pd.top_customers.append({
                "id": f"zoho-{cid}",
                "name": c.get("name") or cid,
                "value": bal,
            })

    vend_totals: Dict[str, float] = defaultdict(float)
    vend_names: Dict[str, str] = {}
    for bill in bills:
        amt = _safe_float(bill.get("total"))
        d = _parse_iso_date(bill.get("date"))
        if d is None:
            continue
        key = _month_key(d)
        pd.monthly_expenses[key] = pd.monthly_expenses.get(key, 0.0) + amt
        if rng.start <= d <= rng.end:
            pd.purchases += amt
            pd.expenses += amt
        elif rng.prev_start <= d <= rng.prev_end:
            pd.purchases_prev += amt
            pd.expenses_prev += amt
        vid = bill.get("vendor_id")
        if vid:
            vend_totals[vid] += amt
            vend_names.setdefault(vid, bill.get("vendor_name") or vid)
        if d and rng.start <= d <= rng.end:
            for line in bill.get("line_items") or []:
                cat = line.get("account_name") or "Uncategorised"
                pd.expense_categories[cat] = pd.expense_categories.get(cat, 0.0) + _safe_float(line.get("item_total"))

    for vid, total in vend_totals.items():
        pd.top_vendors.append({
            "id": f"zoho-{vid}",
            "name": vend_names.get(vid, vid),
            "value": total,
        })
    paid_in_window = set(vend_totals.keys())
    for v in vendors:
        vid = v.get("list_id") or v.get("contact_id")
        bal = _safe_float(v.get("balance"))
        if vid and vid not in paid_in_window and bal > 0:
            pd.top_vendors.append({
                "id": f"zoho-{vid}",
                "name": v.get("name") or vid,
                "value": bal,
            })

    for acc in accounts:
        if (acc.get("account_type") or "").lower() in ("bank", "cash"):
            pd.bank_balance += _safe_float(acc.get("balance"))

    for it in items:
        qty = _safe_float(it.get("stock_on_hand") if isinstance(it, dict) else 0)
        rate = _safe_float((it.get("raw") or {}).get("rate") if isinstance(it, dict) else 0) \
               or _safe_float(it.get("rate") if isinstance(it, dict) else 0)
        pd.closing_stock += qty * rate


# ─────────────────────────────────────────────────────────────────────────────
# Xero aggregation
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_xero(user_id: str, rng: DateRange) -> PlatformData:
    pd = PlatformData(platform="xero")
    tenants = await xero_list_tenants(user_id)
    if not tenants:
        return pd
    for t in tenants:
        tenant_id = t.get("tenantId") or t.get("tenant_id")
        if not tenant_id:
            continue
        try:
            await _fetch_xero_tenant(user_id, tenant_id, rng, pd)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[dashboard] Xero tenant {tenant_id} failed: {exc}")
    pd.top_customers = _top_n(pd.top_customers, 10)
    pd.top_vendors = _top_n(pd.top_vendors, 10)
    return pd


async def _fetch_xero_tenant(user_id: str, tenant_id: str, rng: DateRange, pd: PlatformData) -> None:
    # Xero filters via the `where` param using its own ODATA-like syntax.
    where = (
        f"Date >= DateTime({rng.trend_start.year}, {rng.trend_start.month}, {rng.trend_start.day}) "
        f"&& Date <= DateTime({rng.trend_end.year}, {rng.trend_end.month}, {rng.trend_end.day})"
    )
    inv_task = xero_connector.list_invoices(user_id, tenant_id, {"where": where})
    accounts_task = xero_connector.list_accounts(user_id, tenant_id)
    customers_task = xero_connector.list_customers(user_id, tenant_id)
    vendors_task = xero_connector.list_vendors(user_id, tenant_id)
    items_task = xero_connector.list_items(user_id, tenant_id)

    invoices, accounts, customers, vendors, items = await asyncio.gather(
        inv_task, accounts_task, customers_task, vendors_task, items_task,
        return_exceptions=True,
    )
    invoices = invoices if isinstance(invoices, list) else []
    accounts = accounts if isinstance(accounts, list) else []
    customers = customers if isinstance(customers, list) else []
    vendors = vendors if isinstance(vendors, list) else []
    items = items if isinstance(items, list) else []

    # Xero stores both sales (ACCREC) and bills (ACCPAY) in a single Invoices endpoint
    cust_totals: Dict[str, float] = defaultdict(float)
    vend_totals: Dict[str, float] = defaultdict(float)
    cust_names: Dict[str, str] = {}
    vend_names: Dict[str, str] = {}

    for inv in invoices:
        amt = _safe_float(inv.get("Total"))
        d = _parse_iso_date(inv.get("Date"))
        if d is None:
            continue
        kind = (inv.get("Type") or "").upper()
        key = _month_key(d)
        contact = inv.get("Contact") or {}
        cid = contact.get("ContactID")
        cname = contact.get("Name")

        if kind == "ACCREC":  # Sales invoice
            pd.monthly_income[key] = pd.monthly_income.get(key, 0.0) + amt
            if rng.start <= d <= rng.end:
                pd.income += amt
                for line in inv.get("LineItems") or []:
                    stream = line.get("Description") or "Sales"
                    pd.income_streams[stream] = pd.income_streams.get(stream, 0.0) + _safe_float(line.get("LineAmount"))
            elif rng.prev_start <= d <= rng.prev_end:
                pd.income_prev += amt
            if cid:
                cust_totals[cid] += amt
                cust_names.setdefault(cid, cname or cid)
        elif kind == "ACCPAY":  # Bill
            pd.monthly_expenses[key] = pd.monthly_expenses.get(key, 0.0) + amt
            if rng.start <= d <= rng.end:
                pd.purchases += amt
                pd.expenses += amt
                for line in inv.get("LineItems") or []:
                    acc_code = line.get("AccountCode") or "Uncategorised"
                    pd.expense_categories[acc_code] = pd.expense_categories.get(acc_code, 0.0) + _safe_float(line.get("LineAmount"))
            elif rng.prev_start <= d <= rng.prev_end:
                pd.purchases_prev += amt
                pd.expenses_prev += amt
            if cid:
                vend_totals[cid] += amt
                vend_names.setdefault(cid, cname or cid)

    for cid, total in cust_totals.items():
        pd.top_customers.append({
            "id": f"xero-{cid}",
            "name": cust_names.get(cid, cid),
            "value": total,
        })
    for vid, total in vend_totals.items():
        pd.top_vendors.append({
            "id": f"xero-{vid}",
            "name": vend_names.get(vid, vid),
            "value": total,
        })

    # Bank balance — Xero account cards already carry `balance`
    for acc in accounts:
        atype = (acc.get("account_type") or acc.get("Type") or "").lower()
        if atype == "bank":
            pd.bank_balance += _safe_float(acc.get("balance"))

    for it in items:
        qty = _safe_float(it.get("QuantityOnHand") if isinstance(it, dict) else 0)
        rate = _safe_float((it.get("PurchaseDetails") or {}).get("UnitPrice") if isinstance(it, dict) else 0) \
               or _safe_float(it.get("rate") if isinstance(it, dict) else 0)
        pd.closing_stock += qty * rate


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation across platforms → frontend shape
# ─────────────────────────────────────────────────────────────────────────────

def _top_n(rows: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: r.get("value", 0), reverse=True)[:n]


def _pct_delta(current: float, previous: float) -> float:
    if previous <= 0:
        return 0.0 if current <= 0 else 100.0
    return round((current - previous) / previous * 100.0, 1)


def _build_spark(monthly: Dict[str, float], months: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    return [{"i": i, "v": round(monthly.get(key, 0.0), 2)} for i, (key, _) in enumerate(months)]


def _aggregate(per_platform: List[PlatformData], rng: DateRange) -> Dict[str, Any]:
    months = _twelve_month_keys(rng)
    income = sum(p.income for p in per_platform)
    purchases = sum(p.purchases for p in per_platform)
    expenses = sum(p.expenses for p in per_platform)
    bank = sum(p.bank_balance for p in per_platform)
    stock = sum(p.closing_stock for p in per_platform)
    income_prev = sum(p.income_prev for p in per_platform)
    expenses_prev = sum(p.expenses_prev for p in per_platform)
    purchases_prev = sum(p.purchases_prev for p in per_platform)

    monthly_income_combined: Dict[str, float] = defaultdict(float)
    monthly_expenses_combined: Dict[str, float] = defaultdict(float)
    income_streams_combined: Dict[str, float] = defaultdict(float)
    expense_cats_combined: Dict[str, float] = defaultdict(float)
    customers_combined: List[Dict[str, Any]] = []
    vendors_combined: List[Dict[str, Any]] = []

    for p in per_platform:
        for k, v in p.monthly_income.items():
            monthly_income_combined[k] += v
        for k, v in p.monthly_expenses.items():
            monthly_expenses_combined[k] += v
        for k, v in p.income_streams.items():
            income_streams_combined[k] += v
        for k, v in p.expense_categories.items():
            expense_cats_combined[k] += v
        customers_combined.extend(p.top_customers)
        vendors_combined.extend(p.top_vendors)

    monthly = [
        {
            "month": label,
            "income": round(monthly_income_combined.get(key, 0.0), 2),
            "expenses": round(monthly_expenses_combined.get(key, 0.0), 2),
        }
        for key, label in months
    ]

    expense_categories = _condense_categories(expense_cats_combined, top=6)
    income_breakdown = _condense_categories(income_streams_combined, top=4, fallback_name="Sales", total_fallback=income)

    top_customers = _to_share_list(_top_n(customers_combined, 10))
    top_vendors = _to_share_list(_top_n(vendors_combined, 10))

    kpis = [
        _kpi("income", "Total Income", income, _pct_delta(income, income_prev),
             _build_spark(monthly_income_combined, months)),
        _kpi("purchases", "Total Purchases", purchases, _pct_delta(purchases, purchases_prev),
             _build_spark(monthly_expenses_combined, months)),  # no separate monthly COGS series; reuse the expense trend for the sparkline shape
        _kpi("expenses", "Total Expenses", expenses, _pct_delta(expenses, expenses_prev),
             _build_spark(monthly_expenses_combined, months)),
        _kpi("bank", "Bank Balance", bank, 0.0,
             [{"i": i, "v": round(bank, 2)} for i in range(12)]),
        _kpi("stock", "Closing Stock", stock, 0.0,
             [{"i": i, "v": round(stock, 2)} for i in range(12)]),
    ]

    return {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "sources": [p.platform for p in per_platform],
        "kpis": kpis,
        "monthly": monthly,
        "expenseCategories": expense_categories,
        "incomeBreakdown": income_breakdown,
        "topCustomers": top_customers,
        "topVendors": top_vendors,
    }


def _kpi(kid: str, label: str, value: float, delta: float, spark: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": kid,
        "label": label,
        "value": round(value, 2),
        "deltaPct": delta,
        "spark": spark or [{"i": i, "v": 0} for i in range(12)],
    }


def _to_share_list(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = sum(r.get("value", 0) for r in rows) or 1
    return [
        {
            "id": r.get("id"),
            "name": r.get("name") or "—",
            "value": round(r.get("value", 0), 2),
            "sharePct": round(r.get("value", 0) / total * 100, 1),
        }
        for r in rows
    ]


def _condense_categories(
    cats: Dict[str, float],
    *,
    top: int,
    fallback_name: str = "Other",
    total_fallback: float = 0.0,
) -> List[Dict[str, Any]]:
    if not cats:
        if total_fallback > 0:
            return [{"name": fallback_name, "value": round(total_fallback, 2)}] \
                if fallback_name == "Sales" \
                else []
        return []
    ordered = sorted(cats.items(), key=lambda kv: kv[1], reverse=True)
    head = ordered[:top]
    tail = ordered[top:]
    out = [{"name": name, "value": round(val, 2), "category": name} for name, val in head]
    if tail:
        other = sum(v for _, v in tail)
        if other > 0:
            out.append({"name": "Other", "value": round(other, 2), "category": "Other"})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Connection probe
# ─────────────────────────────────────────────────────────────────────────────

def _probe_oauth_platform(
    platform_id: str,
    is_connected_fn,
    load_tokens_fn,
    user_id: str,
) -> Dict[str, Any]:
    """Isolated per-platform connection probe.

    Catches everything (missing table, DB outage, deserialisation glitch)
    and reports the platform as disconnected with the error attached, so
    one platform's broken state can never take down the entire endpoint.
    """
    try:
        connected = is_connected_fn(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[dashboard] {platform_id} connection probe failed: {exc}")
        return {"status": "disconnected", "lastSyncedAt": None, "error": str(exc)}

    if not connected:
        return {"status": "disconnected", "lastSyncedAt": None, "error": None}

    try:
        tokens = load_tokens_fn(user_id) or {}
        updated = tokens.get("updated_at")
        last_synced = updated.isoformat() if hasattr(updated, "isoformat") else updated
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[dashboard] {platform_id} token load failed: {exc}")
        last_synced = None

    return {"status": "connected", "lastSyncedAt": last_synced, "error": None}


async def _probe_platform_status(user_id: str) -> Dict[str, Dict[str, Any]]:
    """Cheap connection check for each platform, without pulling any data.

    Each probe is wrapped so that one platform's failure (e.g. a missing
    table on a stale database) cannot 500 the whole endpoint.
    """
    status: Dict[str, Dict[str, Any]] = {
        "quickbooks": _probe_oauth_platform("quickbooks", qbo_is_user_connected, qbo_load_tokens, user_id),
        "zoho": _probe_oauth_platform("zoho", zoho_is_user_connected, zoho_load_tokens, user_id),
        "xero": _probe_oauth_platform("xero", xero_is_user_connected, xero_load_tokens, user_id),
    }

    # Tally — websocket-bridged. Best-effort with a short timeout so we don't
    # block the connections endpoint when the desktop app is offline.
    try:
        connected = await asyncio.wait_for(
            ws_manager.check_tally_connection(user_id, timeout=3),
            timeout=4,
        )
        status["tally"] = {
            "status": "connected" if connected else "disconnected",
            "lastSyncedAt": None,
            "error": None,
        }
    except Exception:
        status["tally"] = {"status": "disconnected", "lastSyncedAt": None, "error": None}

    return status


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/connections")
async def get_connections(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Unified connection status across all 4 platforms."""
    user = _verify(credentials)
    statuses = await _probe_platform_status(user["uid"])
    return statuses


@router.get("/summary")
async def get_summary(
    range: str = Query("month", description="month | quarter | year | custom"),
    start: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD), required for range=custom"),
    end: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD), required for range=custom"),
    sources: Optional[str] = Query(None, description="CSV: tally,quickbooks,zoho,xero"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Live aggregated dashboard payload for the current user."""
    user = _verify(credentials)
    uid = user["uid"]
    rng = _resolve_range(range, start, end)

    requested = None
    if sources:
        requested = {s.strip().lower() for s in sources.split(",") if s.strip()}

    # Decide which platforms to fetch — only those connected AND included
    # in the requested filter (if any).
    statuses = await _probe_platform_status(uid)

    fetch_jobs: List[Tuple[str, Any]] = []
    if statuses["quickbooks"]["status"] == "connected" and (requested is None or "quickbooks" in requested):
        fetch_jobs.append(("quickbooks", _fetch_qbo(uid, rng)))
    if statuses["zoho"]["status"] == "connected" and (requested is None or "zoho" in requested):
        fetch_jobs.append(("zoho", _fetch_zoho(uid, rng)))
    if statuses["xero"]["status"] == "connected" and (requested is None or "xero" in requested):
        fetch_jobs.append(("xero", _fetch_xero(uid, rng)))
    # Tally: connection status only for v1.

    per_platform: List[PlatformData] = []
    errors: List[Dict[str, str]] = []
    if fetch_jobs:
        results = await asyncio.gather(*(job for _, job in fetch_jobs), return_exceptions=True)
        for (name, _), res in zip(fetch_jobs, results):
            if isinstance(res, Exception):
                logger.error(f"[dashboard] {name} fetch failed: {res}")
                errors.append({"platform": name, "error": str(res)})
            else:
                per_platform.append(res)

    payload = _aggregate(per_platform, rng)
    if errors:
        payload["errors"] = errors
    return payload


@router.post("/disconnect/{platform}")
async def disconnect_platform(
    platform: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Revoke tokens and clear stored credentials for one platform."""
    user = _verify(credentials)
    uid = user["uid"]
    platform = platform.lower()

    if platform == "quickbooks":
        tokens = qbo_load_tokens(uid)
        if not tokens:
            return {"disconnected": False, "reason": "not connected"}
        try:
            await qbo_revoke_token(tokens["refresh_token"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"QBO revoke failed (continuing): {exc}")
        qbo_delete_tokens(uid)
        return {"disconnected": True, "platform": platform}

    if platform == "zoho":
        tokens = zoho_load_tokens(uid)
        if not tokens:
            return {"disconnected": False, "reason": "not connected"}
        try:
            await zoho_revoke_token(tokens["refresh_token"], tokens.get("dc"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Zoho revoke failed (continuing): {exc}")
        zoho_delete_tokens(uid)
        return {"disconnected": True, "platform": platform}

    if platform == "xero":
        tokens = xero_load_tokens(uid)
        if not tokens:
            return {"disconnected": False, "reason": "not connected"}
        try:
            await xero_revoke_token(tokens["refresh_token"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Xero revoke failed (continuing): {exc}")
        xero_delete_tokens(uid)
        return {"disconnected": True, "platform": platform}

    if platform == "tally":
        # Tally is bridge-based — disconnecting just means the desktop app
        # closes its WS. Nothing to revoke on the backend.
        return {"disconnected": True, "platform": platform, "note": "Close the Tally desktop bridge to disconnect."}

    raise HTTPException(status_code=400, detail=f"Unknown platform '{platform}'")
