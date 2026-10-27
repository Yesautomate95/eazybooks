"""
Persistence + lookup for Zoho Books entity aliases.

Each row in `zoho_aliases` is a learned mapping from a normalized name variant
to a real Zoho entity id (contact_id / item_id / account_id). When the CA picks
an existing entity from the Tier-2 fuzzy-suggestion list during invoice / bill /
bank-entry review, that pick is saved as an alias. Future extractions that
produce the same variant text auto-resolve to the same id without any prompt —
the matcher consults aliases ahead of fuzzy.

Mirrors qbo_aliases with the Zoho `organization_id` company key. The DB stores
platform-neutral `entity_id` / `entity_name` columns; `get_aliases` returns them
under the `qbo_entity_id` / `qbo_entity_name` keys that the *shared* qbo_matcher
consumes (the matcher key name is a fixed contract, not QBO-specific data).
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection

logger = logging.getLogger(__name__)


ENTITY_TYPES = ("customer", "vendor", "item", "account")


def _normalize(name: str) -> str:
    """Light normalization for alias keys — lowercases and collapses whitespace.
    Heavier canonicalization lives in qbo_matcher; keep them aligned."""
    return " ".join((name or "").lower().split())


def save_alias(
    *,
    user_id: str,
    organization_id: str,
    entity_type: str,
    alias_text: str,
    entity_id: str,
    entity_name: str,
) -> bool:
    """Upsert a (normalized alias) → (Zoho entity id) mapping. Idempotent."""
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"invalid entity_type: {entity_type!r}")
    alias_norm = _normalize(alias_text)
    if not alias_norm or not entity_id:
        return False

    conn = get_connection()
    if conn is None:
        logger.error("save_alias skipped — DB unavailable")
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO zoho_aliases
                (user_id, organization_id, entity_type, alias_normalized,
                 entity_id, entity_name, created_from_ui)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (user_id, organization_id, entity_type, alias_normalized)
            DO UPDATE SET
                entity_id   = EXCLUDED.entity_id,
                entity_name = EXCLUDED.entity_name
            """,
            (user_id, organization_id, entity_type, alias_norm, entity_id, entity_name),
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
    organization_id: str,
    entity_type: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """Return aliases keyed by normalized alias text, in the shape the shared
    matcher expects: {alias_normalized: {qbo_entity_id, qbo_entity_name, entity_type}}.
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
                SELECT alias_normalized, entity_id, entity_name, entity_type
                FROM zoho_aliases
                WHERE user_id = %s AND organization_id = %s AND entity_type = %s
                """,
                (user_id, organization_id, entity_type),
            )
        else:
            cur.execute(
                """
                SELECT alias_normalized, entity_id, entity_name, entity_type
                FROM zoho_aliases
                WHERE user_id = %s AND organization_id = %s
                """,
                (user_id, organization_id),
            )
        rows = cur.fetchall()
        cur.close()
        return {
            r["alias_normalized"]: {
                "qbo_entity_id": r["entity_id"],
                "qbo_entity_name": r["entity_name"],
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
    organization_id: str,
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
            DELETE FROM zoho_aliases
            WHERE user_id = %s AND organization_id = %s
              AND entity_type = %s AND alias_normalized = %s
            """,
            (user_id, organization_id, entity_type, alias_norm),
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
