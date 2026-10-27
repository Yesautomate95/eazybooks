"""
Xero company brief — build, cache, and load a compact markdown snapshot
of a user's Xero organisation so the chat agent starts each conversation
already knowing the chart of accounts, top customers / vendors, and item
catalogue shape.

Mirrors zoho_brief, but consumes the normalized cards that xero_connector
returns (list_id / name / account_type / balance) rather than raw QBO objects,
and keys the cache by `tenant_id`. Note that account_type is the Xero `Type`
enum (e.g. BANK, REVENUE, EXPENSE).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.xero_connector import xero_connector

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


async def build_xero_brief(user_id: str, tenant_id: str) -> str:
    """Fetch summary data from Xero and assemble a markdown brief."""

    company_name = "Unknown"
    currency = ""
    try:
        # get_company_info returns a Xero Organisation dict.
        info = await xero_connector.get_company_info(user_id, tenant_id)
        company_name = info.get("Name") or "Unknown"
        currency = info.get("BaseCurrency") or ""
    except Exception:
        pass

    accounts: List[Dict] = []
    customers: List[Dict] = []
    vendors: List[Dict] = []
    items: List[Dict] = []

    try:
        accounts = await xero_connector.list_accounts(user_id, tenant_id)
    except Exception as exc:
        logger.warning(f"xero_brief: accounts fetch failed: {exc}")
    try:
        customers = await xero_connector.list_customers(user_id, tenant_id)
    except Exception as exc:
        logger.warning(f"xero_brief: customers fetch failed: {exc}")
    try:
        vendors = await xero_connector.list_vendors(user_id, tenant_id)
    except Exception as exc:
        logger.warning(f"xero_brief: vendors fetch failed: {exc}")
    try:
        items = await xero_connector.list_items(user_id, tenant_id)
    except Exception as exc:
        logger.warning(f"xero_brief: items fetch failed: {exc}")

    # ---- Assemble markdown ----
    lines: List[str] = [f"## Company context — {company_name}"]
    if currency:
        lines.append(f"Base currency: {currency}.")

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

    # Top customers by balance
    lines.append("")
    lines.append(f"### Customers ({len(customers)} total)")
    top_cust = [c for c in _top_by(customers, "balance", 10) if float(c.get("balance") or 0) != 0]
    if top_cust:
        for c in top_cust:
            lines.append(f"- {c.get('name') or '?'} → balance {_fmt_money(c.get('balance'))}")
    else:
        lines.append("- (none with an outstanding balance)")

    # Top vendors by balance
    lines.append("")
    lines.append(f"### Vendors ({len(vendors)} total)")
    top_vend = [v for v in _top_by(vendors, "balance", 10) if float(v.get("balance") or 0) != 0]
    if top_vend:
        for v in top_vend:
            lines.append(f"- {v.get('name') or '?'} → balance {_fmt_money(v.get('balance'))}")
    else:
        lines.append("- (none with an outstanding balance)")

    # Items
    lines.append("")
    lines.append(f"### Items / Services ({len(items)} total)")
    for it in items[:15]:
        lines.append(f"- {it.get('name', '?')} unit price {_fmt_money(it.get('rate'))}")

    lines.append("")
    lines.append(
        "Use this brief to pick the correct account / customer / vendor names "
        "before querying. When unsure of exact spelling, search with a partial "
        "name match."
    )

    return "\n".join(lines)


# -----------------------------------------------------------------------
# Persistence (xero_company_profile, created in migration 015)
# -----------------------------------------------------------------------

def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xero_company_profile (
                user_id      TEXT NOT NULL,
                tenant_id    TEXT NOT NULL,
                brief_md     TEXT NOT NULL,
                refreshed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, tenant_id)
            )
        """)
        conn.commit()


def save_brief(user_id: str, tenant_id: str, brief_md: str) -> None:
    conn = get_connection()
    if conn is None:
        logger.error("xero_brief save skipped — DB unavailable")
        return
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO xero_company_profile (user_id, tenant_id, brief_md, refreshed_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, tenant_id) DO UPDATE
                  SET brief_md = EXCLUDED.brief_md,
                      refreshed_at = CURRENT_TIMESTAMP
                """,
                (user_id, tenant_id, brief_md),
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


def load_brief(user_id: str, tenant_id: str) -> Optional[str]:
    conn = get_connection()
    if conn is None:
        return None
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT brief_md FROM xero_company_profile WHERE user_id = %s AND tenant_id = %s",
                (user_id, tenant_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as exc:
        logger.warning(f"load_brief failed: {exc}")
        return None
    finally:
        return_connection(conn)


def is_stale(user_id: str, tenant_id: str) -> bool:
    conn = get_connection()
    if conn is None:
        return True
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT refreshed_at FROM xero_company_profile WHERE user_id = %s AND tenant_id = %s",
                (user_id, tenant_id),
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


async def refresh_brief(user_id: str, tenant_id: str) -> str:
    brief = await build_xero_brief(user_id, tenant_id)
    save_brief(user_id, tenant_id, brief)
    return brief


async def get_or_refresh_brief(user_id: str, tenant_id: str) -> Optional[str]:
    if not is_stale(user_id, tenant_id):
        cached = load_brief(user_id, tenant_id)
        if cached:
            return cached
    try:
        return await refresh_brief(user_id, tenant_id)
    except Exception as exc:
        logger.warning(f"get_or_refresh_brief failed, falling back to cache: {exc}")
        return load_brief(user_id, tenant_id)
