"""
SAP S/4HANA company brief — build, cache, and load a compact markdown snapshot
of a user's SAP company code so the chat agent starts each conversation
already knowing the chart of accounts (with period balances), customers, and
vendors.

Mirrors zoho_brief / xero_brief, but consumes the normalized cards that
sap_connector returns (list_id / name / account_type) rather than raw QBO
objects, and keys the cache by `company_code`.

SAP differences vs Zoho/Xero:
  * There is no `get_company_info`; the company name is the company code itself,
    surfaced via `list_companies`.
  * Account / business-partner cards carry no `balance`. GL-account period
    balances are pulled separately from `get_trial_balance` (rows keyed by
    GLAccount with EndingBalanceAmtInCoCodeCrcy) and joined onto the accounts.
  * `list_items` returns [] (SAP FI posts against GL accounts, not item masters),
    so the items section is omitted.
  * Customers / vendors are listed by name only (no outstanding-balance figure
    is available from the master read).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.sap_connector import sap_connector

logger = logging.getLogger(__name__)

STALE_SECONDS = 24 * 60 * 60  # 24 hours


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


def _tb_balance_index(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Map GLAccount -> ending balance (in company-code currency) from a trial
    balance result set. Falls back across the common SAP balance field names."""
    out: Dict[str, float] = {}
    for r in rows:
        gl = r.get("GLAccount")
        if not gl:
            continue
        bal = (r.get("EndingBalanceAmtInCoCodeCrcy")
               or r.get("EndingBalanceAmount")
               or r.get("BalanceAmtInCoCodeCrcy")
               or 0)
        try:
            out[str(gl)] = float(bal)
        except (ValueError, TypeError):
            out[str(gl)] = 0.0
    return out


async def build_sap_brief(user_id: str, company_code: str) -> str:
    """Fetch summary data from SAP and assemble a markdown brief."""

    # SAP has no company-info endpoint; the company name is the company code,
    # optionally enriched via list_companies.
    company_name = company_code or "Unknown"
    try:
        companies = await sap_connector.list_companies(user_id)
        match = next((c for c in companies if c.get("company_code") == company_code), None)
        if match and match.get("name"):
            company_name = match["name"]
    except Exception as exc:
        logger.warning(f"sap_brief: companies fetch failed: {exc}")

    accounts: List[Dict] = []
    customers: List[Dict] = []
    vendors: List[Dict] = []
    balances: Dict[str, float] = {}

    try:
        accounts = await sap_connector.list_accounts(user_id, company_code)
    except Exception as exc:
        logger.warning(f"sap_brief: accounts fetch failed: {exc}")
    try:
        customers = await sap_connector.list_customers(user_id, company_code)
    except Exception as exc:
        logger.warning(f"sap_brief: customers fetch failed: {exc}")
    try:
        vendors = await sap_connector.list_vendors(user_id, company_code)
    except Exception as exc:
        logger.warning(f"sap_brief: vendors fetch failed: {exc}")

    # GL-account balances come from the trial balance, not the COA cards. Pull a
    # year-to-date window so the brief reflects current period activity.
    try:
        today = date.today()
        from_date = today.replace(month=1, day=1).isoformat()
        to_date = today.isoformat()
        tb_rows = await sap_connector.get_trial_balance(user_id, company_code, from_date, to_date)
        balances = _tb_balance_index(tb_rows)
    except Exception as exc:
        logger.warning(f"sap_brief: trial balance fetch failed: {exc}")

    # Join balances onto the account cards (no `balance` field on SAP cards).
    for a in accounts:
        a["balance"] = balances.get(str(a.get("list_id")), 0.0)

    # ---- Assemble markdown ----
    lines: List[str] = [f"## Company context — {company_name}"]
    lines.append(f"Company code: {company_code}.")

    # Chart of accounts grouped by account_type
    lines.append("")
    lines.append("### Chart of Accounts by type")
    type_buckets: Dict[str, List[Dict]] = {}
    for a in accounts:
        atype = a.get("account_type") or "other"
        type_buckets.setdefault(atype, []).append(a)
    for atype in sorted(type_buckets):
        bucket = type_buckets[atype]
        lines.append(f"- **{atype}** ({len(bucket)} accounts)")
        for acc in _top_by(bucket, "balance", 5):
            name = acc.get("name", "?")
            bal = _fmt_money(acc.get("balance"))
            lines.append(f"  - {name} → {bal}")

    # Customers (master list; no outstanding-balance figure available)
    lines.append("")
    lines.append(f"### Customers ({len(customers)} total)")
    if customers:
        for c in customers[:15]:
            lines.append(f"- {c.get('name') or '?'}")
    else:
        lines.append("- (none)")

    # Vendors (master list; no outstanding-balance figure available)
    lines.append("")
    lines.append(f"### Vendors ({len(vendors)} total)")
    if vendors:
        for v in vendors[:15]:
            lines.append(f"- {v.get('name') or '?'}")
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append(
        "Use this brief to pick the correct account / customer / vendor names "
        "before querying. When unsure of exact spelling, search with a partial "
        "name match."
    )

    return "\n".join(lines)


# -----------------------------------------------------------------------
# Persistence (sap_company_profile, created in migration 022)
# -----------------------------------------------------------------------

def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sap_company_profile (
                user_id      TEXT NOT NULL,
                company_code TEXT NOT NULL,
                brief_md     TEXT NOT NULL,
                refreshed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, company_code)
            )
        """)
        conn.commit()


def save_brief(user_id: str, company_code: str, brief_md: str) -> None:
    conn = get_connection()
    if conn is None:
        logger.error("sap_brief save skipped — DB unavailable")
        return
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sap_company_profile (user_id, company_code, brief_md, refreshed_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, company_code) DO UPDATE
                  SET brief_md = EXCLUDED.brief_md,
                      refreshed_at = CURRENT_TIMESTAMP
                """,
                (user_id, company_code, brief_md),
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


def load_brief(user_id: str, company_code: str) -> Optional[str]:
    conn = get_connection()
    if conn is None:
        return None
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT brief_md FROM sap_company_profile WHERE user_id = %s AND company_code = %s",
                (user_id, company_code),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as exc:
        logger.warning(f"load_brief failed: {exc}")
        return None
    finally:
        return_connection(conn)


def is_stale(user_id: str, company_code: str) -> bool:
    conn = get_connection()
    if conn is None:
        return True
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT refreshed_at FROM sap_company_profile WHERE user_id = %s AND company_code = %s",
                (user_id, company_code),
            )
            row = cur.fetchone()
            if not row:
                return True
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            return (now - row[0]).total_seconds() > STALE_SECONDS
    except Exception:
        return True
    finally:
        return_connection(conn)


async def refresh_brief(user_id: str, company_code: str) -> str:
    brief = await build_sap_brief(user_id, company_code)
    save_brief(user_id, company_code, brief)
    return brief


async def get_or_refresh_brief(user_id: str, company_code: str) -> Optional[str]:
    if not is_stale(user_id, company_code):
        cached = load_brief(user_id, company_code)
        if cached:
            return cached
    try:
        return await refresh_brief(user_id, company_code)
    except Exception as exc:
        logger.warning(f"get_or_refresh_brief failed, falling back to cache: {exc}")
        return load_brief(user_id, company_code)
