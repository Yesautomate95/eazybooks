"""
Per-user drafts for in-progress SAP S/4HANA work (purchase / sales / bank-entries).

A draft is one *session* of work — typically the contents of the bills queue,
the bank-entries table, or the sales invoice queue — stored as a JSONB blob so
the frontend can reopen and continue. PDFs themselves are NOT persisted; only
extracted state + edits.

Mirrors qbo_drafts_store / zoho_drafts_store with the SAP `company_code` company
key. Migration 024 brought sap_drafts to parity with the other platforms'
status / pushed_at / error_message lifecycle columns, so the full lifecycle
surface below persists exactly like the Zoho store.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection

logger = logging.getLogger(__name__)


VALID_FLOWS = ("purchase", "sales", "bank_entries")
# Mirrors tally_drafts_store so the shared frontend treats every platform alike.
#   draft   → user has saved edits, push-eligible
#   pushing → push to SAP in progress
#   pushed  → successfully pushed
#   error   → push failed
# (uploaded/extracting/extracted exist for parity but cloud extraction is
#  client-driven, so SAP drafts are created directly in 'draft'.)
VALID_STATUSES = ("uploaded", "extracting", "extracted", "draft", "pushing", "pushed", "error")


def upsert(
    *,
    user_id: str,
    company_code: str,
    flow: str,
    payload: Dict[str, Any],
    title: Optional[str] = None,
    draft_id: Optional[str] = None,
    status: str = "draft",
) -> Optional[str]:
    """Insert a new draft or update an existing one (when draft_id is passed).
    Returns the draft id, or None if persistence failed."""
    if flow not in VALID_FLOWS:
        raise ValueError(f"invalid flow: {flow!r}")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    conn = get_connection()
    if conn is None:
        logger.error("sap_drafts upsert skipped — DB unavailable")
        return None
    try:
        cur = conn.cursor()
        if draft_id:
            cur.execute(
                """
                UPDATE sap_drafts
                   SET title = %s,
                       payload = %s,
                       status = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s AND user_id = %s AND company_code = %s AND flow = %s
                RETURNING id
                """,
                (title, Json(payload), status, draft_id, user_id, company_code, flow),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return str(row[0])
            # No row matched — fall through to insert so a stale id from the
            # frontend doesn't silently lose the user's work.
        cur.execute(
            """
            INSERT INTO sap_drafts (user_id, company_code, flow, title, payload, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, company_code, flow, title, Json(payload), status),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return str(new_id)
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception(f"sap_drafts upsert failed: {exc}")
        return None
    finally:
        return_connection(conn)


def list_drafts(
    *,
    user_id: str,
    company_code: Optional[str],
    flow: Optional[str],
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Header listing — no payload, just enough to render a picker."""
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        clauses = ["user_id = %s"]
        params: List[Any] = [user_id]
        if company_code:
            clauses.append("company_code = %s")
            params.append(company_code)
        if flow:
            if flow not in VALID_FLOWS:
                return []
            clauses.append("flow = %s")
            params.append(flow)
        if status:
            # Support comma-separated: "draft,pushed"
            statuses = [s.strip() for s in status.split(",") if s.strip() in VALID_STATUSES]
            if statuses:
                placeholders = ",".join(["%s"] * len(statuses))
                clauses.append(f"status IN ({placeholders})")
                params.extend(statuses)
        where = " AND ".join(clauses)
        cur.execute(
            f"""
            SELECT id, company_code, flow, title, status, created_at, updated_at,
                   pushed_at, error_message
              FROM sap_drafts
             WHERE {where}
             ORDER BY updated_at DESC
             LIMIT 100
            """,
            tuple(params),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        return_connection(conn)


def get_draft(*, user_id: str, draft_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT id, company_code, flow, title, payload, status, created_at, updated_at,
                   pushed_at, error_message
              FROM sap_drafts
             WHERE id = %s AND user_id = %s
            """,
            (draft_id, user_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        return_connection(conn)


def update_status(
    *,
    draft_id: str,
    user_id: str,
    status: str,
    error_message: Optional[str] = None,
) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    conn = get_connection()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE sap_drafts
               SET status = %s,
                   error_message = %s,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND user_id = %s
            """,
            (status, error_message, draft_id, user_id),
        )
        updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("sap_drafts update_status failed: %s", exc)
        return False
    finally:
        return_connection(conn)


def update_payload(
    *,
    draft_id: str,
    user_id: str,
    payload: Dict[str, Any],
    set_status: Optional[str] = None,
) -> bool:
    """Persist an updated payload. When set_status is provided we move the row
    to that lifecycle state in the same UPDATE; otherwise the existing status is
    preserved. This keeps automatic transitions (e.g. extract -> 'extracted')
    and user-driven ones (e.g. save -> 'draft') explicit at the call site."""
    if set_status is not None and set_status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {set_status!r}")
    conn = get_connection()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        if set_status is None:
            cur.execute(
                """
                UPDATE sap_drafts
                   SET payload = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s AND user_id = %s
                """,
                (Json(payload), draft_id, user_id),
            )
        else:
            cur.execute(
                """
                UPDATE sap_drafts
                   SET payload = %s,
                       status = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s AND user_id = %s
                """,
                (Json(payload), set_status, draft_id, user_id),
            )
        updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("sap_drafts update_payload failed: %s", exc)
        return False
    finally:
        return_connection(conn)


def mark_pushed(*, draft_id: str, user_id: str) -> bool:
    conn = get_connection()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE sap_drafts
               SET status = 'pushed',
                   pushed_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND user_id = %s
            """,
            (draft_id, user_id),
        )
        updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("sap_drafts mark_pushed failed: %s", exc)
        return False
    finally:
        return_connection(conn)


def delete_draft(*, user_id: str, draft_id: str) -> bool:
    conn = get_connection()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM sap_drafts WHERE id = %s AND user_id = %s",
            (draft_id, user_id),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        return_connection(conn)
