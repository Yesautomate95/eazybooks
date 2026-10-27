"""
QBO company brief — build, cache, and load a compact markdown snapshot
of a user's QuickBooks Online data so the chat agent starts each
conversation already knowing the chart of accounts, top customers /
vendors, item catalogue shape, and transaction volume.

Mirrors `tally_db_sync._build_company_brief` but pulls from the QBO
REST API (via qbo_connector) instead of a local Postgres mirror.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.qbo_connector import qbo_connector

logger = logging.getLogger(__name__)

STALE_SECONDS = 24 * 60 * 60  # 24 hours
BRIEF_SCHEMA = "qbo-brief-v2-full-directory"


# -----------------------------------------------------------------------
# Build the brief from live QBO data
# -----------------------------------------------------------------------

def _top_by(items: List[Dict[str, Any]], key: str, n: int = 15) -> List[Dict[str, Any]]:
    def _val(item):
        try:
            return abs(float(item.get(key) or 0))
        except (ValueError, TypeError):
            return 0.0
    return sorted(items, key=_val, reverse=True)[:n]


def _fmt_money(val: Any) -> str:
    try:
        return f"{float(val):,.2f}"
    except (ValueError, TypeError):
        return "0.00"


def _fmt_bool(val: Any) -> str:
    if val is True:
        return "active"
    if val is False:
        return "inactive"
    return "active?"


def _ref_id(item: Dict[str, Any]) -> str:
    return str(item.get("Id") or item.get("id") or "?")


def _display_name(item: Dict[str, Any]) -> str:
    return (
        item.get("FullyQualifiedName")
        or item.get("DisplayName")
        or item.get("CompanyName")
        or item.get("Name")
        or "?"
    )


def _email(item: Dict[str, Any]) -> Optional[str]:
    addr = item.get("PrimaryEmailAddr") or {}
    if isinstance(addr, dict):
        return addr.get("Address")
    return None


async def build_qbo_brief(user_id: str, realm_id: Optional[str]) -> str:
    """Fetch summary data from QBO and assemble a markdown brief."""

    company_name = "Unknown"
    company_info: Dict[str, Any] = {}
    try:
        info = await qbo_connector.get_company_info(user_id, realm_id)
        company_info = info or {}
        company_name = info.get("CompanyName") or info.get("LegalName") or "Unknown"
        fy_start = info.get("FiscalYearStartMonth")
    except Exception:
        fy_start = None

    accounts: List[Dict] = []
    customers: List[Dict] = []
    vendors: List[Dict] = []
    items: List[Dict] = []

    try:
        accounts = await qbo_connector.list_accounts(user_id, realm_id)
    except Exception as exc:
        logger.warning(f"qbo_brief: accounts fetch failed: {exc}")

    try:
        customers = await qbo_connector.list_customers(user_id, realm_id)
    except Exception as exc:
        logger.warning(f"qbo_brief: customers fetch failed: {exc}")

    try:
        vendors = await qbo_connector.list_vendors(user_id, realm_id)
    except Exception as exc:
        logger.warning(f"qbo_brief: vendors fetch failed: {exc}")

    try:
        items = await qbo_connector.list_items(user_id, realm_id)
    except Exception as exc:
        logger.warning(f"qbo_brief: items fetch failed: {exc}")

    # Transaction counts (cheap COUNT queries)
    txn_counts: Dict[str, int] = {}
    for entity in ("Invoice", "Bill", "Payment", "BillPayment", "Purchase", "Deposit", "JournalEntry"):
        try:
            resp = await qbo_connector.query(user_id, f"SELECT COUNT(*) FROM {entity}", realm_id)
            txn_counts[entity] = resp.get("QueryResponse", {}).get("totalCount", 0)
        except Exception:
            txn_counts[entity] = -1  # signal "query failed"

    # ---- Assemble markdown ----
    lines: List[str] = [f"## Company context — {company_name}"]
    lines.append(f"Brief schema: {BRIEF_SCHEMA}.")

    if realm_id:
        lines.append(f"Realm/company id: `{realm_id}`.")
    if fy_start:
        lines.append(f"Fiscal year starts month {fy_start}.")
    if company_info:
        legal_name = company_info.get("LegalName")
        country = company_info.get("Country")
        currency_ref = company_info.get("CurrencyRef") or {}
        base_currency = None
        if isinstance(currency_ref, dict):
            base_currency = currency_ref.get("name") or currency_ref.get("value")
        if legal_name and legal_name != company_name:
            lines.append(f"Legal name: {legal_name}.")
        if country:
            lines.append(f"Country: {country}.")
        if base_currency:
            lines.append(f"Base currency: {base_currency}.")

    lines.append("")
    lines.append("### How to use this company directory")
    lines.append("- This snapshot is for choosing the right QBO entity, exact names, and IDs.")
    lines.append("- Prefer direct qbo_query/qbo_report calls using these names/IDs; do not call list_qbo_entities just to rediscover available entities.")
    lines.append("- Use reports for totals and accounting statements; use qbo_query for detail rows and entity lookups.")
    lines.append("- The snapshot can be up to 24 hours old, so verify live amounts, dates, and open balances with tools before answering.")

    # Chart of accounts grouped by AccountType. Include every fetched account
    # so the agent can resolve exact account names/ids without exploratory calls.
    lines.append("")
    lines.append(f"### Full Chart of Accounts ({len(accounts)} accounts)")
    type_buckets: Dict[str, List[Dict]] = {}
    for a in accounts:
        atype = a.get("AccountType", "Other")
        type_buckets.setdefault(atype, []).append(a)
    for atype in sorted(type_buckets):
        bucket = sorted(type_buckets[atype], key=lambda x: _display_name(x).lower())
        lines.append(f"- **{atype}** ({len(bucket)} accounts)")
        for acc in bucket:
            name = _display_name(acc)
            bal = _fmt_money(acc.get("CurrentBalance"))
            sub = acc.get("AccountSubType", "")
            active = _fmt_bool(acc.get("Active"))
            lines.append(f"  - id={_ref_id(acc)} | {name} | subtype={sub} | balance={bal} | {active}")

    # Top customers by AR balance
    lines.append("")
    lines.append(f"### Customer directory ({len(customers)} customers)")
    top_cust = _top_by(customers, "Balance", 10)
    if top_cust:
        lines.append("Top AR balances:")
        for c in top_cust:
            name = _display_name(c)
            bal = _fmt_money(c.get("Balance"))
            if float(c.get("Balance") or 0) != 0:
                lines.append(f"- id={_ref_id(c)} | {name} | balance={bal}")
    if customers:
        lines.append("All customers:")
        for c in sorted(customers, key=lambda x: _display_name(x).lower()):
            name = _display_name(c)
            bal = _fmt_money(c.get("Balance"))
            email = _email(c)
            company = c.get("CompanyName")
            extra = []
            if company and company != name:
                extra.append(f"company={company}")
            if email:
                extra.append(f"email={email}")
            extra.append(f"balance={bal}")
            extra.append(_fmt_bool(c.get("Active")))
            lines.append(f"- id={_ref_id(c)} | {name} | " + " | ".join(extra))
    else:
        lines.append("- (none)")

    # Top vendors by AP balance
    lines.append("")
    lines.append(f"### Vendor directory ({len(vendors)} vendors)")
    top_vend = _top_by(vendors, "Balance", 10)
    if top_vend:
        lines.append("Top AP balances:")
        for v in top_vend:
            name = _display_name(v)
            bal = _fmt_money(v.get("Balance"))
            if float(v.get("Balance") or 0) != 0:
                lines.append(f"- id={_ref_id(v)} | {name} | balance={bal}")
    if vendors:
        lines.append("All vendors:")
        for v in sorted(vendors, key=lambda x: _display_name(x).lower()):
            name = _display_name(v)
            bal = _fmt_money(v.get("Balance"))
            email = _email(v)
            company = v.get("CompanyName")
            extra = []
            if company and company != name:
                extra.append(f"company={company}")
            if email:
                extra.append(f"email={email}")
            extra.append(f"balance={bal}")
            extra.append(_fmt_bool(v.get("Active")))
            lines.append(f"- id={_ref_id(v)} | {name} | " + " | ".join(extra))
    else:
        lines.append("- (none)")

    # Items
    lines.append("")
    lines.append(f"### Item / Service directory ({len(items)} items)")
    if items:
        for it in sorted(items, key=lambda x: _display_name(x).lower()):
            name = _display_name(it)
            itype = it.get("Type", "")
            price = _fmt_money(it.get("UnitPrice"))
            income_ref = it.get("IncomeAccountRef") or {}
            expense_ref = it.get("ExpenseAccountRef") or {}
            refs = []
            if income_ref:
                refs.append(f"income={income_ref.get('name') or income_ref.get('value')}")
            if expense_ref:
                refs.append(f"expense={expense_ref.get('name') or expense_ref.get('value')}")
            refs.append(_fmt_bool(it.get("Active")))
            lines.append(f"- id={_ref_id(it)} | {name} | type={itype} | unit_price={price} | " + " | ".join(refs))
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Quick query hints")
    lines.append("- Customer/vendor-specific detail: query Invoice/Bill/Payment/Purchase using exact names from the directory, usually with LIKE if QBO filtering requires it.")
    lines.append("- Account-specific detail: use Account ids/names above, then qbo_report('GeneralLedger') or qbo_report('TransactionList') with the account/date params when applicable.")
    lines.append("- P&L, Balance Sheet, Trial Balance, AR/AP Aging, Cash Flow, Customer Sales, and Vendor Expenses should be fetched with qbo_report, not reconstructed manually.")
    lines.append("- For names with punctuation or uncertain casing, use LIKE '%partial name%' and MAXRESULTS to keep results small.")

    # Legacy short item preview kept for readability when the directory is huge.
    lines.append("")
    lines.append("### Item / Service preview")
    for it in items[:15]:
        name = _display_name(it)
        itype = it.get("Type", "")
        price = _fmt_money(it.get("UnitPrice"))
        lines.append(f"- {name} [{itype}] unit price {price}")

    # Transaction volumes
    lines.append("")
    lines.append("### Transaction volumes")
    for entity, count in txn_counts.items():
        if count < 0:
            lines.append(f"- {entity}: (query failed)")
        else:
            lines.append(f"- {entity}: {count:,}")

    lines.append("")
    lines.append(
        "Use this brief to pick correct account / customer / vendor names "
        "before writing queries. Names are case-sensitive in QBO SQL — use "
        "LIKE '%partial%' when unsure of exact spelling."
    )

    return "\n".join(lines)


# -----------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------

def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS qbo_company_profile (
                user_id       TEXT NOT NULL,
                realm_id      TEXT NOT NULL,
                brief_md      TEXT NOT NULL,
                refreshed_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, realm_id)
            )
        """)
        conn.commit()


def save_brief(user_id: str, realm_id: str, brief_md: str) -> None:
    conn = get_connection()
    if conn is None:
        logger.error("qbo_brief save skipped — DB unavailable")
        return
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qbo_company_profile (user_id, realm_id, brief_md, refreshed_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, realm_id) DO UPDATE
                  SET brief_md = EXCLUDED.brief_md,
                      refreshed_at = CURRENT_TIMESTAMP
                """,
                (user_id, realm_id, brief_md),
            )
            conn.commit()
    except Exception as exc:
        logger.warning(f"save_brief failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        return_connection(conn)


def load_brief(user_id: str, realm_id: str) -> Optional[str]:
    conn = get_connection()
    if conn is None:
        return None
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT brief_md FROM qbo_company_profile WHERE user_id = %s AND realm_id = %s",
                (user_id, realm_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as exc:
        logger.warning(f"load_brief failed: {exc}")
        return None
    finally:
        return_connection(conn)


def is_stale(user_id: str, realm_id: str) -> bool:
    conn = get_connection()
    if conn is None:
        return True
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT refreshed_at FROM qbo_company_profile WHERE user_id = %s AND realm_id = %s",
                (user_id, realm_id),
            )
            row = cur.fetchone()
            if not row:
                return True
            refreshed = row[0]
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            return (now - refreshed).total_seconds() > STALE_SECONDS
    except Exception:
        return True
    finally:
        return_connection(conn)


async def refresh_brief(user_id: str, realm_id: str) -> str:
    """Build a fresh brief from QBO and persist it."""
    brief = await build_qbo_brief(user_id, realm_id)
    save_brief(user_id, realm_id, brief)
    return brief


async def get_or_refresh_brief(user_id: str, realm_id: str) -> Optional[str]:
    """Return cached brief if fresh enough, otherwise rebuild."""
    if not is_stale(user_id, realm_id):
        cached = load_brief(user_id, realm_id)
        if cached and BRIEF_SCHEMA in cached:
            return cached
    try:
        return await refresh_brief(user_id, realm_id)
    except Exception as exc:
        logger.warning(f"get_or_refresh_brief failed, falling back to cache: {exc}")
        return load_brief(user_id, realm_id)
