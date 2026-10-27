"""
Persistence for SAP S/4HANA Cloud chat messages.

A "thread" is a chat session. The frontend opens a new one when the user
clears history; otherwise the latest thread for (user, company_code) is reused
so history survives page reloads.

Mirrors zoho_chat_store with the SAP `company_code` company key.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection

logger = logging.getLogger(__name__)


def _new_thread_id() -> str:
    return uuid.uuid4().hex


def get_or_create_latest_thread(user_id: str, company_code: Optional[str]) -> str:
    """Return the thread id of the most-recently-used chat for (user, company_code),
    or mint a new one if none exists. Used by the chat endpoint when the
    frontend doesn't pass an explicit thread_id."""
    conn = get_connection()
    if conn is None:
        return _new_thread_id()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT thread_id FROM sap_chat_messages
                 WHERE user_id = %s AND company_code = %s
                 ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, company_code or ""),
            )
            row = cur.fetchone()
            if row:
                return row[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"chat thread lookup failed: {exc}")
    finally:
        return_connection(conn)
    return _new_thread_id()


def append_message(
    *,
    user_id: str,
    company_code: Optional[str],
    thread_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one chat message. Silently no-ops on DB failure — the live chat
    stream must never be blocked by persistence problems."""
    if role not in ("user", "assistant"):
        return
    if not content and not metadata:
        return
    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sap_chat_messages
                    (user_id, company_code, thread_id, role, content, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, company_code or "", thread_id, role, content or "", Json(metadata or {})),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"chat append failed: {exc}")
    finally:
        return_connection(conn)


def load_thread(
    *,
    user_id: str,
    company_code: Optional[str],
    thread_id: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Return messages for one thread, oldest first."""
    conn = get_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, role, content, metadata, created_at
                  FROM sap_chat_messages
                 WHERE user_id = %s AND company_code = %s AND thread_id = %s
                 ORDER BY created_at ASC, id ASC
                 LIMIT %s
                """,
                (user_id, company_code or "", thread_id, limit),
            )
            rows = cur.fetchall()
            return [{
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "metadata": r["metadata"] or {},
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            } for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"chat load failed: {exc}")
        return []
    finally:
        return_connection(conn)


def clear_thread(*, user_id: str, company_code: Optional[str], thread_id: str) -> None:
    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sap_chat_messages WHERE user_id = %s AND company_code = %s AND thread_id = %s",
                (user_id, company_code or "", thread_id),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"chat clear failed: {exc}")
    finally:
        return_connection(conn)
