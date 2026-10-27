"""
Persistence + lookup for QBO entity aliases.

Each row in `qbo_aliases` is a learned mapping from a normalized name variant
(e.g. "agc asia pacific india pvt. ltd.") to a real QBO entity id. When the CA
picks an existing entity from the Tier-2 fuzzy-suggestion list during invoice /
bill / bank-entry review, that pick is saved as an alias. Future extractions
that produce the same variant text auto-resolve to the same id without any
prompt — the matcher consults aliases ahead of fuzzy.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection

logger = logging.getLogger(__name__)


# Entity types accepted by the table CHECK constraint
ENTITY_TYPES = ("customer", "vendor", "item", "account")


def _normalize(name: str) -> str:
    """Light normalization for alias keys. Lowercases and collapses whitespace.
    Heavier canonicalization (stripping Pvt/Ltd/etc.) lives in qbo_matcher so
    aliases and matcher share semantics — keep them aligned."""
    return " ".join((name or "").lower().split())


def save_alias(
    *,
    user_id: str,
    realm_id: str,
    entity_type: str,
    alias_text: str,
    qbo_entity_id: str,
    qbo_entity_name: str,
) -> bool:
    """Upsert a (normalized alias) → (qbo entity id) mapping.

    Returns True on success, False if the DB is unavailable or the write
    failed. ON CONFLICT is a no-op so repeated saves are idempotent.
    """
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"invalid entity_type: {entity_type!r}")
    alias_norm = _normalize(alias_text)
    if not alias_norm or not qbo_entity_id:
        return False

    conn = get_connection()
    if conn is None:
        logger.error("save_alias skipped — DB unavailable")
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO qbo_aliases
                (user_id, realm_id, entity_type, alias_normalized,
                 qbo_entity_id, qbo_entity_name, created_from_ui)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (user_id, realm_id, entity_type, alias_normalized)
            DO UPDATE SET
                qbo_entity_id   = EXCLUDED.qbo_entity_id,
                qbo_entity_name = EXCLUDED.qbo_entity_name
            """,
            (user_id, realm_id, entity_type, alias_norm, qbo_entity_id, qbo_entity_name),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"save_alias failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        return_connection(conn)


def get_aliases(
    *,
    user_id: str,
    realm_id: str,
    entity_type: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """Return all aliases for the user × realm, keyed by normalized alias text.

    Shape: {alias_normalized: {"qbo_entity_id": ..., "qbo_entity_name": ..., "entity_type": ...}}

    If `entity_type` is omitted, returns the full set scoped only by entity_type
    inside the value — caller filters. If provided, returns only that type.
    """
    conn = get_connection()
    if conn is None:
        logger.warning("get_aliases — DB unavailable, returning empty")
        return {}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if entity_type:
            cur.execute(
                """
                SELECT alias_normalized, qbo_entity_id, qbo_entity_name, entity_type
                FROM qbo_aliases
                WHERE user_id = %s AND realm_id = %s AND entity_type = %s
                """,
                (user_id, realm_id, entity_type),
            )
        else:
            cur.execute(
                """
                SELECT alias_normalized, qbo_entity_id, qbo_entity_name, entity_type
                FROM qbo_aliases
                WHERE user_id = %s AND realm_id = %s
                """,
                (user_id, realm_id),
            )
        rows = cur.fetchall()
        cur.close()
        return {
            r["alias_normalized"]: {
                "qbo_entity_id": r["qbo_entity_id"],
                "qbo_entity_name": r["qbo_entity_name"],
                "entity_type": r["entity_type"],
            }
            for r in rows
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"get_aliases failed: {exc}")
        return {}
    finally:
        return_connection(conn)


def delete_alias(
    *,
    user_id: str,
    realm_id: str,
    entity_type: str,
    alias_text: str,
) -> bool:
    """Remove a single alias. Returns True if a row was deleted."""
    alias_norm = _normalize(alias_text)
    conn = get_connection()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM qbo_aliases
            WHERE user_id = %s AND realm_id = %s
              AND entity_type = %s AND alias_normalized = %s
            """,
            (user_id, realm_id, entity_type, alias_norm),
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        return deleted > 0
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"delete_alias failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        return_connection(conn)
