"""
Read/write helpers for Tally cached companies and ledgers.

Populated whenever a live fetch (WebSocket or direct HTTP) succeeds, and read
when the bridge is offline so users can still pick a company, open drafts, and
edit ledger mappings against the last-known list.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection

logger = logging.getLogger(__name__)


def upsert_companies(*, user_id: str, companies: List[Any]) -> None:
    if not user_id or not companies:
        return
    names: List[str] = []
    for entry in companies:
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            name = (entry.get("name") or entry.get("company") or "").strip()
        else:
            name = ""
        if name:
            names.append(name)
    if not names:
        return
    conn = get_connection()
    if conn is None:
        return
    try:
        cur = conn.cursor()
        for name in names:
            cur.execute(
                """
                INSERT INTO tally_cached_companies (user_id, company_name, last_seen_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, company_name)
                DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
                """,
                (user_id, name),
            )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("tally cached companies upsert failed: %s", exc)
    finally:
        return_connection(conn)


def list_cached_companies(*, user_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT company_name, last_seen_at, ledgers_synced_at
              FROM tally_cached_companies
             WHERE user_id = %s
             ORDER BY last_seen_at DESC
            """,
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        return_connection(conn)


def upsert_ledgers(*, user_id: str, company_name: str, ledgers: List[Any]) -> None:
    if not user_id or not company_name or ledgers is None:
        return
    conn = get_connection()
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tally_cached_ledgers (user_id, company_name, ledgers, refreshed_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, company_name)
            DO UPDATE SET ledgers = EXCLUDED.ledgers,
                          refreshed_at = CURRENT_TIMESTAMP
            """,
            (user_id, company_name, Json(ledgers)),
        )
        cur.execute(
            """
            INSERT INTO tally_cached_companies (user_id, company_name, last_seen_at, ledgers_synced_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, company_name)
            DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP,
                          ledgers_synced_at = CURRENT_TIMESTAMP
            """,
            (user_id, company_name),
        )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("tally cached ledgers upsert failed: %s", exc)
    finally:
        return_connection(conn)


def get_cached_ledgers(*, user_id: str, company_name: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT ledgers, refreshed_at
              FROM tally_cached_ledgers
             WHERE user_id = %s AND company_name = %s
            """,
            (user_id, company_name),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        return_connection(conn)


def upsert_grouped_ledgers(
    *, user_id: str, company_name: str, grouped: Dict[str, Any]
) -> None:
    """Persist the grouped (party / purchase / sales) ledger payload that the
    suggestion service consumes. Called on every successful live WebSocket
    fetch so the offline path has the right shape to fall back to.

    Coexists with the flat-list cache in the same row — both columns are
    populated independently as live fetches succeed."""
    if not user_id or not company_name or grouped is None:
        return
    conn = get_connection()
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tally_cached_ledgers (
                user_id, company_name, ledgers,
                grouped_ledgers, grouped_refreshed_at,
                refreshed_at
            )
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, company_name)
            DO UPDATE SET grouped_ledgers = EXCLUDED.grouped_ledgers,
                          grouped_refreshed_at = CURRENT_TIMESTAMP
            """,
            (user_id, company_name, Json([]), Json(grouped)),
        )
        cur.execute(
            """
            INSERT INTO tally_cached_companies (user_id, company_name, last_seen_at, ledgers_synced_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, company_name)
            DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP,
                          ledgers_synced_at = CURRENT_TIMESTAMP
            """,
            (user_id, company_name),
        )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("tally cached grouped ledgers upsert failed: %s", exc)
    finally:
        return_connection(conn)


def get_cached_grouped_ledgers(
    *, user_id: str, company_name: str
) -> Optional[Dict[str, Any]]:
    """Return the last-known grouped (party/purchase/sales) ledger payload
    plus its refresh timestamp, or None if nothing has been cached for this
    user+company."""
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT grouped_ledgers, grouped_refreshed_at
              FROM tally_cached_ledgers
             WHERE user_id = %s AND company_name = %s
            """,
            (user_id, company_name),
        )
        row = cur.fetchone()
        if not row or not row.get("grouped_ledgers"):
            return None
        return dict(row)
    finally:
        return_connection(conn)
