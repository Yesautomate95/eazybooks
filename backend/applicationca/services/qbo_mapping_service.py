"""
QuickBooks mapping-rules engine (Phase 3).

User-authorable rules that map a transaction to a target QBO account/party,
the QB analog of Tally's mapping_service. CRUD + apply live here with their own
DB access (like qbo_suggestion_service). Rule MATCHING reuses Tally's proven,
ERP-agnostic evaluator (MappingService._rule_matches) so keyword / pattern /
vendor / amount_range / composite (AND-OR) rules behave identically.

Additive: with no rules, apply_rules is a no-op and nothing changes.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.mapping_service import MappingService

logger = logging.getLogger(__name__)

# A single throwaway instance gives us Tally's pure (rule, txn) matchers.
_EVALUATOR = MappingService(user_id="_qbo_eval", company_name="_qbo")

_JSON_FIELDS = ("keywords", "conditions")
_RETURN_COLS = (
    "rule_id, user_id, realm_id, rule_name, rule_type, document_type, account_slot, "
    "target_account_id, target_account_name, keywords, pattern, vendor_name, "
    "amount_min, amount_max, conditions, priority, match_count, is_active, "
    "created_at, updated_at"
)


def list_rules(
    user_id: str, realm_id: str, *,
    document_type: Optional[str] = None,
    account_slot: Optional[str] = None,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    try:
        clauses = ["user_id = %s", "realm_id = %s"]
        params: List[Any] = [user_id, realm_id or ""]
        if document_type:
            clauses.append("document_type = %s")
            params.append(document_type)
        if account_slot:
            # NULL slot means "either"
            clauses.append("(account_slot = %s OR account_slot IS NULL)")
            params.append(account_slot)
        if active_only:
            clauses.append("is_active = TRUE")
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT {_RETURN_COLS} FROM qbo_mapping_rules "
                f"WHERE {' AND '.join(clauses)} "
                f"ORDER BY priority DESC, match_count DESC, rule_id DESC",
                params,
            )
            return [dict(r) for r in cur.fetchall()]
    except psycopg2.Error as exc:
        logger.warning("qbo_mapping_rules list failed: %s", exc)
        return []
    finally:
        return_connection(conn)


def create_rule(user_id: str, realm_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                INSERT INTO qbo_mapping_rules
                    (user_id, realm_id, rule_name, rule_type, document_type, account_slot,
                     target_account_id, target_account_name, keywords, pattern, vendor_name,
                     amount_min, amount_max, conditions, priority, is_active)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING {_RETURN_COLS}
                """,
                (
                    user_id, realm_id or "",
                    data.get("rule_name"), data.get("rule_type") or "keyword",
                    data.get("document_type"), data.get("account_slot"),
                    data.get("target_account_id"), data.get("target_account_name"),
                    json.dumps(data.get("keywords")) if data.get("keywords") is not None else None,
                    data.get("pattern"), data.get("vendor_name"),
                    data.get("amount_min"), data.get("amount_max"),
                    json.dumps(data.get("conditions")) if data.get("conditions") is not None else None,
                    int(data.get("priority") or 0),
                    data.get("is_active", True),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("qbo_mapping_rules create failed: %s", exc)
        return None
    finally:
        return_connection(conn)


def update_rule(user_id: str, rule_id: int, data: Dict[str, Any]) -> bool:
    allowed = (
        "rule_name", "rule_type", "document_type", "account_slot",
        "target_account_id", "target_account_name", "keywords", "pattern",
        "vendor_name", "amount_min", "amount_max", "conditions", "priority", "is_active",
    )
    sets, params = [], []
    for k in allowed:
        if k in data:
            sets.append(f"{k} = %s")
            v = data[k]
            params.append(json.dumps(v) if k in _JSON_FIELDS and v is not None else v)
    if not sets:
        return False
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.extend([user_id, rule_id])
    conn = get_connection()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE qbo_mapping_rules SET {', '.join(sets)} "
                f"WHERE user_id = %s AND rule_id = %s",
                params,
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("qbo_mapping_rules update failed: %s", exc)
        return False
    finally:
        return_connection(conn)


def delete_rule(user_id: str, rule_id: int) -> bool:
    conn = get_connection()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM qbo_mapping_rules WHERE user_id = %s AND rule_id = %s",
                (user_id, rule_id),
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("qbo_mapping_rules delete failed: %s", exc)
        return False
    finally:
        return_connection(conn)


def record_rule_hit(rule_id: int) -> None:
    """Bump a rule's match_count — call after a rule-suggested value is actually
    pushed (not on every suggest, which would corrupt the ranking)."""
    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE qbo_mapping_rules SET match_count = match_count + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE rule_id = %s",
                (rule_id,),
            )
        conn.commit()
    except psycopg2.Error:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        return_connection(conn)


def match_rule(
    user_id: str, realm_id: str, transaction: Dict[str, Any], *,
    document_type: Optional[str] = None, account_slot: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the first (highest-priority) rule that matches `transaction`, with
    its target, or None. Bumps the rule's match_count. No-op when no rules."""
    rules = list_rules(user_id, realm_id, document_type=document_type, account_slot=account_slot)
    for rule in rules:
        try:
            if _EVALUATOR._rule_matches(rule, transaction):  # noqa: SLF001 — reuse pure matcher
                # NOTE: match_count is bumped on actual push (record_rule_hit),
                # NOT here — incrementing on every suggest/re-extract would
                # inflate the count and corrupt the priority ranking.
                return rule
        except Exception as exc:  # noqa: BLE001 — a bad rule must never break suggestions
            logger.warning("qbo rule %s eval failed: %s", rule.get("rule_id"), exc)
    return None


def preview_rule(user_id: str, realm_id: str, rule: Dict[str, Any], transactions: List[Dict[str, Any]]) -> List[int]:
    """Return the indices of `transactions` a transient (unsaved) rule matches —
    powers the 'preview matches' UI before a rule is created."""
    out: List[int] = []
    for i, txn in enumerate(transactions or []):
        try:
            if _EVALUATOR._rule_matches(rule, txn):  # noqa: SLF001
                out.append(i)
        except Exception:  # noqa: BLE001
            continue
    return out
