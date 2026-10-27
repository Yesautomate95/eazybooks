"""
Per-user drafts for in-progress QuickBooks work (purchase / sales / bank-entries).

A draft is one *session* of work — typically the contents of the bills queue,
the bank-entries table, or the sales invoice queue — stored as a JSONB blob so
the frontend can reopen and continue.

Source PDFs are best-effort uploaded to Cloudflare R2 by the extract endpoint
(`_upload_doc_to_r2` in qb_controller); their pointers (file_url / file_name /
r2_key) ride along the draft so a reopened batch can re-fetch a signed URL
via /ca/qb/draft-file-url and re-render the preview pane. For multi-invoice
batches the per-invoice r2_key lives inside the JSONB payload — these top-level
columns hold the "primary" file's pointers so the draft picker can preview the
first PDF without parsing the payload.
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
#   pushing → push to QuickBooks in progress
#   pushed  → successfully pushed
#   error   → push failed
# (uploaded/extracting/extracted exist for parity but cloud extraction is
#  client-driven, so QB drafts are created directly in 'draft'.)
VALID_STATUSES = ("uploaded", "extracting", "extracted", "draft", "pushing", "pushed", "error")


def upsert(
    *,
    user_id: str,
    realm_id: str,
    flow: str,
    payload: Dict[str, Any],
    title: Optional[str] = None,
    draft_id: Optional[str] = None,
    status: str = "draft",
    file_url: Optional[str] = None,
    file_name: Optional[str] = None,
    r2_key: Optional[str] = None,
) -> Optional[str]:
    """Insert a new draft or update an existing one (when draft_id is passed).
    Returns the draft id, or None if persistence failed. file_url/file_name/r2_key
    are optional pointers to the source document in R2 — see module docstring."""
    if flow not in VALID_FLOWS:
        raise ValueError(f"invalid flow: {flow!r}")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    conn = get_connection()
    if conn is None:
        logger.error("qbo_drafts upsert skipped — DB unavailable")
        return None
    try:
        cur = conn.cursor()
        if draft_id:
            # COALESCE so a save without R2 metadata (e.g. an edit after the
            # original extract uploaded the PDF) keeps the existing pointer.
            cur.execute(
                """
                UPDATE qbo_drafts
                   SET title = %s,
                       payload = %s,
                       status = %s,
                       file_url = COALESCE(%s, file_url),
                       file_name = COALESCE(%s, file_name),
                       r2_key = COALESCE(%s, r2_key),
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s AND user_id = %s AND realm_id = %s AND flow = %s
                RETURNING id
                """,
                (title, Json(payload), status, file_url, file_name, r2_key,
                 draft_id, user_id, realm_id, flow),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return str(row[0])
            # No row matched — fall through to insert so a stale id from the
            # frontend doesn't silently lose the user's work.
        cur.execute(
            """
            INSERT INTO qbo_drafts (user_id, realm_id, flow, title, payload, status,
                                    file_url, file_name, r2_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, realm_id, flow, title, Json(payload), status,
             file_url, file_name, r2_key),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return str(new_id)
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception(f"qbo_drafts upsert failed: {exc}")
        return None
    finally:
        return_connection(conn)


def list_drafts(
    *,
    user_id: str,
    realm_id: Optional[str],
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
        if realm_id:
            clauses.append("realm_id = %s")
            params.append(realm_id)
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
            SELECT id, realm_id, flow, title, status, created_at, updated_at,
                   pushed_at, error_message, file_url, file_name, r2_key
              FROM qbo_drafts
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
            SELECT id, realm_id, flow, title, payload, status, created_at, updated_at,
                   pushed_at, error_message, file_url, file_name, r2_key
              FROM qbo_drafts
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
            UPDATE qbo_drafts
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
        logger.exception("qbo_drafts update_status failed: %s", exc)
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
                UPDATE qbo_drafts
                   SET payload = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s AND user_id = %s
                """,
                (Json(payload), draft_id, user_id),
            )
        else:
            cur.execute(
                """
                UPDATE qbo_drafts
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
        logger.exception("qbo_drafts update_payload failed: %s", exc)
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
            UPDATE qbo_drafts
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
        logger.exception("qbo_drafts mark_pushed failed: %s", exc)
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
            "DELETE FROM qbo_drafts WHERE id = %s AND user_id = %s",
            (draft_id, user_id),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        return_connection(conn)
