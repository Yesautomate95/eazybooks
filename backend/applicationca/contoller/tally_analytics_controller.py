"""
Tally analytics controller.

Serves the Tally Analytics page from OUR database — never by live-querying
Tally. The desktop tally-database-loader syncs each company's masters and
transactions into the shared Postgres (mst_* / trn_* tables); this controller
reads those tables, ALWAYS scoped to (user_id, company_name), and shapes them
into the JSON the frontend renders.

Hard rule (data isolation): every query below filters on BOTH the JWT's uid AND
the caller-supplied company. A user may own several companies; companies across
users must never mix. There is no code path that reads a tally table without
both filters.

Endpoints (mounted under /ca → /ca/tally/analytics/*):
  GET  /companies            — companies this user has synced, with sync status
  GET  /sync-status          — per-(user, company) sync cursor + honest status
  GET  /summary              — KPIs, charts and lists for one (user, company)
  POST /sync                 — ask the desktop bridge to run an incremental /
                               full (reconcile) sync for one company

Sign convention (Tally, as stored by the loader): ledger-entry `amount` and
`closing_balance` are NEGATIVE for debit and POSITIVE for credit. So sales
(credited) are positive, purchases/expenses (debited) are negative, and a
positive bank/asset balance is stored negative. We negate where a debit-natured
figure is shown as a positive real-world number, and use ABS for "top N by
magnitude" lists so a sign flip in any deployment can't invert a ranking.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.contoller.websocket_controller import manager as ws_manager
from utils.auth_utils import verify_jwt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tally/analytics", tags=["Tally Analytics"])
security = HTTPBearer()

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Tally reserved primary groups — used to classify ledgers without guessing.
INCOME_GROUPS = ("Sales Accounts", "Direct Incomes", "Indirect Incomes")
EXPENSE_GROUPS = ("Purchase Accounts", "Direct Expenses", "Indirect Expenses")
DEBTOR_GROUPS = ("Sundry Debtors",)
CREDITOR_GROUPS = ("Sundry Creditors",)
BANK_GROUPS = ("Bank Accounts", "Bank OD A/c", "Bank OCC A/c")
CASH_GROUPS = ("Cash-in-hand",)
TAX_GROUPS = ("Duties & Taxes",)


# Recursive CTE resolving every group name (for this tenant) that rolls up to one
# of the given Tally primary groups. We classify by walking mst_group.parent —
# NOT by trusting mst_group.primary_group, which some sync paths (e.g. the HTTP
# masters sync) leave blank, silently emptying every primary-group-filtered panel
# for the affected company. Seeds at the named primary groups and descends into
# all sub-groups; the `root` column tags each resolved group with the primary it
# descends from, so a single CTE can serve multi-bucket queries (income vs
# expense). UNION (not UNION ALL) dedups, so a malformed hierarchy can't loop.
#
# Bind params, in order: (uid, company, roots_array, uid, company).
_GROUP_TREE_CTE = """
WITH RECURSIVE _grp(name, root) AS (
    SELECT name, name
    FROM mst_group
    WHERE user_id = %s AND company_name = %s AND name = ANY(%s)
    UNION
    SELECT c.name, t.root
    FROM mst_group c
    JOIN _grp t ON c.parent = t.name
    WHERE c.user_id = %s AND c.company_name = %s
)
"""


# ─────────────────────────────────────────────────────────────────────────────
# Auth + DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _verify(credentials: HTTPAuthorizationCredentials) -> Dict[str, Any]:
    user = verify_jwt(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


def _query(sql: str, params: Tuple) -> List[Dict[str, Any]]:
    """Run a read query and return list[dict]. Returns [] on any DB error so a
    single failing panel can't 500 the whole page."""
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[tally-analytics] query failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    finally:
        return_connection(conn)


def _f(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Date range
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a YYYY-MM-DD query param. Returns None if absent/unparseable so a
    bad value can't 500 the page — the caller falls back to a sane default."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _resolve_range(
    range_str: str,
    start_param: Optional[str] = None,
    end_param: Optional[str] = None,
) -> Tuple[date, date]:
    """Resolve the (start, end) window for period figures.

    For range='custom' we honour the caller-supplied start/end (YYYY-MM-DD);
    this is what lets users view a historical FY whose data sits outside the
    current calendar year. If custom dates are missing/invalid we fall back to
    the trailing 90 days so the page still renders something sensible.
    """
    today = date.today()
    if range_str == "custom":
        start = _parse_date(start_param)
        end = _parse_date(end_param)
        if start and end:
            if start > end:
                start, end = end, start  # tolerate reversed inputs
            return start, end
        # Partial/invalid custom range → trailing 90 days.
        return (start or (end or today) - timedelta(days=90)), (end or today)
    if range_str == "year":
        return date(today.year, 1, 1), today
    if range_str == "quarter":
        q = (today.month - 1) // 3
        return date(today.year, q * 3 + 1, 1), today
    return date(today.year, today.month, 1), today  # month


def _twelve_months(end: date) -> List[Tuple[str, str]]:
    """[(YYYY-MM, label)] for the trailing 12 months ending in `end`'s month."""
    out: List[Tuple[str, str]] = []
    y, m = end.year, end.month - 11
    while m <= 0:
        m += 12
        y -= 1
    for _ in range(12):
        out.append((f"{y:04d}-{m:02d}", MONTH_LABELS[m - 1]))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Tenancy guard — there is no read without both filters.
# ─────────────────────────────────────────────────────────────────────────────

def _scope(uid: str, company: str) -> Tuple[str, str]:
    if not uid:
        raise HTTPException(status_code=401, detail="Missing user")
    if not company:
        raise HTTPException(status_code=400, detail="company is required")
    return uid, company


# ─────────────────────────────────────────────────────────────────────────────
# Analytics builders — every SQL is scoped by (user_id, company_name)
# ─────────────────────────────────────────────────────────────────────────────

def _monthly_pl(uid: str, company: str, months: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Monthly income vs expense for the trailing 12 months, from posted vouchers."""
    start_key = months[0][0]
    start = date(int(start_key[:4]), int(start_key[5:7]), 1)
    rows = _query(
        _GROUP_TREE_CTE + """
        SELECT to_char(v.date, 'YYYY-MM') AS ym,
               -- income groups are credited (positive); expense groups debited (negative)
               COALESCE(SUM(CASE WHEN l.parent IN (SELECT name FROM _grp WHERE root = ANY(%s)) THEN a.amount ELSE 0 END), 0)  AS income,
               COALESCE(SUM(CASE WHEN l.parent IN (SELECT name FROM _grp WHERE root = ANY(%s)) THEN -a.amount ELSE 0 END), 0) AS expense
        FROM trn_accounting a
        JOIN trn_voucher v
          ON v.guid = a.guid AND v.user_id = a.user_id AND v.company_name = a.company_name
        JOIN mst_ledger l
          ON l.name = a.ledger AND l.user_id = a.user_id AND l.company_name = a.company_name
        WHERE a.user_id = %s AND a.company_name = %s AND v.date >= %s
        GROUP BY 1
        """,
        (uid, company, list(INCOME_GROUPS + EXPENSE_GROUPS), uid, company,
         list(INCOME_GROUPS), list(EXPENSE_GROUPS), uid, company, start),
    )
    by_ym = {r["ym"]: r for r in rows}
    out = []
    for key, label in months:
        r = by_ym.get(key)
        out.append({
            "month": label,
            "income": round(_f(r["income"]) if r else 0.0, 2),
            "expenses": round(_f(r["expense"]) if r else 0.0, 2),
        })
    return out


def _period_pl(uid: str, company: str, start: date, end: date) -> Tuple[float, float]:
    rows = _query(
        _GROUP_TREE_CTE + """
        SELECT COALESCE(SUM(CASE WHEN l.parent IN (SELECT name FROM _grp WHERE root = ANY(%s)) THEN a.amount ELSE 0 END), 0)  AS income,
               COALESCE(SUM(CASE WHEN l.parent IN (SELECT name FROM _grp WHERE root = ANY(%s)) THEN -a.amount ELSE 0 END), 0) AS expense
        FROM trn_accounting a
        JOIN trn_voucher v
          ON v.guid = a.guid AND v.user_id = a.user_id AND v.company_name = a.company_name
        JOIN mst_ledger l
          ON l.name = a.ledger AND l.user_id = a.user_id AND l.company_name = a.company_name
        WHERE a.user_id = %s AND a.company_name = %s AND v.date >= %s AND v.date <= %s
        """,
        (uid, company, list(INCOME_GROUPS + EXPENSE_GROUPS), uid, company,
         list(INCOME_GROUPS), list(EXPENSE_GROUPS), uid, company, start, end),
    )
    if not rows:
        return 0.0, 0.0
    return round(_f(rows[0]["income"]), 2), round(_f(rows[0]["expense"]), 2)


def _expense_breakdown(uid: str, company: str, start: date, end: date, top: int = 6) -> List[Dict[str, Any]]:
    rows = _query(
        _GROUP_TREE_CTE + """
        SELECT l.parent AS name, COALESCE(SUM(-a.amount), 0) AS value
        FROM trn_accounting a
        JOIN trn_voucher v
          ON v.guid = a.guid AND v.user_id = a.user_id AND v.company_name = a.company_name
        JOIN mst_ledger l
          ON l.name = a.ledger AND l.user_id = a.user_id AND l.company_name = a.company_name
        WHERE a.user_id = %s AND a.company_name = %s
          AND v.date >= %s AND v.date <= %s
          AND l.parent IN (SELECT name FROM _grp)
        GROUP BY l.parent
        HAVING COALESCE(SUM(-a.amount), 0) > 0
        ORDER BY value DESC
        """,
        (uid, company, list(EXPENSE_GROUPS), uid, company,
         uid, company, start, end),
    )
    head = rows[:top]
    out = [{"name": r["name"] or "Uncategorised", "value": round(_f(r["value"]), 2),
            "category": r["name"] or "Uncategorised"} for r in head]
    other = sum(_f(r["value"]) for r in rows[top:])
    if other > 0:
        out.append({"name": "Other", "value": round(other, 2), "category": "Other"})
    return out


def _income_breakdown(uid: str, company: str, start: date, end: date, top: int = 4) -> List[Dict[str, Any]]:
    rows = _query(
        _GROUP_TREE_CTE + """
        SELECT l.parent AS name, COALESCE(SUM(a.amount), 0) AS value
        FROM trn_accounting a
        JOIN trn_voucher v
          ON v.guid = a.guid AND v.user_id = a.user_id AND v.company_name = a.company_name
        JOIN mst_ledger l
          ON l.name = a.ledger AND l.user_id = a.user_id AND l.company_name = a.company_name
        WHERE a.user_id = %s AND a.company_name = %s
          AND v.date >= %s AND v.date <= %s
          AND l.parent IN (SELECT name FROM _grp)
        GROUP BY l.parent
        HAVING COALESCE(SUM(a.amount), 0) > 0
        ORDER BY value DESC
        """,
        (uid, company, list(INCOME_GROUPS), uid, company,
         uid, company, start, end),
    )
    head = rows[:top]
    out = [{"name": r["name"] or "Sales", "value": round(_f(r["value"]), 2)} for r in head]
    other = sum(_f(r["value"]) for r in rows[top:])
    if other > 0:
        out.append({"name": "Other", "value": round(other, 2)})
    return out


def _top_parties(uid: str, company: str, groups: Tuple[str, ...], limit: int = 10) -> List[Dict[str, Any]]:
    """Top ledgers (debtors or creditors) by absolute closing balance. ABS keeps
    the ranking correct regardless of the stored sign convention."""
    rows = _query(
        _GROUP_TREE_CTE + """
        SELECT l.name AS name, l.closing_balance AS bal
        FROM mst_ledger l
        WHERE l.user_id = %s AND l.company_name = %s
          AND l.parent IN (SELECT name FROM _grp)
          AND COALESCE(l.closing_balance, 0) <> 0
        ORDER BY ABS(COALESCE(l.closing_balance, 0)) DESC
        LIMIT %s
        """,
        (uid, company, list(groups), uid, company,
         uid, company, limit),
    )
    items = [{"id": f"tally-{i}", "name": r["name"], "value": round(abs(_f(r["bal"])), 2)}
             for i, r in enumerate(rows)]
    total = sum(it["value"] for it in items) or 1
    for it in items:
        it["sharePct"] = round(it["value"] / total * 100, 1)
    return items


def _balance_for_groups(uid: str, company: str, groups: Tuple[str, ...], negate: bool) -> float:
    """Sum of closing balances for ledgers in the given primary groups.
    `negate` flips debit-natured figures (stored negative) to positive reals."""
    rows = _query(
        _GROUP_TREE_CTE + """
        SELECT COALESCE(SUM(l.closing_balance), 0) AS total
        FROM mst_ledger l
        WHERE l.user_id = %s AND l.company_name = %s
          AND l.parent IN (SELECT name FROM _grp)
        """,
        (uid, company, list(groups), uid, company, uid, company),
    )
    total = _f(rows[0]["total"]) if rows else 0.0
    return round(-total if negate else total, 2)


def _closing_stock(uid: str, company: str) -> float:
    rows = _query(
        """
        SELECT COALESCE(SUM(closing_value), 0) AS total
        FROM mst_stock_item
        WHERE user_id = %s AND company_name = %s
        """,
        (uid, company),
    )
    return round(abs(_f(rows[0]["total"])) if rows else 0.0, 2)


def _ageing(uid: str, company: str, groups: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """Receivables/payables ageing from bill-wise outstanding. A bill ref's
    outstanding = net of its New/Agst Ref rows; we age by the voucher date of
    the bill's entries. Buckets in days."""
    rows = _query(
        _GROUP_TREE_CTE + """
        SELECT b.name AS bill, b.ledger AS ledger,
               COALESCE(SUM(b.amount), 0) AS net,
               MIN(v.date) AS bill_date
        FROM trn_bill b
        JOIN trn_voucher v
          ON v.guid = b.guid AND v.user_id = b.user_id AND v.company_name = b.company_name
        JOIN mst_ledger l
          ON l.name = b.ledger AND l.user_id = b.user_id AND l.company_name = b.company_name
        WHERE b.user_id = %s AND b.company_name = %s
          AND l.parent IN (SELECT name FROM _grp)
        GROUP BY b.name, b.ledger
        HAVING ROUND(COALESCE(SUM(b.amount), 0)::numeric, 2) <> 0
        """,
        (uid, company, list(groups), uid, company, uid, company),
    )
    buckets = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    today = date.today()
    for r in rows:
        amt = abs(_f(r["net"]))
        bd = r["bill_date"]
        if isinstance(bd, datetime):
            bd = bd.date()
        age = (today - bd).days if isinstance(bd, date) else 0
        if age <= 30:
            buckets["0-30"] += amt
        elif age <= 60:
            buckets["31-60"] += amt
        elif age <= 90:
            buckets["61-90"] += amt
        else:
            buckets["90+"] += amt
    return [{"bucket": k, "value": round(v, 2)} for k, v in buckets.items()]


def _cash_position(uid: str, company: str, months: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Trailing-12-month closing cash+bank position. Computed as opening balance
    of cash/bank ledgers + cumulative net movement up to each month end."""
    opening_rows = _query(
        _GROUP_TREE_CTE + """
        SELECT COALESCE(SUM(l.opening_balance), 0) AS opening
        FROM mst_ledger l
        WHERE l.user_id = %s AND l.company_name = %s
          AND l.parent IN (SELECT name FROM _grp)
        """,
        (uid, company, list(BANK_GROUPS + CASH_GROUPS), uid, company, uid, company),
    )
    # debit-natured: stored negative → negate to a positive cash figure
    opening = -_f(opening_rows[0]["opening"]) if opening_rows else 0.0

    start_key = months[0][0]
    start = date(int(start_key[:4]), int(start_key[5:7]), 1)
    move_rows = _query(
        _GROUP_TREE_CTE + """
        SELECT to_char(v.date, 'YYYY-MM') AS ym, COALESCE(SUM(-a.amount), 0) AS net
        FROM trn_accounting a
        JOIN trn_voucher v
          ON v.guid = a.guid AND v.user_id = a.user_id AND v.company_name = a.company_name
        JOIN mst_ledger l
          ON l.name = a.ledger AND l.user_id = a.user_id AND l.company_name = a.company_name
        WHERE a.user_id = %s AND a.company_name = %s
          AND l.parent IN (SELECT name FROM _grp)
        GROUP BY 1
        """,
        (uid, company, list(BANK_GROUPS + CASH_GROUPS), uid, company, uid, company),
    )
    moves = {r["ym"]: _f(r["net"]) for r in move_rows}
    # Net movement before the window so the first month starts from the right base.
    prior = sum(v for k, v in moves.items() if k < start_key)
    running = opening + prior
    out = []
    for key, label in months:
        running += moves.get(key, 0.0)
        out.append({"month": label, "value": round(running, 2)})
    return out


def _voucher_activity(uid: str, company: str, start: date, end: date) -> List[Dict[str, Any]]:
    rows = _query(
        """
        SELECT voucher_type AS name, COUNT(*) AS count
        FROM trn_voucher
        WHERE user_id = %s AND company_name = %s AND date >= %s AND date <= %s
        GROUP BY voucher_type
        ORDER BY count DESC
        LIMIT 8
        """,
        (uid, company, start, end),
    )
    return [{"name": r["name"], "value": int(r["count"])} for r in rows]


def _gst_liability(uid: str, company: str) -> Dict[str, Any]:
    """Net GST position from Duties & Taxes ledger balances. This is a
    ledger-derived snapshot, NOT a filed GSTR figure — labelled as such in the UI."""
    rows = _query(
        _GROUP_TREE_CTE + """
        SELECT l.name AS name, l.closing_balance AS bal
        FROM mst_ledger l
        WHERE l.user_id = %s AND l.company_name = %s
          AND l.parent IN (SELECT name FROM _grp)
        """,
        (uid, company, list(TAX_GROUPS), uid, company, uid, company),
    )
    # Credit balance (positive) = output tax payable; debit (negative) = input credit.
    output_tax = sum(_f(r["bal"]) for r in rows if _f(r["bal"]) > 0)
    input_credit = sum(-_f(r["bal"]) for r in rows if _f(r["bal"]) < 0)
    return {
        "output": round(output_tax, 2),
        "input": round(input_credit, 2),
        "net": round(output_tax - input_credit, 2),
        "derivedFrom": "ledger-balances",  # not a filed GSTR
    }


def _data_date_range(uid: str, company: str) -> Dict[str, Any]:
    """Earliest/latest voucher date + total voucher count for this (user, company).
    The frontend uses this to (a) tell a 'zero' window from genuinely-no-data, and
    (b) offer a one-click 'jump to my data' custom range for historical companies
    whose books sit outside the current calendar year."""
    rows = _query(
        """
        SELECT MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS voucher_count
        FROM trn_voucher
        WHERE user_id = %s AND company_name = %s
        """,
        (uid, company),
    )
    if not rows:
        return {"start": None, "end": None, "voucherCount": 0}
    r = rows[0]
    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    return {
        "start": _iso(r["min_date"]),
        "end": _iso(r["max_date"]),
        "voucherCount": int(r["voucher_count"] or 0),
    }


def _kpi(kid: str, label: str, value: float, kind: str = "neutral") -> Dict[str, Any]:
    # kind: credit | debit | neutral — drives colour semantics on the frontend.
    return {"id": kid, "label": label, "value": round(value, 2), "kind": kind}


# ─────────────────────────────────────────────────────────────────────────────
# Sync-state access
# ─────────────────────────────────────────────────────────────────────────────

def _sync_state(uid: str, company: str) -> Dict[str, Any]:
    rows = _query(
        """
        SELECT status, sync_mode, last_alterid_master, last_alterid_transaction,
               last_synced_at, last_full_reconcile_at, sync_started_at,
               last_error, row_counts
        FROM tally_sync_state
        WHERE user_id = %s AND company_name = %s
        """,
        (uid, company),
    )
    if not rows:
        return {"status": "idle", "lastSyncedAt": None, "lastError": None,
                "lastFullReconcileAt": None, "syncMode": None, "rowCounts": {}}
    r = rows[0]
    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    return {
        "status": r["status"],
        "syncMode": r["sync_mode"],
        "lastSyncedAt": _iso(r["last_synced_at"]),
        "lastFullReconcileAt": _iso(r["last_full_reconcile_at"]),
        "syncStartedAt": _iso(r["sync_started_at"]),
        "lastError": r["last_error"],
        "rowCounts": r["row_counts"] or {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/companies")
def list_companies(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Companies this user can view analytics for — the union of companies that
    have a sync-state row and companies the bridge has cached. Scoped to the uid."""
    user = _verify(credentials)
    uid = user["uid"]
    rows = _query(
        """
        SELECT c.company_name,
               s.status, s.last_synced_at, s.last_error,
               s.last_full_reconcile_at
        FROM (
            SELECT company_name FROM tally_cached_companies WHERE user_id = %s
            UNION
            SELECT company_name FROM tally_sync_state WHERE user_id = %s
        ) c
        LEFT JOIN tally_sync_state s
          ON s.user_id = %s AND s.company_name = c.company_name
        ORDER BY s.last_synced_at DESC NULLS LAST, c.company_name
        """,
        (uid, uid, uid),
    )
    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    return {
        "companies": [
            {
                "company": r["company_name"],
                "status": r["status"] or "idle",
                "lastSyncedAt": _iso(r["last_synced_at"]),
                "lastFullReconcileAt": _iso(r["last_full_reconcile_at"]),
                "lastError": r["last_error"],
            }
            for r in rows
        ]
    }


@router.get("/sync-status")
def sync_status(
    company: str = Query(..., description="Tally company name"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    uid, company = _scope(user["uid"], company)
    return _sync_state(uid, company)


@router.get("/summary")
def summary(
    company: str = Query(..., description="Tally company name"),
    range: str = Query("month", description="month | quarter | year | custom"),
    start: Optional[str] = Query(None, description="custom range start, YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="custom range end, YYYY-MM-DD"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Full analytics payload for one (user, company) — read entirely from our DB.

    For range='custom', `start`/`end` (YYYY-MM-DD) scope the period figures
    (P&L, breakdowns, voucher activity). Balance-based KPIs (receivables, payables,
    cash/bank, stock, GST) are always the latest closing position and ignore the
    window — a point-in-time balance has no date range.
    """
    user = _verify(credentials)
    uid, company = _scope(user["uid"], company)

    start, end = _resolve_range(range, start, end)
    months = _twelve_months(end)

    income, expense = _period_pl(uid, company, start, end)
    net = round(income - expense, 2)
    receivables = _balance_for_groups(uid, company, DEBTOR_GROUPS, negate=True)
    payables = _balance_for_groups(uid, company, CREDITOR_GROUPS, negate=False)
    bank = _balance_for_groups(uid, company, BANK_GROUPS + CASH_GROUPS, negate=True)
    stock = _closing_stock(uid, company)
    gst = _gst_liability(uid, company)

    payload = {
        "company": company,
        "range": range,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "dataRange": _data_date_range(uid, company),
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "kpis": [
            _kpi("income", "Total Income", income, "credit"),
            _kpi("expenses", "Total Expenses", expense, "debit"),
            _kpi("net", "Net (Income − Expense)", net, "credit" if net >= 0 else "debit"),
            _kpi("bank", "Cash & Bank", bank, "credit" if bank >= 0 else "debit"),
            _kpi("receivables", "Receivables", receivables, "debit"),
            _kpi("payables", "Payables", payables, "credit"),
            _kpi("stock", "Closing Stock", stock, "neutral"),
            _kpi("gst", "GST Payable (net)", gst["net"], "credit" if gst["net"] >= 0 else "debit"),
        ],
        "monthly": _monthly_pl(uid, company, months),
        "cashFlow": _cash_position(uid, company, months),
        "expenseCategories": _expense_breakdown(uid, company, start, end),
        "incomeBreakdown": _income_breakdown(uid, company, start, end),
        "topCustomers": _top_parties(uid, company, DEBTOR_GROUPS),
        "topVendors": _top_parties(uid, company, CREDITOR_GROUPS),
        "ageing": {
            "receivable": _ageing(uid, company, DEBTOR_GROUPS),
            "payable": _ageing(uid, company, CREDITOR_GROUPS),
        },
        "voucherActivity": _voucher_activity(uid, company, start, end),
        "gst": gst,
        "sync": _sync_state(uid, company),
    }
    return payload


@router.post("/sync")
async def trigger_sync(
    company: str = Query(..., description="Tally company name"),
    mode: str = Query("incremental", description="incremental | full"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Ask the user's desktop bridge to run a sync for one company. Returns
    immediately with an ack — the run itself is tracked via /sync-status. We
    never block the request on a (possibly multi-minute) sync."""
    user = _verify(credentials)
    uid, company = _scope(user["uid"], company)
    mode = mode if mode in ("incremental", "full") else "incremental"
    try:
        ack = await ws_manager.trigger_tally_sync(uid, company, mode)
        return {"requested": True, "company": company, "mode": mode, "ack": ack}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[tally-analytics] sync trigger failed: {exc}")
        raise HTTPException(status_code=502, detail="Could not reach the desktop bridge")
