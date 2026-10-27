"""
Per-user drafts for in-progress Tally work (purchase / sales / bank-entries).

Lifecycle statuses:
  uploaded   → file saved to R2, extraction not yet run
  extracting → extraction in progress
  extracted  → extraction complete, awaiting user review (not yet a draft)
  draft      → user has explicitly saved edits via Save Draft
  pushing    → push to Tally in progress
  pushed     → successfully pushed
  error      → extraction or push failed
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection

logger = logging.getLogger(__name__)


VALID_FLOWS = ("purchase", "sales", "bank_entries")
VALID_STATUSES = ("uploaded", "extracting", "extracted", "draft", "pushing", "pushed", "error")


def upsert(
    *,
    user_id: str,
    company_name: str,
    flow: str,
    payload: Dict[str, Any],
    title: Optional[str] = None,
    draft_id: Optional[str] = None,
    status: str = "draft",
    file_url: Optional[str] = None,
    file_name: Optional[str] = None,
    r2_key: Optional[str] = None,
) -> Optional[str]:
    if flow not in VALID_FLOWS:
        raise ValueError(f"invalid flow: {flow!r}")
    conn = get_connection()
    if conn is None:
        logger.error("tally_drafts upsert skipped — DB unavailable")
        return None
    try:
        cur = conn.cursor()
        if draft_id:
            cur.execute(
                """
                UPDATE tally_drafts
                   SET title = %s,
                       payload = %s,
                       status = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s AND user_id = %s AND company_name = %s AND flow = %s
                RETURNING id
                """,
                (title, Json(payload), status, draft_id, user_id, company_name, flow),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return str(row[0])
        cur.execute(
            """
            INSERT INTO tally_drafts (user_id, company_name, flow, title, payload, status, file_url, file_name, r2_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, company_name, flow, title, Json(payload), status, file_url, file_name, r2_key),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return str(new_id)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("tally_drafts upsert failed: %s", exc)
        return None
    finally:
        return_connection(conn)


def list_drafts(
    *,
    user_id: str,
    company_name: Optional[str] = None,
    flow: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        clauses = ["user_id = %s"]
        params: List[Any] = [user_id]
        if company_name:
            clauses.append("company_name = %s")
            params.append(company_name)
        if flow:
            if flow not in VALID_FLOWS:
                return []
            clauses.append("flow = %s")
            params.append(flow)
        if status:
            # Support comma-separated: "uploaded,draft"
            statuses = [s.strip() for s in status.split(",") if s.strip() in VALID_STATUSES]
            if statuses:
                placeholders = ",".join(["%s"] * len(statuses))
                clauses.append(f"status IN ({placeholders})")
                params.extend(statuses)
        where = " AND ".join(clauses)
        cur.execute(
            f"""
            SELECT id, company_name, flow, title, status, file_url, file_name,
                   created_at, updated_at, pushed_at, error_message
              FROM tally_drafts
             WHERE {where}
             ORDER BY updated_at DESC
             LIMIT 200
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
            SELECT id, company_name, flow, title, payload, status, file_url, file_name,
                   r2_key, created_at, updated_at, pushed_at, error_message
              FROM tally_drafts
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
            UPDATE tally_drafts
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
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("update_status failed: %s", exc)
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
    to that lifecycle state in the same UPDATE; otherwise the existing status
    is preserved. This keeps the boundary between automatic transitions (e.g.
    extract → 'extracted') and user-driven ones (e.g. save → 'draft') explicit
    at the call site."""
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
                UPDATE tally_drafts
                   SET payload = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s AND user_id = %s
                """,
                (Json(payload), draft_id, user_id),
            )
        else:
            cur.execute(
                """
                UPDATE tally_drafts
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
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("update_payload failed: %s", exc)
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
            UPDATE tally_drafts
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
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("mark_pushed failed: %s", exc)
        return False
    finally:
        return_connection(conn)


def delete_draft(*, user_id: str, draft_id: str) -> Optional[str]:
    """Delete draft and return its r2_key (so caller can clean up R2)."""
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tally_drafts WHERE id = %s AND user_id = %s RETURNING r2_key",
            (draft_id, user_id),
        )
        row = cur.fetchone()
        conn.commit()
        # row is None → draft not found; row[0] may be None → found but no R2 file
        if row is None:
            return None
        return row[0] or ""  # empty string = deleted, no R2 key to clean up
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("delete_draft failed: %s", exc)
        return None
    finally:
        return_connection(conn)
