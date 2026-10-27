"""
QuickBooks Online suggestion orchestration.

Wraps the primitive `qbo_matcher` with three capabilities the bare matcher lacks:

  1. Bank narration tokenization — strip payment-rail / bank / digit-ref tokens
     from raw bank-statement descriptions before fuzzy matching, so
     "UPI ZOMATO HDFC 223020100330" resolves to the existing "Zomato" vendor.

  2. Vendor → account memory — once a user has booked a vendor to an expense
     account even once, surface that account as a high-confidence "history"
     suggestion that bypasses fuzzy matching. Confidence scales gently with
     usage so a 5x-confirmed mapping still beats a single fresh one.

  3. Extended mapping history — item description → account and bank
     narration → account memory via `qbo_mapping_history` table.

Public surface:

    suggest_for_purchase(...)              # used by agents/qbo/purchase.py
    suggest_for_bank_entry(...)            # used by agents/qbo/bank_entries.py
    record_match(...)                      # called from controllers after a successful push
    record_item_mapping(...)               # item-level account history
    record_bank_description_mapping(...)   # bank narration → account history

The suggestion shape extends qbo_matcher's return dict with one new method
value, "history", so AISuggestedDropdown can style history hits distinctly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services import bank_narration_tokenizer
from applicationca.services.ledger_matcher import normalize_name
from applicationca.services.qbo_matcher import match_entity, match_account_for_line
from applicationca.services import qbo_mapping_service

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
    user_id: str, realm_id: str, vendor_canonical: str
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
                  FROM qbo_vendor_account_history
                 WHERE user_id = %s
                   AND realm_id = %s
                   AND vendor_name_canonical = %s
                 ORDER BY usage_count DESC, last_used_at DESC
                 LIMIT 1
                """,
                (user_id, realm_id or "", vendor_canonical),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except psycopg2.Error as exc:
        logger.warning(f"vendor_account_history lookup failed: {exc}")
        return None
    finally:
        return_connection(conn)


def _history_suggestion(
    vendor_match: Dict[str, Any],
    history_row: Dict[str, Any],
    account_cards: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Build a matcher-compatible response dict for a history hit. If the
    historical account is no longer present in the QB account list (deleted /
    deactivated), return None so we fall back to fuzzy."""
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
    realm_id: str,
    vendor_match: Dict[str, Any],
    fallback_query: str,
    account_cards: List[Dict[str, Any]],
    llm_async,
    account_aliases=None,
) -> Dict[str, Any]:
    """Return an account suggestion for the given (already-matched) vendor.
    Tries history first, then falls back to qbo_matcher.match_entity against
    the supplied accounts."""
    if vendor_match.get("matched") and (vendor_match.get("confidence") or 0) >= _VENDOR_HIT_MIN_CONFIDENCE:
        vendor_canonical = normalize_name(vendor_match.get("name") or "")
        history_row = _lookup_top_account_for_vendor(user_id, realm_id or "", vendor_canonical)
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
    realm_id: Optional[str],
    extracted_vendor_name: str,
    extracted_vendor_email: Optional[str],
    line_descriptions: List[str],
    vendor_cards: List[Dict[str, Any]],
    account_cards: List[Dict[str, Any]],
    llm_async,
    vendor_aliases=None,
    account_aliases=None,
) -> Dict[str, Any]:
    """Vendor + per-line account suggestions for a purchase bill.

    Returns:
        {
            "vendor_match":   <match dict>,
            "line_matches":   [<match dict>, ...],   # parallel to line_descriptions
        }
    """
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
        lm = await _suggest_account_for_line(
            user_id=user_id,
            realm_id=realm_id or "",
            vendor_match=vendor_match,
            description=desc or "",
            account_cards=account_cards,
            llm_async=llm_async,
            account_aliases=account_aliases,
        )
        line_matches.append(lm)

    return {"vendor_match": vendor_match, "line_matches": line_matches}


async def _suggest_account_for_line(
    *,
    user_id: str,
    realm_id: str,
    vendor_match: Dict[str, Any],
    description: str,
    account_cards: List[Dict[str, Any]],
    llm_async,
    account_aliases=None,
) -> Dict[str, Any]:
    """Pick the expense account for a single bill line, in priority order:

      1. item-description -> account history (most specific learned signal)
      2. vendor -> account history (the vendor's usual expense account)
      3. exact / alias account-name match
      4. LLM semantic categorization (what was bought -> which account)

    Deliberately does NOT fuzzy-match the free-text description against account
    NAMES — that's what produced "description-as-account". match_entity is kept
    only for its alias/exact layers (its fuzzy no longer leaks a name).
    """
    desc = description or ""

    # 0. user-defined mapping rules (highest priority — keyword/pattern/vendor/
    #    amount/composite). No-op when the user has no rules.
    rule = qbo_mapping_service.match_rule(
        user_id, realm_id or "",
        {"description": desc, "vendor_name": vendor_match.get("name")},
        document_type="qb_purchase", account_slot="account",
    )
    if rule and rule.get("target_account_id") and any(
        a.get("list_id") == rule["target_account_id"] for a in account_cards
    ):
        return {
            "matched": True,
            "list_id": rule["target_account_id"],
            "name": rule.get("target_account_name"),
            "confidence": 0.99,
            "method": "mapping_rule",
            "candidate_name": desc,
            "top_candidates": [],
        }

    # 1. item description -> account history
    if desc:
        item_hist = _lookup_mapping_history(user_id, realm_id or "", "item_account", desc)
        if item_hist and any(a.get("list_id") == item_hist["target_list_id"] for a in account_cards):
            usage = int(item_hist.get("usage_count") or 0)
            return {
                "matched": True,
                "list_id": item_hist["target_list_id"],
                "name": item_hist["target_name"],
                "confidence": round(_history_confidence(usage), 3),
                "method": "history",
                "usage_count": usage,
                "candidate_name": desc,
                "top_candidates": [],
            }

    # 2. vendor -> account history
    if vendor_match.get("matched") and (vendor_match.get("confidence") or 0) >= _VENDOR_HIT_MIN_CONFIDENCE:
        vendor_canonical = normalize_name(vendor_match.get("name") or "")
        history_row = _lookup_top_account_for_vendor(user_id, realm_id or "", vendor_canonical)
        if history_row and int(history_row.get("usage_count") or 0) >= _HISTORY_MIN_USES:
            hit = _history_suggestion(vendor_match, history_row, account_cards)
            if hit:
                return hit

    # 3. exact / alias account-name match (match_entity's fuzzy no longer leaks)
    em = await match_entity(
        desc, None, account_cards,
        entity_label="expense account", llm_async=None, aliases=account_aliases,
    )
    if em.get("matched"):
        return em

    # 4. LLM semantic categorization (the real fix for the description-as-account bug)
    return await match_account_for_line(desc, account_cards, llm_async=llm_async)


async def suggest_for_bank_entry(
    *,
    user_id: str,
    realm_id: Optional[str],
    description: str,
    counterparty_cards: List[Dict[str, Any]],
    account_cards: List[Dict[str, Any]],
    counterparty_label: str,
    account_label: str,
    llm_async,
    counterparty_aliases=None,
    account_aliases=None,
) -> Dict[str, Any]:
    """Counterparty + account suggestion for a single bank-statement row.

    For purchase (debit) rows: counterparty_cards = vendors, account_cards = expense accounts.
    For deposit (credit) rows: counterparty_cards = customers, account_cards = income accounts.

    Returns:
        {
            "cleaned":            "ZOMATO",
            "counterparty_match": <match dict>,
            "account_match":      <match dict>,
        }
    """
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

    # 0. user-defined mapping rules apply to bank rows too (parity with Tally).
    rule = qbo_mapping_service.match_rule(
        user_id, realm_id or "",
        {"description": cleaned, "vendor_name": counterparty_match.get("name")},
        document_type="qb_bank", account_slot="account",
    )
    if rule and rule.get("target_account_id") and any(
        a.get("list_id") == rule["target_account_id"] for a in account_cards
    ):
        return {
            "cleaned": cleaned,
            "counterparty_match": counterparty_match,
            "account_match": {
                "matched": True,
                "list_id": rule["target_account_id"],
                "name": rule.get("target_account_name"),
                "confidence": 0.99,
                "method": "mapping_rule",
            },
        }

    # Vendors use vendor→account history. For credit entries (customers),
    # check bank_description_ledger history before falling through to fuzzy.
    use_vendor_history = counterparty_label == "vendor"
    if use_vendor_history:
        account_match = await _suggest_account_for_vendor(
            user_id=user_id,
            realm_id=realm_id or "",
            vendor_match=counterparty_match,
            fallback_query=cleaned,
            account_cards=account_cards,
            llm_async=llm_async,
            account_aliases=account_aliases,
        )
    else:
        # Check bank_description_ledger history for credit entries
        desc_history = _lookup_mapping_history(
            user_id, realm_id or "", "bank_description_ledger", cleaned,
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
    realm_id: Optional[str],
    payload: Dict[str, Any],
) -> None:
    """Extract vendor + per-line accounts from a QBO Bill payload and record
    each (vendor, account) pair. Also records item description → account
    mappings for line items that have a description. Used by the /push-bill
    controller after a successful create_bill."""
    if not isinstance(payload, dict):
        return
    vendor_ref = payload.get("VendorRef") or {}
    vendor_name = vendor_ref.get("name")
    vendor_list_id = vendor_ref.get("value")
    if not vendor_name:
        return
    for line in payload.get("Line") or []:
        detail = line.get("AccountBasedExpenseLineDetail") or {}
        acct_ref = detail.get("AccountRef") or {}
        account_id = acct_ref.get("value")
        account_name = acct_ref.get("name")
        if not account_id or not account_name:
            continue
        record_match(
            user_id=user_id, realm_id=realm_id,
            vendor_name=vendor_name, vendor_list_id=vendor_list_id,
            account_list_id=account_id, account_name=account_name,
        )
        # Record item description → account mapping
        description = (line.get("Description") or "").strip()
        if description:
            record_item_mapping(
                user_id=user_id, realm_id=realm_id,
                item_description=description,
                account_list_id=account_id, account_name=account_name,
            )


def record_from_bank_entry_payload(
    *,
    user_id: str,
    realm_id: Optional[str],
    entry_type: str,
    payload: Dict[str, Any],
) -> None:
    """Extract entity + account from a QBO Purchase or Deposit payload and
    record mappings. For purchases: vendor→account history. For deposits:
    bank description→account history (so credit entries also build memory)."""
    if not isinstance(payload, dict):
        return

    if entry_type == "purchase":
        entity_ref = payload.get("EntityRef") or {}
        if entity_ref.get("type") != "Vendor":
            return
        vendor_name = entity_ref.get("name")
        vendor_list_id = entity_ref.get("value")
        if not vendor_name:
            return
        for line in payload.get("Line") or []:
            detail = line.get("AccountBasedExpenseLineDetail") or {}
            acct_ref = detail.get("AccountRef") or {}
            account_id = acct_ref.get("value")
            account_name = acct_ref.get("name")
            if not account_id or not account_name:
                continue
            record_match(
                user_id=user_id, realm_id=realm_id,
                vendor_name=vendor_name, vendor_list_id=vendor_list_id,
                account_list_id=account_id, account_name=account_name,
            )

    # For both purchase and deposit: record bank description → account mapping
    # if the original bank description is available in metadata
    bank_description = (payload.get("_bank_description") or "").strip()
    if bank_description:
        for line in payload.get("Line") or []:
            detail = (
                line.get("AccountBasedExpenseLineDetail")
                or line.get("DepositLineDetail")
                or {}
            )
            acct_ref = detail.get("AccountRef") or {}
            account_id = acct_ref.get("value")
            account_name = acct_ref.get("name")
            if account_id and account_name:
                record_bank_description_mapping(
                    user_id=user_id, realm_id=realm_id,
                    description=bank_description,
                    account_list_id=account_id, account_name=account_name,
                )
                break


def record_match(
    *,
    user_id: str,
    realm_id: Optional[str],
    vendor_name: str,
    vendor_list_id: Optional[str],
    account_list_id: str,
    account_name: str,
) -> None:
    """Upsert a vendor→account assignment. Called from the QB controller after
    a successful push so the next bill for the same vendor pre-fills the
    same account. Silently no-ops on missing inputs or DB errors — we never
    want to break the push path because history persistence failed."""
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
                INSERT INTO qbo_vendor_account_history
                    (user_id, realm_id, vendor_name_canonical, vendor_list_id,
                     account_list_id, account_name, usage_count, last_used_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, realm_id, vendor_name_canonical, account_list_id)
                DO UPDATE SET
                    usage_count   = qbo_vendor_account_history.usage_count + 1,
                    last_used_at  = CURRENT_TIMESTAMP,
                    vendor_list_id = COALESCE(EXCLUDED.vendor_list_id, qbo_vendor_account_history.vendor_list_id),
                    account_name  = EXCLUDED.account_name
                """,
                (
                    user_id, realm_id or "", canonical,
                    vendor_list_id, account_list_id, account_name,
                ),
            )
        conn.commit()
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"vendor_account_history upsert failed: {exc}")
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------
# Extended mapping history (qbo_mapping_history table)
# ---------------------------------------------------------------------------


def _lookup_mapping_history(
    user_id: str,
    realm_id: str,
    mapping_type: str,
    source_key: str,
) -> Optional[Dict[str, Any]]:
    """Look up the top match from qbo_mapping_history for item or bank
    description mappings."""
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
                  FROM qbo_mapping_history
                 WHERE user_id = %s
                   AND realm_id = %s
                   AND mapping_type = %s
                   AND source_identifier = %s
                 ORDER BY usage_count DESC, last_used_at DESC
                 LIMIT 1
                """,
                (user_id, realm_id or "", mapping_type, canonical),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except psycopg2.Error as exc:
        logger.warning("qbo_mapping_history lookup failed: %s", exc)
        return None
    finally:
        return_connection(conn)


def _upsert_mapping_history(
    user_id: str,
    realm_id: str,
    mapping_type: str,
    source_key: str,
    target_list_id: str,
    target_name: str,
) -> None:
    """Upsert a row into qbo_mapping_history. Silent no-op on errors."""
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
                INSERT INTO qbo_mapping_history
                    (user_id, realm_id, mapping_type, source_identifier,
                     target_list_id, target_name, usage_count, last_used_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, realm_id, mapping_type, source_identifier, target_list_id)
                DO UPDATE SET
                    usage_count  = qbo_mapping_history.usage_count + 1,
                    last_used_at = CURRENT_TIMESTAMP,
                    target_name  = EXCLUDED.target_name
                """,
                (user_id, realm_id or "", mapping_type, canonical,
                 target_list_id, target_name),
            )
        conn.commit()
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("qbo_mapping_history upsert failed: %s", exc)
    finally:
        return_connection(conn)


def record_item_mapping(
    *,
    user_id: str,
    realm_id: Optional[str],
    item_description: str,
    account_list_id: str,
    account_name: str,
) -> None:
    """Record an item description → expense account mapping."""
    _upsert_mapping_history(
        user_id, realm_id or "", "item_account",
        item_description, account_list_id, account_name,
    )


def record_suggestion_feedback(
    *,
    user_id: str,
    realm_id: Optional[str],
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
                INSERT INTO qbo_suggestion_feedback
                    (user_id, realm_id, slot, context_text, vendor_name, amount,
                     suggested_id, suggested_name, suggested_confidence, suggested_method,
                     picked_id, picked_name, was_override)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    user_id, realm_id or "", slot, context_text, vendor_name, amount,
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
        logger.warning("qbo_suggestion_feedback insert failed: %s", exc)
    finally:
        return_connection(conn)


def record_bank_description_mapping(
    *,
    user_id: str,
    realm_id: Optional[str],
    description: str,
    account_list_id: str,
    account_name: str,
) -> None:
    """Record a bank narration → account mapping."""
    tokens = bank_narration_tokenizer.tokenize(description or "")
    cleaned = tokens["cleaned"] or description or ""
    if not cleaned:
        return
    _upsert_mapping_history(
        user_id, realm_id or "", "bank_description_ledger",
        cleaned, account_list_id, account_name,
    )
