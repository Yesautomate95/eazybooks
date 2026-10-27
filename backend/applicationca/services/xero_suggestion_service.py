"""
Xero suggestion orchestration.

Mirrors qbo_suggestion_service / zoho_suggestion_service. Wraps the shared
`qbo_matcher` with:

  1. Bank narration tokenization (shared bank_narration_tokenizer).
  2. Vendor → account memory (xero_vendor_account_history).
  3. Extended mapping history — item description → account and bank narration →
     account (xero_mapping_history).

Differences vs Zoho:
  * `tenant_id` company key instead of organization_id.
  * Xero create payloads (Invoices / BankTransactions) carry display-only fields
    inline with a leading underscore — line items reference accounts by
    AccountCode (a string Code), so the AccountID + name ride along as
    `_account_id` / `_account_name` for history. The connector strips these
    underscore-prefixed keys (plus vendor_name/customer_name) before POSTing, but
    the controller passes the un-stripped payload here for history recording.

Public surface mirrors the Zoho service: suggest_for_purchase, suggest_for_bank_entry,
record_match, record_from_bill_payload, record_from_bank_entry_payload,
record_item_mapping, record_bank_description_mapping.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services import bank_narration_tokenizer
from applicationca.services.ledger_matcher import normalize_name
from applicationca.services.qbo_matcher import match_entity

logger = logging.getLogger(__name__)


_HISTORY_MIN_USES = 1
_HISTORY_BASE_CONFIDENCE = 0.88
_HISTORY_PER_USE_BONUS = 0.02
_HISTORY_MAX_CONFIDENCE = 0.99
_VENDOR_HIT_MIN_CONFIDENCE = 0.85


def _history_confidence(usage_count: int) -> float:
    return min(
        _HISTORY_BASE_CONFIDENCE + usage_count * _HISTORY_PER_USE_BONUS,
        _HISTORY_MAX_CONFIDENCE,
    )


def _lookup_top_account_for_vendor(
    user_id: str, tenant_id: str, vendor_canonical: str
) -> Optional[Dict[str, Any]]:
    if not vendor_canonical:
        return None
    conn = get_connection()
    if conn is None:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT account_list_id, account_name, usage_count
                  FROM xero_vendor_account_history
                 WHERE user_id = %s
                   AND tenant_id = %s
                   AND vendor_name_canonical = %s
                 ORDER BY usage_count DESC, last_used_at DESC
                 LIMIT 1
                """,
                (user_id, tenant_id or "", vendor_canonical),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except psycopg2.Error as exc:
        logger.warning(f"xero_vendor_account_history lookup failed: {exc}")
        return None
    finally:
        return_connection(conn)


def _history_suggestion(
    vendor_match: Dict[str, Any],
    history_row: Dict[str, Any],
    account_cards: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    account_id = history_row["account_list_id"]
    if not any(a.get("list_id") == account_id for a in account_cards):
        return None
    usage_count = int(history_row.get("usage_count") or 0)
    return {
        "matched": True,
        "list_id": account_id,
        "name": history_row["account_name"],
        "confidence": round(_history_confidence(usage_count), 3),
        "method": "history",
        "candidate_name": vendor_match.get("name"),
        "usage_count": usage_count,
    }


async def _suggest_account_for_vendor(
    *,
    user_id: str,
    tenant_id: str,
    vendor_match: Dict[str, Any],
    fallback_query: str,
    account_cards: List[Dict[str, Any]],
    llm_async,
    account_aliases=None,
) -> Dict[str, Any]:
    if vendor_match.get("matched") and (vendor_match.get("confidence") or 0) >= _VENDOR_HIT_MIN_CONFIDENCE:
        vendor_canonical = normalize_name(vendor_match.get("name") or "")
        history_row = _lookup_top_account_for_vendor(user_id, tenant_id or "", vendor_canonical)
        if history_row and int(history_row.get("usage_count") or 0) >= _HISTORY_MIN_USES:
            hit = _history_suggestion(vendor_match, history_row, account_cards)
            if hit:
                return hit

    return await match_entity(
        fallback_query,
        None,
        account_cards,
        entity_label="expense account",
        llm_async=llm_async,
        aliases=account_aliases,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def suggest_for_purchase(
    *,
    user_id: str,
    tenant_id: Optional[str],
    extracted_vendor_name: str,
    extracted_vendor_email: Optional[str],
    line_descriptions: List[str],
    vendor_cards: List[Dict[str, Any]],
    account_cards: List[Dict[str, Any]],
    llm_async,
    vendor_aliases=None,
    account_aliases=None,
) -> Dict[str, Any]:
    vendor_match = await match_entity(
        extracted_vendor_name,
        extracted_vendor_email,
        vendor_cards,
        entity_label="vendor",
        llm_async=llm_async,
        aliases=vendor_aliases,
    )

    line_matches: List[Dict[str, Any]] = []
    for desc in line_descriptions:
        lm = await _suggest_account_for_vendor(
            user_id=user_id,
            tenant_id=tenant_id or "",
            vendor_match=vendor_match,
            fallback_query=desc or "",
            account_cards=account_cards,
            llm_async=llm_async,
            account_aliases=account_aliases,
        )
        line_matches.append(lm)

    return {"vendor_match": vendor_match, "line_matches": line_matches}


async def suggest_for_bank_entry(
    *,
    user_id: str,
    tenant_id: Optional[str],
    description: str,
    counterparty_cards: List[Dict[str, Any]],
    account_cards: List[Dict[str, Any]],
    counterparty_label: str,
    account_label: str,
    llm_async,
    counterparty_aliases=None,
    account_aliases=None,
) -> Dict[str, Any]:
    tokens = bank_narration_tokenizer.tokenize(description or "")
    cleaned = tokens["cleaned"] or (description or "")

    counterparty_match = await match_entity(
        cleaned,
        None,
        counterparty_cards,
        entity_label=counterparty_label,
        llm_async=llm_async,
        aliases=counterparty_aliases,
    )

    use_vendor_history = counterparty_label == "vendor"
    if use_vendor_history:
        account_match = await _suggest_account_for_vendor(
            user_id=user_id,
            tenant_id=tenant_id or "",
            vendor_match=counterparty_match,
            fallback_query=cleaned,
            account_cards=account_cards,
            llm_async=llm_async,
            account_aliases=account_aliases,
        )
    else:
        desc_history = _lookup_mapping_history(
            user_id, tenant_id or "", "bank_description_ledger", cleaned,
        )
        if desc_history:
            acct_id = desc_history["target_list_id"]
            if any(a.get("list_id") == acct_id for a in account_cards):
                usage = int(desc_history.get("usage_count") or 0)
                account_match = {
                    "matched": True,
                    "list_id": acct_id,
                    "name": desc_history["target_name"],
                    "confidence": round(_history_confidence(usage), 3),
                    "method": "history",
                    "usage_count": usage,
                }
            else:
                account_match = await match_entity(
                    cleaned, None, account_cards,
                    entity_label=account_label, llm_async=llm_async,
                    aliases=account_aliases,
                )
        else:
            account_match = await match_entity(
                cleaned, None, account_cards,
                entity_label=account_label, llm_async=llm_async,
                aliases=account_aliases,
            )

    return {
        "cleaned": cleaned,
        "counterparty_match": counterparty_match,
        "account_match": account_match,
    }


def record_from_bill_payload(
    *,
    user_id: str,
    tenant_id: Optional[str],
    payload: Dict[str, Any],
) -> None:
    """Record vendor→account + item→account history from a Xero Bill payload
    (an Invoice with Type=ACCPAY). Reads display names carried inline on the
    payload (vendor_name top-level; LineItems[]._account_id / ._account_name) —
    Xero lines reference the account by AccountCode, so the AccountID + name ride
    along under underscore keys. The connector strips those before POSTing, but
    the controller passes the un-stripped payload here."""
    if not isinstance(payload, dict):
        return
    vendor_name = payload.get("vendor_name")
    contact = payload.get("Contact") or {}
    vendor_list_id = contact.get("ContactID") if isinstance(contact, dict) else None
    if not vendor_name:
        return
    for line in payload.get("LineItems") or []:
        account_id = line.get("_account_id")
        account_name = line.get("_account_name")
        if not account_id or not account_name:
            continue
        record_match(
            user_id=user_id, tenant_id=tenant_id,
            vendor_name=vendor_name, vendor_list_id=vendor_list_id,
            account_list_id=account_id, account_name=account_name,
        )
        description = (line.get("Description") or "").strip()
        if description:
            record_item_mapping(
                user_id=user_id, tenant_id=tenant_id,
                item_description=description,
                account_list_id=account_id, account_name=account_name,
            )


def record_from_bank_entry_payload(
    *,
    user_id: str,
    tenant_id: Optional[str],
    entry_type: str,
    payload: Dict[str, Any],
) -> None:
    """Record entity + account history from a Xero bank-entry payload
    (BankTransaction), reading display names carried inline on the payload.
    `bank_description` (the raw statement narration) is attached by the
    bank-entries agent for narration→account memory."""
    if not isinstance(payload, dict):
        return
    lines = payload.get("LineItems") or []

    if entry_type in ("purchase", "expense", "spend", "vendor_payment"):
        vendor_name = payload.get("vendor_name") or payload.get("customer_name")
        contact = payload.get("Contact") or {}
        vendor_list_id = contact.get("ContactID") if isinstance(contact, dict) else None
        if vendor_name:
            for line in lines:
                account_id = line.get("_account_id")
                account_name = line.get("_account_name")
                if not account_id or not account_name:
                    continue
                record_match(
                    user_id=user_id, tenant_id=tenant_id,
                    vendor_name=vendor_name, vendor_list_id=vendor_list_id,
                    account_list_id=account_id, account_name=account_name,
                )

    bank_description = (payload.get("bank_description") or "").strip()
    if bank_description:
        for line in lines:
            account_id = line.get("_account_id")
            account_name = line.get("_account_name")
            if account_id and account_name:
                record_bank_description_mapping(
                    user_id=user_id, tenant_id=tenant_id,
                    description=bank_description,
                    account_list_id=account_id, account_name=account_name,
                )
                break


def record_match(
    *,
    user_id: str,
    tenant_id: Optional[str],
    vendor_name: str,
    vendor_list_id: Optional[str],
    account_list_id: str,
    account_name: str,
) -> None:
    """Upsert a vendor→account assignment. Silent no-op on missing inputs / DB
    errors — never break the push path because history persistence failed."""
    if not user_id or not vendor_name or not account_list_id:
        return
    canonical = normalize_name(vendor_name)
    if not canonical:
        return

    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO xero_vendor_account_history
                    (user_id, tenant_id, vendor_name_canonical, vendor_list_id,
                     account_list_id, account_name, usage_count, last_used_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, tenant_id, vendor_name_canonical, account_list_id)
                DO UPDATE SET
                    usage_count    = xero_vendor_account_history.usage_count + 1,
                    last_used_at   = CURRENT_TIMESTAMP,
                    vendor_list_id = COALESCE(EXCLUDED.vendor_list_id, xero_vendor_account_history.vendor_list_id),
                    account_name   = EXCLUDED.account_name
                """,
                (
                    user_id, tenant_id or "", canonical,
                    vendor_list_id, account_list_id, account_name,
                ),
            )
        conn.commit()
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"xero_vendor_account_history upsert failed: {exc}")
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------
# Extended mapping history (xero_mapping_history table)
# ---------------------------------------------------------------------------


def _lookup_mapping_history(
    user_id: str,
    tenant_id: str,
    mapping_type: str,
    source_key: str,
) -> Optional[Dict[str, Any]]:
    if not source_key:
        return None
    canonical = normalize_name(source_key)
    if not canonical:
        return None
    conn = get_connection()
    if conn is None:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT target_list_id, target_name, usage_count
                  FROM xero_mapping_history
                 WHERE user_id = %s
                   AND tenant_id = %s
                   AND mapping_type = %s
                   AND source_identifier = %s
                 ORDER BY usage_count DESC, last_used_at DESC
                 LIMIT 1
                """,
                (user_id, tenant_id or "", mapping_type, canonical),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except psycopg2.Error as exc:
        logger.warning("xero_mapping_history lookup failed: %s", exc)
        return None
    finally:
        return_connection(conn)


def _upsert_mapping_history(
    user_id: str,
    tenant_id: str,
    mapping_type: str,
    source_key: str,
    target_list_id: str,
    target_name: str,
) -> None:
    if not user_id or not source_key or not target_list_id:
        return
    canonical = normalize_name(source_key)
    if not canonical:
        return
    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO xero_mapping_history
                    (user_id, tenant_id, mapping_type, source_identifier,
                     target_list_id, target_name, usage_count, last_used_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, tenant_id, mapping_type, source_identifier, target_list_id)
                DO UPDATE SET
                    usage_count  = xero_mapping_history.usage_count + 1,
                    last_used_at = CURRENT_TIMESTAMP,
                    target_name  = EXCLUDED.target_name
                """,
                (user_id, tenant_id or "", mapping_type, canonical,
                 target_list_id, target_name),
            )
        conn.commit()
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("xero_mapping_history upsert failed: %s", exc)
    finally:
        return_connection(conn)


def record_item_mapping(
    *,
    user_id: str,
    tenant_id: Optional[str],
    item_description: str,
    account_list_id: str,
    account_name: str,
) -> None:
    _upsert_mapping_history(
        user_id, tenant_id or "", "item_account",
        item_description, account_list_id, account_name,
    )


def record_bank_description_mapping(
    *,
    user_id: str,
    tenant_id: Optional[str],
    description: str,
    account_list_id: str,
    account_name: str,
) -> None:
    tokens = bank_narration_tokenizer.tokenize(description or "")
    cleaned = tokens["cleaned"] or description or ""
    if not cleaned:
        return
    _upsert_mapping_history(
        user_id, tenant_id or "", "bank_description_ledger",
        cleaned, account_list_id, account_name,
    )


def record_suggestion_feedback(
    *,
    user_id: str,
    tenant_id: Optional[str],
    slot: str,
    context_text: Optional[str] = None,
    vendor_name: Optional[str] = None,
    amount: Optional[float] = None,
    suggested_id: Optional[str] = None,
    suggested_name: Optional[str] = None,
    suggested_confidence: Optional[float] = None,
    suggested_method: Optional[str] = None,
    picked_id: Optional[str] = None,
    picked_name: Optional[str] = None,
) -> None:
    """Record a suggestion outcome (Phase 4). was_override is derived from
    whether the picked target differs from the AI suggestion. Silent no-op on
    errors — feedback must never break the push/edit path."""
    if not user_id:
        return
    # An override = the AI suggested something and the user picked a DIFFERENT
    # target. The AI abstaining (no suggestion) and the user filling it in is
    # NOT an override — that would over-count failures in the learning signal.
    was_override = bool(suggested_id and picked_id and str(suggested_id) != str(picked_id))
    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO xero_suggestion_feedback
                    (user_id, tenant_id, slot, context_text, vendor_name, amount,
                     suggested_id, suggested_name, suggested_confidence, suggested_method,
                     picked_id, picked_name, was_override)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    user_id, tenant_id or "", slot, context_text, vendor_name, amount,
                    suggested_id, suggested_name, suggested_confidence, suggested_method,
                    picked_id, picked_name, was_override,
                ),
            )
        conn.commit()
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("xero_suggestion_feedback insert failed: %s", exc)
    finally:
        return_connection(conn)
