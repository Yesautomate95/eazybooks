"""
Tally transaction history — historical-pattern suggestion engine.

Records every mapping decision when a user pushes a transaction to Tally.
On the next suggestion request for the same vendor, returns the top-ranked
historical mapping(s) using a deterministic tier-based scorer (GSTIN exact
match → frequency dominance → recency tiebreak).

Public surface:

    lookup_history(...)            # back-compat: returns top-1 raw row
    lookup_history_ranked(...)     # returns ranked top-N with breakdowns
    record_history(...)            # low-level upsert for a single mapping
    record_correction(...)         # records a user override into feedback table
    record_from_push_payload(...)  # called from tally_controller after a push
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.ledger_matcher import normalize_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public confidence range. The tier-based scorer emits one of a fixed set of
# base confidences (see _TIER_BASE below). Bounded by [MIN, MAX].
# ---------------------------------------------------------------------------
HISTORY_BASE_CONFIDENCE = 0.88
HISTORY_PER_USE_BONUS = 0.02
HISTORY_MAX_CONFIDENCE = 0.99
HISTORY_MIN_CONFIDENCE = 0.65

# Tier-based confidences. Order matters: A is the strongest signal.
#   A — GSTIN matches AND this target is the dominant mapping for the vendor
#   B — GSTIN matches alone
#   C — Dominant mapping alone (≥70% of this vendor's history points here)
#   D — Highest mapping but ambiguous (40-70%)
#   E — Long tail
#
# A 'stale' penalty (-0.05) kicks in if the candidate hasn't been used in
# the last 180 days — so a top-tier-but-ancient mapping is still beaten by
# a tier-down-but-fresh one.
_TIER_BASE = {"A": 0.97, "B": 0.92, "C": 0.88, "D": 0.82, "E": 0.70}
_TIER_ORDER = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
_DOMINANT_THRESHOLD = 0.70
_AMBIGUOUS_THRESHOLD = 0.40
_STALE_DAYS = 180
_STALE_PENALTY = 0.05


def history_confidence(usage_count: int) -> float:
    """Legacy formula; kept so any external caller that imported it still works."""
    return min(
        HISTORY_BASE_CONFIDENCE + usage_count * HISTORY_PER_USE_BONUS,
        HISTORY_MAX_CONFIDENCE,
    )


# ---------------------------------------------------------------------------
# Context + scoring primitives
# ---------------------------------------------------------------------------

@dataclass
class HistoryContext:
    """Invoice context passed in at suggestion time. All fields optional —
    missing values are treated as mild penalties, not disqualifiers."""
    voucher_type: Optional[str] = None
    amount: Optional[float] = None
    gstin: Optional[str] = None
    hsn_codes: List[str] = field(default_factory=list)

    @property
    def amount_band(self) -> Optional[str]:
        return amount_to_band(self.amount)


@dataclass
class HistoryCandidate:
    """One historical row, scored against the current invoice context."""
    target_value: str
    usage_count: int
    metadata: Dict[str, Any]
    voucher_type: Optional[str]
    amount_band: Optional[str]
    gstin: Optional[str]
    hsn_codes: List[str]
    confirmation_source: str
    last_used_at: Optional[datetime]
    confidence: float = 0.0
    composite_score: float = 0.0
    breakdown: Dict[str, Any] = field(default_factory=dict)


def amount_to_band(amount: Optional[float]) -> Optional[str]:
    """Discretize a monetary amount into one of four bands.

    Matches the values stored in tally_transaction_history.amount_band.
    """
    if amount is None:
        return None
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return None
    if a < 5000:
        return "lt_5k"
    if a < 50000:
        return "5k_50k"
    if a < 500000:
        return "50k_500k"
    return "gt_500k"


def _days_since(ts: Optional[datetime]) -> float:
    if ts is None:
        return 365.0
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    return max(delta.total_seconds() / 86400.0, 0.0)


def score_history_candidate(
    row: HistoryCandidate,
    ctx: HistoryContext,
    source_total_uses: int,
) -> None:
    """Tier-based historical scorer. Three signals matter:

      1. GSTIN exact match — strongest signal that "same vendor" → "same
         ledger" is correct. Byte-exact, near-deterministic.
      2. Frequency dominance — what share of the vendor's history points
         at THIS target? Dominant (≥70%) means the user has been
         consistent; ambiguous (40-70%) means there are competing
         mappings; long-tail (<40%) is unreliable.
      3. Recency — used as a tiebreaker within a tier and as a small
         confidence penalty for stale (>180 day) rows.

    Tier table:
      A — GSTIN match + dominant → 0.97   (near-certain)
      B — GSTIN match alone      → 0.92   (vendor identified, target uncertain)
      C — Dominant alone         → 0.88   (vendor name matches, target consistent)
      D — Highest but ambiguous  → 0.82   (best guess, but the user has waffled)
      E — Long tail              → 0.70

    The other context fields (voucher_type, amount_band, HSN codes,
    confirmation_source) were dropped from the scorer — they added
    weighted noise without changing the eventual top pick in practice.
    They're still stored on the row so a future pass can reintroduce
    them if needed.
    """
    gstin_match = bool(ctx.gstin and row.gstin and ctx.gstin == row.gstin)
    freq_share = (row.usage_count / source_total_uses) if source_total_uses else 1.0
    days_old = _days_since(row.last_used_at)

    is_dominant = freq_share >= _DOMINANT_THRESHOLD
    if gstin_match and is_dominant:
        tier = "A"
    elif gstin_match:
        tier = "B"
    elif is_dominant:
        tier = "C"
    elif freq_share >= _AMBIGUOUS_THRESHOLD:
        tier = "D"
    else:
        tier = "E"

    base = _TIER_BASE[tier]
    if days_old > _STALE_DAYS:
        base -= _STALE_PENALTY

    row.confidence = round(
        min(max(base, HISTORY_MIN_CONFIDENCE), HISTORY_MAX_CONFIDENCE), 3
    )
    # composite_score is the sort key. Primary by tier (A > B > C > ...);
    # secondary by recency so within a tier the freshest candidate wins.
    row.composite_score = round(_TIER_ORDER[tier] - (days_old / 365.0), 4)
    row.breakdown = {
        "tier": tier,
        "gstin_match": gstin_match,
        "frequency_share": round(freq_share, 3),
        "days_since_used": int(round(days_old, 0)),
    }


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def lookup_history_ranked(
    user_id: str,
    company_name: str,
    mapping_type: str,
    source_key: str,
    context: Optional[HistoryContext] = None,
    top_n: int = 3,
) -> List[HistoryCandidate]:
    """Return up to `top_n` historical candidates ranked by context-aware score."""
    if not user_id or not company_name or not source_key:
        return []
    canonical = normalize_name(source_key)
    if not canonical:
        return []

    ctx = context or HistoryContext()

    conn = get_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT target_value, usage_count, metadata,
                       voucher_type, amount_band, gstin, hsn_codes,
                       COALESCE(confirmation_source, 'auto_pushed') AS confirmation_source,
                       last_used_at
                  FROM tally_transaction_history
                 WHERE user_id = %s
                   AND company_name = %s
                   AND mapping_type = %s
                   AND source_identifier = %s
                """,
                (user_id, company_name, mapping_type, canonical),
            )
            rows = cur.fetchall() or []
    except psycopg2.Error as exc:
        logger.warning("tally_transaction_history lookup failed: %s", exc)
        return []
    finally:
        return_connection(conn)

    if not rows:
        return []

    candidates: List[HistoryCandidate] = []
    for r in rows:
        meta = r.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        candidates.append(
            HistoryCandidate(
                target_value=r["target_value"],
                usage_count=int(r["usage_count"]),
                metadata=meta or {},
                voucher_type=r.get("voucher_type"),
                amount_band=r.get("amount_band"),
                gstin=r.get("gstin"),
                hsn_codes=list(r.get("hsn_codes") or []),
                confirmation_source=r.get("confirmation_source") or "auto_pushed",
                last_used_at=r.get("last_used_at"),
            )
        )

    source_total = sum(c.usage_count for c in candidates)
    for c in candidates:
        score_history_candidate(c, ctx, source_total)

    candidates.sort(key=lambda c: c.composite_score, reverse=True)
    return candidates[:top_n]


def lookup_history(
    user_id: str,
    company_name: str,
    mapping_type: str,
    source_key: str,
) -> Optional[Dict[str, Any]]:
    """Back-compat shim. Returns just the top-1 row in the legacy dict shape."""
    ranked = lookup_history_ranked(user_id, company_name, mapping_type, source_key, top_n=1)
    if not ranked:
        return None
    top = ranked[0]
    return {
        "target_value": top.target_value,
        "usage_count": top.usage_count,
        "metadata": top.metadata,
    }


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------

def record_history(
    user_id: str,
    company_name: str,
    mapping_type: str,
    source_key: str,
    target_value: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    voucher_type: Optional[str] = None,
    amount: Optional[float] = None,
    gstin: Optional[str] = None,
    hsn_codes: Optional[List[str]] = None,
    confirmation_source: str = "auto_pushed",
) -> None:
    """Upsert a single mapping into history. Silent no-op on any error.

    The context kwargs (voucher_type, amount, gstin, hsn_codes,
    confirmation_source) feed the Layer 0 scorer at lookup time. Rows
    without them still work — the scorer treats missing context as neutral.
    """
    if not user_id or not company_name or not source_key or not target_value:
        return
    canonical = normalize_name(source_key)
    if not canonical:
        return

    meta_json = json.dumps(metadata or {})
    band = amount_to_band(amount)
    hsn = list(hsn_codes) if hsn_codes else None

    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tally_transaction_history
                    (user_id, company_name, mapping_type, source_identifier,
                     target_value, metadata, usage_count, last_used_at,
                     voucher_type, amount_band, gstin, hsn_codes, confirmation_source)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, 1, CURRENT_TIMESTAMP,
                        %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, company_name, mapping_type, source_identifier, target_value)
                DO UPDATE SET
                    usage_count         = tally_transaction_history.usage_count + 1,
                    last_used_at        = CURRENT_TIMESTAMP,
                    metadata            = COALESCE(EXCLUDED.metadata, tally_transaction_history.metadata),
                    voucher_type        = COALESCE(EXCLUDED.voucher_type, tally_transaction_history.voucher_type),
                    amount_band         = COALESCE(EXCLUDED.amount_band, tally_transaction_history.amount_band),
                    gstin               = COALESCE(EXCLUDED.gstin, tally_transaction_history.gstin),
                    hsn_codes           = COALESCE(EXCLUDED.hsn_codes, tally_transaction_history.hsn_codes),
                    -- A user override always upgrades the confirmation status;
                    -- never silently downgrade a user_corrected row to auto_pushed.
                    confirmation_source = CASE
                        WHEN EXCLUDED.confirmation_source = 'user_corrected' THEN 'user_corrected'
                        WHEN tally_transaction_history.confirmation_source = 'user_corrected' THEN 'user_corrected'
                        WHEN EXCLUDED.confirmation_source = 'user_picked_from_alts' THEN 'user_picked_from_alts'
                        ELSE tally_transaction_history.confirmation_source
                    END
                """,
                (
                    user_id, company_name, mapping_type, canonical,
                    target_value, meta_json,
                    voucher_type, band, gstin, hsn, confirmation_source,
                ),
            )
        conn.commit()
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("tally_transaction_history upsert failed: %s", exc)
    finally:
        return_connection(conn)


def record_correction(
    user_id: str,
    company_name: str,
    field: str,
    *,
    vendor_name: Optional[str] = None,
    vendor_gstin: Optional[str] = None,
    voucher_type: Optional[str] = None,
    amount: Optional[float] = None,
    hsn_codes: Optional[List[str]] = None,
    suggested_ledger: Optional[str] = None,
    suggested_match_type: Optional[str] = None,
    suggested_confidence: Optional[float] = None,
    picked_ledger: str = "",
    source: str = "push",
    invoice_draft_id: Optional[str] = None,
) -> None:
    """Insert a row into ledger_suggestion_feedback. Silent on error.

    Called from two paths:
      * push controller, after comparing pushed vs originally-suggested ledger
      * /ca/suggest-ledger/feedback endpoint, when the user picks a different
        ledger from the dropdown before pushing.
    """
    if not user_id or not company_name or not picked_ledger:
        return
    if field not in ("party_ledger", "expense_ledger"):
        return
    was_override = bool(suggested_ledger) and suggested_ledger.strip() != picked_ledger.strip()

    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ledger_suggestion_feedback
                    (user_id, company_name, invoice_draft_id, field,
                     vendor_name, vendor_gstin, voucher_type, amount, hsn_codes,
                     suggested_ledger, suggested_match_type, suggested_confidence,
                     picked_ledger, was_override, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id, company_name, invoice_draft_id, field,
                    vendor_name, vendor_gstin, voucher_type,
                    float(amount) if amount is not None else None,
                    list(hsn_codes) if hsn_codes else None,
                    suggested_ledger, suggested_match_type,
                    float(suggested_confidence) if suggested_confidence is not None else None,
                    picked_ledger, was_override, source,
                ),
            )
        conn.commit()
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("ledger_suggestion_feedback insert failed: %s", exc)
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------
# Batch record from push payload
# ---------------------------------------------------------------------------

def _collect_hsn(items: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        code = it.get("hsn_code") or it.get("hsn") or it.get("sac_code") or it.get("sac")
        if code:
            out.append(str(code).strip())
    # Dedup while preserving order so we keep the first HSN as primary.
    seen, deduped = set(), []
    for c in out:
        if c not in seen:
            deduped.append(c)
            seen.add(c)
    return deduped


def record_from_push_payload(
    user_id: str,
    company_name: str,
    voucher_type: str,
    transactions: List[Dict[str, Any]],
) -> None:
    """Extract all learnable mappings from a successful push and record them.

    Called from the push-to-tally controller after Tally confirms the voucher.
    Also writes ledger_suggestion_feedback rows whenever the pushed ledger
    differs from the original AI suggestion the frontend included in the
    transaction. Never raises — all errors are logged and swallowed.
    """
    if not user_id or not company_name or not transactions:
        return

    try:
        for tx in transactions:
            vendor = tx.get("vendor_name") or ""
            party_ledger = tx.get("party_ledger") or ""
            purchase_ledger = tx.get("purchase_ledger") or ""
            sales_ledger = tx.get("sales_ledger") or ""
            tds_section = tx.get("tds_section") or ""
            narration = tx.get("narration") or ""
            items = tx.get("items") or []
            tax_entries = tx.get("tax_entries") or []
            vendor_gstin = tx.get("vendor_gstin") or ""
            amount = tx.get("total_amount") or tx.get("amount")
            hsn_codes = _collect_hsn(items)

            # AI suggestions the frontend stamped onto the row when the user
            # asked for suggestions. Compared against the pushed ledger to
            # detect overrides.
            ai_party = tx.get("ai_party_suggestion") or {}
            ai_expense = tx.get("ai_expense_suggestion") or {}

            party_confirmation = "auto_pushed"
            expense_confirmation = "auto_pushed"

            # ── Party ledger feedback ──
            if vendor and party_ledger:
                suggested_party = ai_party.get("recommended_ledger") if isinstance(ai_party, dict) else None
                if suggested_party and suggested_party.strip() != party_ledger.strip():
                    party_confirmation = "user_corrected"
                    record_correction(
                        user_id, company_name, "party_ledger",
                        vendor_name=vendor,
                        vendor_gstin=vendor_gstin,
                        voucher_type=voucher_type,
                        amount=amount,
                        hsn_codes=hsn_codes,
                        suggested_ledger=suggested_party,
                        suggested_match_type=ai_party.get("match_type") if isinstance(ai_party, dict) else None,
                        suggested_confidence=ai_party.get("confidence") if isinstance(ai_party, dict) else None,
                        picked_ledger=party_ledger,
                        source="push",
                    )

                record_history(
                    user_id, company_name, "party_ledger",
                    vendor, party_ledger,
                    metadata={"voucher_type": voucher_type},
                    voucher_type=voucher_type,
                    amount=amount,
                    gstin=vendor_gstin or None,
                    hsn_codes=hsn_codes or None,
                    confirmation_source=party_confirmation,
                )

            # ── Expense / sales / purchase ledger feedback ──
            expense_ledger = purchase_ledger or sales_ledger
            if vendor and expense_ledger:
                suggested_expense = ai_expense.get("recommended_ledger") if isinstance(ai_expense, dict) else None
                if suggested_expense and suggested_expense.strip() != expense_ledger.strip():
                    expense_confirmation = "user_corrected"
                    record_correction(
                        user_id, company_name, "expense_ledger",
                        vendor_name=vendor,
                        vendor_gstin=vendor_gstin,
                        voucher_type=voucher_type,
                        amount=amount,
                        hsn_codes=hsn_codes,
                        suggested_ledger=suggested_expense,
                        suggested_match_type=ai_expense.get("match_type") if isinstance(ai_expense, dict) else None,
                        suggested_confidence=ai_expense.get("confidence") if isinstance(ai_expense, dict) else None,
                        picked_ledger=expense_ledger,
                        source="push",
                    )

                record_history(
                    user_id, company_name, "expense_ledger",
                    vendor, expense_ledger,
                    metadata={"voucher_type": voucher_type},
                    voucher_type=voucher_type,
                    amount=amount,
                    gstin=vendor_gstin or None,
                    hsn_codes=hsn_codes or None,
                    confirmation_source=expense_confirmation,
                )

            # ── Item descriptions → stock item names ──
            for item in items:
                original = item.get("original_name") or ""
                matched = item.get("matched_stock_item") or ""
                if original and matched and original != matched:
                    record_history(
                        user_id, company_name, "item_mapping",
                        original, matched,
                        voucher_type=voucher_type,
                    )

            # ── Vendor → tax treatment ──
            if vendor and tax_entries:
                tax_meta: Dict[str, Any] = {}
                for te in tax_entries:
                    ledger_name = (te.get("ledger_name") or "").upper()
                    amt = te.get("amount") or 0
                    if "CGST" in ledger_name:
                        tax_meta["cgst_amount"] = amt
                    elif "SGST" in ledger_name:
                        tax_meta["sgst_amount"] = amt
                    elif "IGST" in ledger_name:
                        tax_meta["igst_amount"] = amt
                if tax_meta:
                    tax_meta["tax_ledgers"] = [
                        te.get("ledger_name") for te in tax_entries
                        if te.get("ledger_name")
                    ]
                    record_history(
                        user_id, company_name, "tax_treatment",
                        vendor, "tax_config",
                        metadata=tax_meta,
                        voucher_type=voucher_type,
                        gstin=vendor_gstin or None,
                    )

            # ── Vendor → TDS section ──
            if vendor and tds_section:
                record_history(
                    user_id, company_name, "tds_section",
                    vendor, tds_section,
                    voucher_type=voucher_type,
                    gstin=vendor_gstin or None,
                )

            # ── Bank narration → ledger ──
            if narration and party_ledger and voucher_type in ("bank_entry", "bank"):
                record_history(
                    user_id, company_name, "bank_description",
                    narration, party_ledger,
                    metadata={"voucher_type": voucher_type},
                    voucher_type=voucher_type,
                    amount=amount,
                )

    except Exception as exc:
        logger.warning("record_from_push_payload failed (non-blocking): %s", exc)
