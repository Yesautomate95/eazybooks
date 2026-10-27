"""Pull master data from live Tally and upsert into the Postgres schema
used by TallyChat (mst_group / mst_ledger / mst_stock_item / mst_godown /
mst_vouchertype). MVP scope: master data only — transactions still need
a full sync tool (e.g. tally-database-loader).

Synthetic GUIDs are md5(user_id|company|kind|name) so re-runs are idempotent
and rows stay tenant-scoped.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from psycopg2.extras import execute_values

from applicationca.database.database_retrieval import get_connection
from applicationca.services.tally_connector import TallyConnector, tally_connector

logger = logging.getLogger(__name__)


def _synthetic_guid(user_id: str, company: str, kind: str, name: str) -> str:
    raw = f"{user_id}|{company}|{kind}|{name}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _to_number(value: Any) -> float:
    """Tally returns strings like '1,234.56 Dr' or '(500.00)'. Extract a float."""
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    negative = text.endswith("Cr") or text.startswith("(")
    match = _NUMERIC_RE.search(text.replace(",", ""))
    if not match:
        return 0.0
    num = float(match.group(0))
    return -num if negative and num > 0 else num


class _BridgeConnector(TallyConnector):
    """TallyConnector whose XML transport goes through the user's desktop app.

    The cloud backend cannot reach a user's local Tally over HTTP, but the
    desktop bridge's 'push-to-tally' message forwards any XML to Tally and
    returns the raw response, so every existing get_* parser works unchanged.
    """

    def __init__(self, user_id: str):
        super().__init__()
        self._user_id = user_id
        self._company: Optional[str] = None

    async def _post_xml(self, xml_payload: str) -> str:
        from applicationca.contoller.websocket_controller import manager

        data = await manager.push_to_tally(
            self._user_id, self._company or "", xml_payload, timeout=120
        )
        raw = data.get("raw")
        if not raw:
            raise RuntimeError("Desktop bridge returned an empty response from Tally")
        return raw


async def _fetch_all(company: str, connector: Optional[TallyConnector] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Pull every master collection we care about. Each call is independent,
    but we keep them sequential so Tally isn't hammered with concurrent
    TDL requests (ODBC/XML server isn't fond of that)."""
    c = connector or tally_connector
    return {
        "groups": await c.get_groups(company),
        "ledgers": await c.get_ledgers(company),
        "stock_items": await c.get_inventory(company),
        "godowns": await c.get_godowns(company),
        "voucher_types": await c.get_voucher_types(company),
    }


def _upsert(
    cursor,
    table: str,
    columns: List[str],
    rows: List[Tuple],
    conflict_col: str = "guid",
) -> int:
    if not rows:
        return 0
    col_list = ", ".join(columns)
    update_cols = [c for c in columns if c != conflict_col]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    query = (
        f"INSERT INTO {table} ({col_list}) VALUES %s "
        f"ON CONFLICT ({conflict_col}) DO UPDATE SET {set_clause}"
    )
    execute_values(cursor, query, rows)
    return cursor.rowcount or len(rows)


def _build_group_rows(user_id: str, company: str, data: List[Dict]) -> List[Tuple]:
    out = []
    for g in data:
        name = g.get("name", "").strip()
        if not name:
            continue
        out.append((
            _synthetic_guid(user_id, company, "group", name),
            name,
            g.get("parent", "") or "",
            user_id,
            company,
        ))
    return out


def _build_ledger_rows(user_id: str, company: str, data: List[Dict]) -> List[Tuple]:
    out = []
    for l in data:
        name = l.get("name", "").strip()
        if not name:
            continue
        opening = _to_number(l.get("openingBalance"))
        # Tally omits CLOSINGBALANCE when it equals opening — fall back to opening.
        raw_closing = l.get("closingBalance")
        closing = _to_number(raw_closing) if raw_closing not in (None, "", "0") else opening
        out.append((
            _synthetic_guid(user_id, company, "ledger", name),
            name,
            l.get("parent", "") or "",
            opening,
            closing,
            user_id,
            company,
        ))
    return out


def _build_stock_item_rows(user_id: str, company: str, data: List[Dict]) -> List[Tuple]:
    out = []
    for s in data:
        name = s.get("name", "").strip()
        if not name:
            continue
        opening = _to_number(s.get("openingBalance"))
        opening_value = _to_number(s.get("openingValue"))
        raw_closing = s.get("closingBalance")
        closing = _to_number(raw_closing) if raw_closing not in (None, "", "0") else opening
        raw_closing_val = s.get("closingValue")
        closing_value = _to_number(raw_closing_val) if raw_closing_val not in (None, "", "0") else opening_value
        out.append((
            _synthetic_guid(user_id, company, "stock_item", name),
            name,
            s.get("parent", "") or "",
            s.get("baseUnits", "") or "",
            opening,
            opening_value,
            closing,
            closing_value,
            user_id,
            company,
        ))
    return out


def _build_godown_rows(user_id: str, company: str, data: List[Dict]) -> List[Tuple]:
    out = []
    for g in data:
        name = g.get("name", "").strip()
        if not name:
            continue
        out.append((
            _synthetic_guid(user_id, company, "godown", name),
            name,
            g.get("parent", "") or "",
            user_id,
            company,
        ))
    return out


def _build_vouchertype_rows(user_id: str, company: str, data: List[Dict]) -> List[Tuple]:
    out = []
    for v in data:
        name = v.get("name", "").strip()
        if not name:
            continue
        out.append((
            _synthetic_guid(user_id, company, "voucher_type", name),
            name,
            v.get("parent", "") or "",
            user_id,
            company,
        ))
    return out


def _is_loader_managed(user_id: str, company: str) -> bool:
    """True if the desktop tally-database-loader already owns this (user, company).

    The two ingest paths write the SAME tables with DIFFERENT guid schemes —
    sync_masters uses md5 synthetic guids, the loader uses real Tally guids
    (which contain dashes). They can't dedupe against each other via
    ON CONFLICT (guid), so running sync_masters over a loader-managed company
    would create a second copy of every ledger and silently double chat
    totals. We detect the loader by a tally_sync_state row OR any non-synthetic
    guid already present in mst_ledger, and refuse in that case.
    """
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return False  # can't tell; let the caller proceed
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM tally_sync_state WHERE user_id = %s AND company_name = %s LIMIT 1",
                (user_id, company),
            )
            if cur.fetchone():
                return True
            # A real Tally guid is not a bare 32-char md5; synthetic ones are.
            cur.execute(
                """
                SELECT 1 FROM mst_ledger
                 WHERE user_id = %s AND company_name = %s
                   AND guid !~ '^[0-9a-f]{32}$'
                 LIMIT 1
                """,
                (user_id, company),
            )
            return cur.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"loader-managed check failed ({exc}); allowing sync.")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn:
            conn.close()


async def sync_masters(
    user_id: str,
    company: str,
    connector: Optional[TallyConnector] = None,
    check_loader: bool = True,
) -> Dict[str, Any]:
    """Pull masters from live Tally and upsert into Postgres.

    Returns {"success": bool, "counts": {...}, "error": str|None}.
    """
    if not company:
        return {"success": False, "error": "company is required", "counts": {}}

    # Guard against the dual-ingest-path duplication (see _is_loader_managed).
    if check_loader and _is_loader_managed(user_id, company):
        return {
            "success": False,
            "error": (
                "This company is already synced by the desktop Tally loader. "
                "Use the desktop app's sync for it — running the masters-only "
                "sync here would create duplicate ledgers."
            ),
            "counts": {},
        }

    try:
        data = await _fetch_all(company, connector)
    except Exception as exc:
        logger.error(f"Tally fetch failed during sync: {exc}")
        return {"success": False, "error": f"Tally fetch failed: {exc}", "counts": {}}

    group_rows = _build_group_rows(user_id, company, data["groups"])
    ledger_rows = _build_ledger_rows(user_id, company, data["ledgers"])
    stock_rows = _build_stock_item_rows(user_id, company, data["stock_items"])
    godown_rows = _build_godown_rows(user_id, company, data["godowns"])
    vtype_rows = _build_vouchertype_rows(user_id, company, data["voucher_types"])

    plan: List[Tuple[str, str, List[str], List[Tuple]]] = [
        ("groups",        "mst_group",        ["guid", "name", "parent", "user_id", "company_name"], group_rows),
        ("ledgers",       "mst_ledger",       ["guid", "name", "parent", "opening_balance", "closing_balance", "user_id", "company_name"], ledger_rows),
        ("stock_items",   "mst_stock_item",   ["guid", "name", "parent", "uom", "opening_balance", "opening_value", "closing_balance", "closing_value", "user_id", "company_name"], stock_rows),
        ("godowns",       "mst_godown",       ["guid", "name", "parent", "user_id", "company_name"], godown_rows),
        ("voucher_types", "mst_vouchertype",  ["guid", "name", "parent", "user_id", "company_name"], vtype_rows),
    ]

    counts: Dict[str, int] = {}
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return {"success": False, "error": "DB connection unavailable", "counts": {}}
        with conn.cursor() as cursor:
            for key, table, columns, rows in plan:
                counts[key] = _upsert(cursor, table, columns, rows)
            _ensure_profile_table(cursor)
            brief = _build_company_brief(cursor, user_id, company)
            _upsert_profile(cursor, user_id, company, brief)
        conn.commit()
    except Exception as exc:
        if conn:
            conn.rollback()
        logger.error(f"DB upsert failed during sync: {exc}")
        return {"success": False, "error": f"DB upsert failed: {exc}", "counts": counts}
    finally:
        if conn:
            conn.close()

    logger.info(f"Tally sync complete for user={user_id} company={company} counts={counts}")
    return {"success": True, "counts": counts, "error": None}


def _set_sync_state(
    user_id: str,
    company: str,
    status: str,
    counts: Optional[Dict[str, int]] = None,
    error: Optional[str] = None,
) -> None:
    """Upsert the (user, company) row the web app polls via /sync-status."""
    import json

    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tally_sync_state
                    (user_id, company_name, status, sync_mode, sync_started_at,
                     last_synced_at, last_full_reconcile_at, last_error, row_counts)
                VALUES (%s, %s, %s, 'full',
                        CASE WHEN %s = 'syncing' THEN NOW() END,
                        CASE WHEN %s = 'completed' THEN NOW() END,
                        CASE WHEN %s = 'completed' THEN NOW() END,
                        %s, %s::jsonb)
                ON CONFLICT (user_id, company_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    sync_mode = 'full',
                    sync_started_at = CASE WHEN EXCLUDED.status = 'syncing'
                                           THEN NOW() ELSE tally_sync_state.sync_started_at END,
                    last_synced_at = CASE WHEN EXCLUDED.status = 'completed'
                                          THEN NOW() ELSE tally_sync_state.last_synced_at END,
                    last_full_reconcile_at = CASE WHEN EXCLUDED.status = 'completed'
                                                  THEN NOW() ELSE tally_sync_state.last_full_reconcile_at END,
                    last_error = EXCLUDED.last_error,
                    row_counts = CASE WHEN EXCLUDED.status = 'completed'
                                      THEN EXCLUDED.row_counts ELSE tally_sync_state.row_counts END
                """,
                (user_id, company, status, status, status, status, error, json.dumps(counts or {})),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"could not record sync state ({exc})")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            conn.close()


async def sync_masters_via_bridge(user_id: str, company: str) -> Dict[str, Any]:
    """Sync a company's masters by pulling XML through the user's desktop app.

    Works in production (cloud backend -> desktop bridge -> local Tally) and
    keeps all DB writes on the backend, so the desktop needs no DB credentials.
    Masters only: groups, ledgers, stock items, godowns, voucher types.
    """
    _set_sync_state(user_id, company, "syncing")
    connector = _BridgeConnector(user_id)
    connector._company = company
    result = await sync_masters(user_id, company, connector=connector, check_loader=False)
    if result.get("success"):
        _set_sync_state(user_id, company, "completed", counts=result.get("counts"))
    else:
        _set_sync_state(user_id, company, "failed", error=result.get("error"))
    return result


# ---------------------------------------------------------------------------
#  Company profile — "helicopter view" brief injected into the agent prompt.
# ---------------------------------------------------------------------------

_PROFILE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS tally_company_profile (
    user_id       varchar(255) NOT NULL,
    company_name  varchar(255) NOT NULL,
    brief_md      text         NOT NULL,
    refreshed_at  timestamptz  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, company_name)
);
"""


def _ensure_profile_table(cursor) -> None:
    cursor.execute(_PROFILE_TABLE_DDL)


def _safe_fetchall(cursor, sql: str, params: Tuple) -> List[Tuple]:
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    except Exception as exc:
        logger.warning(f"Profile query failed ({exc}); treating as empty.")
        # Reset aborted transaction state on the current cursor's connection
        # so subsequent queries still work.
        try:
            cursor.connection.rollback()
        except Exception:
            pass
        return []


def _build_company_brief(cursor, user_id: str, company: str) -> str:
    """Assemble a compact markdown brief the agent can use as company context."""
    tenant = (user_id, company)

    # Group tree with ledger counts
    groups = _safe_fetchall(
        cursor,
        """
        SELECT g.name, g.parent, COUNT(l.guid) AS ledger_count
        FROM mst_group g
        LEFT JOIN mst_ledger l
          ON l.parent = g.name
         AND l.user_id = g.user_id
         AND l.company_name = g.company_name
        WHERE g.user_id = %s AND g.company_name = %s
        GROUP BY g.name, g.parent
        ORDER BY ledger_count DESC, g.name
        LIMIT 60
        """,
        tenant,
    )

    # Top ledgers by |closing_balance|
    top_ledgers = _safe_fetchall(
        cursor,
        """
        SELECT name, parent, closing_balance
        FROM mst_ledger
        WHERE user_id = %s AND company_name = %s
        ORDER BY ABS(closing_balance) DESC NULLS LAST
        LIMIT 20
        """,
        tenant,
    )

    # Top stock items by |closing_value|
    top_items = _safe_fetchall(
        cursor,
        """
        SELECT name, parent, closing_value
        FROM mst_stock_item
        WHERE user_id = %s AND company_name = %s
        ORDER BY ABS(closing_value) DESC NULLS LAST
        LIMIT 10
        """,
        tenant,
    )

    # Transaction-table row counts + FY range
    trn_counts: Dict[str, int] = {}
    for table in ("trn_voucher", "trn_accounting", "trn_inventory"):
        rows = _safe_fetchall(
            cursor,
            f"SELECT COUNT(*) FROM {table} WHERE user_id = %s AND company_name = %s",
            tenant,
        )
        trn_counts[table] = rows[0][0] if rows else 0

    fy_range = _safe_fetchall(
        cursor,
        "SELECT MIN(date), MAX(date) FROM trn_voucher WHERE user_id = %s AND company_name = %s",
        tenant,
    )
    fy_min = fy_range[0][0] if fy_range and fy_range[0][0] else None
    fy_max = fy_range[0][1] if fy_range and fy_range[0][1] else None

    # Build markdown
    lines: List[str] = [f"## Company context — {company}"]

    lines.append("")
    lines.append("### Groups (name → ledger_count) [parent]")
    if groups:
        for name, parent, count in groups:
            parent_str = f" [{parent}]" if parent else ""
            lines.append(f"- {name} ({count}){parent_str}")
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Top ledgers by |closing_balance|")
    if top_ledgers:
        for name, parent, bal in top_ledgers:
            bal_f = float(bal) if bal is not None else 0.0
            lines.append(f"- {name}  [{parent}]  →  {bal_f:,.2f}")
    else:
        lines.append("- (none)")

    if top_items:
        lines.append("")
        lines.append("### Top stock items by |closing_value|")
        for name, parent, val in top_items:
            val_f = float(val) if val is not None else 0.0
            lines.append(f"- {name}  [{parent}]  →  {val_f:,.2f}")

    lines.append("")
    lines.append("### Transaction data")
    for table, n in trn_counts.items():
        lines.append(f"- {table}: {n:,} rows")
    if fy_min and fy_max:
        lines.append(f"- voucher date range: {fy_min} → {fy_max}")
    else:
        lines.append("- voucher date range: (no vouchers synced)")

    lines.append("")
    lines.append(
        "Use this brief to pick correct group names and ledger spellings "
        "before writing SQL. If an item isn't listed, it may still exist — "
        "use ILIKE to search."
    )

    return "\n".join(lines)


def _upsert_profile(cursor, user_id: str, company: str, brief_md: str) -> None:
    cursor.execute(
        """
        INSERT INTO tally_company_profile (user_id, company_name, brief_md, refreshed_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (user_id, company_name) DO UPDATE
          SET brief_md = EXCLUDED.brief_md,
              refreshed_at = NOW()
        """,
        (user_id, company, brief_md),
    )


def load_company_brief(user_id: str, company: str) -> Optional[str]:
    """Read the cached brief for a (user, company). Returns None if absent."""
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return None
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT brief_md FROM tally_company_profile
                WHERE user_id = %s AND company_name = %s
                """,
                (user_id, company),
            )
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as exc:
        logger.warning(f"load_company_brief failed: {exc}")
        return None
    finally:
        if conn:
            conn.close()
