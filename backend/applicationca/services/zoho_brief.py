"""
Zoho Books company brief — build, cache, and load a compact markdown snapshot
of a user's Zoho organization so the chat agent starts each conversation
already knowing the chart of accounts, top customers / vendors, and item
catalogue shape.

Mirrors qbo_brief, but consumes the normalized cards that zoho_connector
returns (list_id / name / account_type / balance) rather than raw QBO objects,
and keys the cache by `organization_id`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.zoho_connector import zoho_connector

logger = logging.getLogger(__name__)

STALE_SECONDS = 24 * 60 * 60  # 24 hours
BRIEF_SCHEMA = "zoho-brief-v2-full-directory"


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


def _ref_id(item: Dict[str, Any]) -> str:
    return str(item.get("list_id") or item.get("id") or "?")


def _display_name(item: Dict[str, Any]) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    return (
        item.get("name")
        or item.get("company")
        or raw.get("contact_name")
        or raw.get("company_name")
        or raw.get("account_name")
        or raw.get("name")
        or "?"
    )


def _email(item: Dict[str, Any]) -> Optional[str]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    return item.get("email") or raw.get("email")


def _status(item: Dict[str, Any]) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    status = item.get("status") or raw.get("status") or raw.get("account_status")
    return str(status or "active?")


async def build_zoho_brief(user_id: str, organization_id: str) -> str:
    """Fetch summary data from Zoho and assemble a markdown brief."""

    company_name = "Unknown"
    company_info: Dict[str, Any] = {}
    currency = ""
    try:
        info = await zoho_connector.get_company_info(user_id, organization_id)
        company_info = info or {}
        company_name = info.get("name") or info.get("company_name") or "Unknown"
        currency = info.get("currency_code") or ""
    except Exception:
        pass

    accounts: List[Dict] = []
    customers: List[Dict] = []
    vendors: List[Dict] = []
    items: List[Dict] = []

    try:
        accounts = await zoho_connector.list_accounts(user_id, organization_id)
    except Exception as exc:
        logger.warning(f"zoho_brief: accounts fetch failed: {exc}")
    try:
        customers = await zoho_connector.list_customers(user_id, organization_id)
    except Exception as exc:
        logger.warning(f"zoho_brief: customers fetch failed: {exc}")
    try:
        vendors = await zoho_connector.list_vendors(user_id, organization_id)
    except Exception as exc:
        logger.warning(f"zoho_brief: vendors fetch failed: {exc}")
    try:
        items = await zoho_connector.list_items(user_id, organization_id)
    except Exception as exc:
        logger.warning(f"zoho_brief: items fetch failed: {exc}")

    # ---- Assemble markdown ----
    lines: List[str] = [f"## Company context — {company_name}"]
    lines.append(f"Brief schema: {BRIEF_SCHEMA}.")
    if organization_id:
        lines.append(f"Organization id: `{organization_id}`.")
    if currency:
        lines.append(f"Base currency: {currency}.")
    if company_info:
        country = company_info.get("country") or company_info.get("country_code")
        fy_start = company_info.get("fiscal_year_start_month")
        tax_basis = company_info.get("tax_basis")
        if country:
            lines.append(f"Country: {country}.")
        if fy_start:
            lines.append(f"Fiscal year starts month {fy_start}.")
        if tax_basis:
            lines.append(f"Tax basis: {tax_basis}.")

    lines.append("")
    lines.append("### How to use this company directory")
    lines.append("- This snapshot is for choosing the right Zoho Books resource, exact names, and IDs.")
    lines.append("- Prefer direct zoho_list/zoho_report calls using these names/IDs; do not call list_zoho_entities just to rediscover available resources.")
    lines.append("- Use reports for totals and accounting statements; use zoho_list for detail rows and entity lookups.")
    lines.append("- The snapshot can be up to 24 hours old, so verify live amounts, dates, and open balances with tools before answering.")

    # Chart of accounts grouped by account_type
    lines.append("")
    lines.append(f"### Full Chart of Accounts ({len(accounts)} accounts)")
    type_buckets: Dict[str, List[Dict]] = {}
    for a in accounts:
        atype = a.get("account_type") or "other"
        type_buckets.setdefault(atype, []).append(a)
    for atype in sorted(type_buckets):
        bucket = sorted(type_buckets[atype], key=lambda x: _display_name(x).lower())
        lines.append(f"- **{atype}** ({len(bucket)} accounts)")
        for acc in bucket:
            name = _display_name(acc)
            bal = _fmt_money(acc.get("balance"))
            raw = acc.get("raw") if isinstance(acc.get("raw"), dict) else {}
            code = raw.get("account_code") or raw.get("code")
            details = [f"id={_ref_id(acc)}", name, f"balance={bal}", _status(acc)]
            if code:
                details.insert(2, f"code={code}")
            lines.append("  - " + " | ".join(details))

    # Top customers by balance
    lines.append("")
    lines.append(f"### Customer directory ({len(customers)} customers)")
    top_cust = [c for c in _top_by(customers, "balance", 10) if float(c.get("balance") or 0) != 0]
    if top_cust:
        lines.append("Top AR balances:")
        for c in top_cust:
            lines.append(f"- id={_ref_id(c)} | {_display_name(c)} | balance={_fmt_money(c.get('balance'))}")
    if customers:
        lines.append("All customers:")
        for c in sorted(customers, key=lambda x: _display_name(x).lower()):
            extra = []
            company = c.get("company")
            email = _email(c)
            if company and company != _display_name(c):
                extra.append(f"company={company}")
            if email:
                extra.append(f"email={email}")
            extra.append(f"balance={_fmt_money(c.get('balance'))}")
            extra.append(_status(c))
            lines.append(f"- id={_ref_id(c)} | {_display_name(c)} | " + " | ".join(extra))
    else:
        lines.append("- (none)")

    # Top vendors by balance
    lines.append("")
    lines.append(f"### Vendor directory ({len(vendors)} vendors)")
    top_vend = [v for v in _top_by(vendors, "balance", 10) if float(v.get("balance") or 0) != 0]
    if top_vend:
        lines.append("Top AP balances:")
        for v in top_vend:
            lines.append(f"- id={_ref_id(v)} | {_display_name(v)} | balance={_fmt_money(v.get('balance'))}")
    if vendors:
        lines.append("All vendors:")
        for v in sorted(vendors, key=lambda x: _display_name(x).lower()):
            extra = []
            company = v.get("company")
            email = _email(v)
            if company and company != _display_name(v):
                extra.append(f"company={company}")
            if email:
                extra.append(f"email={email}")
            extra.append(f"balance={_fmt_money(v.get('balance'))}")
            extra.append(_status(v))
            lines.append(f"- id={_ref_id(v)} | {_display_name(v)} | " + " | ".join(extra))
    else:
        lines.append("- (none)")

    # Items
    lines.append("")
    lines.append(f"### Item / Service directory ({len(items)} items)")
    if items:
        for it in sorted(items, key=lambda x: _display_name(x).lower()):
            raw = it.get("raw") if isinstance(it.get("raw"), dict) else {}
            item_type = raw.get("item_type") or raw.get("product_type") or raw.get("type") or ""
            details = [f"id={_ref_id(it)}", _display_name(it), f"rate={_fmt_money(it.get('rate'))}", _status(it)]
            if item_type:
                details.insert(2, f"type={item_type}")
            lines.append("- " + " | ".join(details))
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Quick query hints")
    lines.append("- Customer/vendor-specific detail: use zoho_list('invoices') or zoho_list('bills') with exact names from the directory, and search_text for partial matches.")
    lines.append("- Account-specific detail: use account IDs/names above, then zoho_report('trialbalance'), zoho_report('balancesheet'), or zoho_report('cashflow') where applicable.")
    lines.append("- P&L, Balance Sheet, Trial Balance, Cash Flow, and movement-of-equity totals should be fetched with zoho_report, not reconstructed manually from list rows.")
    lines.append("- For names with punctuation or uncertain casing, use search_text with a stable partial name and keep result sets small.")

    # Legacy short item preview kept for readability when the directory is huge.
    lines.append("")
    lines.append("### Item / Service preview")
    for it in items[:15]:
        lines.append(f"- {_display_name(it)} unit price {_fmt_money(it.get('rate'))}")

    lines.append("")
    lines.append(
        "Use this brief to pick the correct account / customer / vendor names "
        "before querying. When unsure of exact spelling, search with a partial "
        "name match."
    )

    return "\n".join(lines)


# -----------------------------------------------------------------------
# Persistence (zoho_company_profile, created in migration 014)
# -----------------------------------------------------------------------

def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS zoho_company_profile (
                user_id         TEXT NOT NULL,
                organization_id TEXT NOT NULL,
                brief_md        TEXT NOT NULL,
                refreshed_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, organization_id)
            )
        """)
        conn.commit()


def save_brief(user_id: str, organization_id: str, brief_md: str) -> None:
    conn = get_connection()
    if conn is None:
        logger.error("zoho_brief save skipped — DB unavailable")
        return
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO zoho_company_profile (user_id, organization_id, brief_md, refreshed_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, organization_id) DO UPDATE
                  SET brief_md = EXCLUDED.brief_md,
                      refreshed_at = CURRENT_TIMESTAMP
                """,
                (user_id, organization_id, brief_md),
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


def load_brief(user_id: str, organization_id: str) -> Optional[str]:
    conn = get_connection()
    if conn is None:
        return None
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT brief_md FROM zoho_company_profile WHERE user_id = %s AND organization_id = %s",
                (user_id, organization_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as exc:
        logger.warning(f"load_brief failed: {exc}")
        return None
    finally:
        return_connection(conn)


def is_stale(user_id: str, organization_id: str) -> bool:
    conn = get_connection()
    if conn is None:
        return True
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT refreshed_at FROM zoho_company_profile WHERE user_id = %s AND organization_id = %s",
                (user_id, organization_id),
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


async def refresh_brief(user_id: str, organization_id: str) -> str:
    brief = await build_zoho_brief(user_id, organization_id)
    save_brief(user_id, organization_id, brief)
    return brief


async def get_or_refresh_brief(user_id: str, organization_id: str) -> Optional[str]:
    if not is_stale(user_id, organization_id):
        cached = load_brief(user_id, organization_id)
        if cached and BRIEF_SCHEMA in cached:
            return cached
    try:
        return await refresh_brief(user_id, organization_id)
    except Exception as exc:
        logger.warning(f"get_or_refresh_brief failed, falling back to cache: {exc}")
        return load_brief(user_id, organization_id)
