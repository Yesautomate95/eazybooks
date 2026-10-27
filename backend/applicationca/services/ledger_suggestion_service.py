"""
Ledger suggestion service for TallyPrime invoice processing.

Delegates matching to:
  Layer 0 – Transaction history  (instant, skips pipeline if hit)
  Layer 1 – Mapping rules        (user-verified, instant)
  Layer 2 – GSTIN exact          (deterministic, instant)
  Layer 3 – Fuzzy matching       (rapidfuzz, ~5 ms)
  Layer 4 – LLM with CoT         (top-20 candidates, ~2 s)

Layer 0 checks tally_transaction_history for vendor/item/narration mappings
recorded from past pushes. When it hits, it bypasses the expensive LLM call.

Confidence is ALWAYS computed locally (fuzzy score or history formula), never by the LLM.
"""

import logging
from typing import Dict, Any, List, Optional

from config.settings import settings
from applicationca.contoller.websocket_controller import manager as ws_manager
from applicationca.services import tally_cache_store
from applicationca.services.ledger_matcher import LedgerMatcher
from applicationca.services.tally_history_service import (
    HistoryCandidate,
    HistoryContext,
    history_confidence,
    lookup_history,
    lookup_history_ranked,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Voucher Type → Tally Group Mapping
# ──────────────────────────────────────────────────────────────
VOUCHER_GROUP_MAP = {
    "sales_invoice": {
        "party_groups": ["Sundry Debtors", "Sundry Creditors"],
        "ledger_groups": ["Sales Accounts", "Direct Incomes", "Indirect Incomes"],
    },
    "purchase_bill": {
        "party_groups": ["Sundry Debtors", "Sundry Creditors"],
        "ledger_groups": ["Purchase Accounts", "Direct Expenses", "Indirect Expenses"],
    },
    "payment_voucher": {
        "party_groups": ["Sundry Creditors"],
        "ledger_groups": ["Bank Accounts"],
    },
    "receipt_voucher": {
        "party_groups": ["Sundry Debtors"],
        "ledger_groups": ["Bank Accounts"],
    },
    "expense_voucher": {
        "party_groups": [],
        "ledger_groups": ["Indirect Expenses", "Direct Expenses"],
    },
    "journal_voucher": {
        "party_groups": ["Sundry Debtors", "Sundry Creditors"],
        "ledger_groups": [],
    },
    "auto": {
        "party_groups": ["Sundry Creditors", "Sundry Debtors"],
        "ledger_groups": [
            "Purchase Accounts", "Direct Expenses", "Indirect Expenses",
            "Sales Accounts", "Direct Incomes", "Indirect Incomes",
        ],
    },
}


# ──────────────────────────────────────────────────────────────
# Fetch grouped ledgers based on voucher type
# ──────────────────────────────────────────────────────────────
def _shape_grouped_for_voucher(
    grouped_data: Dict[str, Any], party_groups: List[str], ledger_groups: List[str]
) -> Dict[str, Any]:
    """Pure transform: extract party / category / discount slices from the
    full grouped payload (whether it came from the live bridge or the cache).

    Kept side-effect free so the same code path handles cache hits and live
    hits identically — that's the property we lean on for the offline mode
    to behave exactly like online mode aside from staleness.
    """
    party_ledgers: List[Any] = []
    ledger_by_group: Dict[str, Any] = {}

    party_data = grouped_data.get("party", {}) if grouped_data else {}
    for group_name in party_groups:
        ledgers = party_data.get(group_name, [])
        party_ledgers.extend(ledgers)

    purchase_data = grouped_data.get("purchase", {}) if grouped_data else {}
    sales_data = grouped_data.get("sales", {}) if grouped_data else {}

    for group_name in ledger_groups:
        ledgers = purchase_data.get(group_name, []) or sales_data.get(group_name, [])
        if ledgers:
            key = group_name.lower().replace(" ", "_")
            ledger_by_group[key] = ledgers

    # Discount candidates always come from Indirect Expenses regardless of
    # voucher type. Tally's convention is to post line-level discounts to
    # "Discount Allowed" / "Trade Discount" etc. which live under this group,
    # including any nested sub-groups (BELONGSTO=Yes handles the recursion at
    # the Tally XML query layer).
    discount_candidates = purchase_data.get("Indirect Expenses", [])

    return {
        "party_ledgers": party_ledgers,
        "ledger_groups": ledger_by_group,
        "discount_candidates": discount_candidates,
    }


async def _fetch_ledgers_for_voucher_type(
    company: str, voucher_type: str, firebase_uid: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch only the relevant ledger group branches from Tally, with offline
    cache fallback.

    Strategy:
      1. If the WebSocket bridge is online, ask the Electron client for the
         grouped payload. On success, write it through to the DB cache so
         every successful live call refreshes the offline universe.
      2. If the bridge is offline OR the call fails/times out, read the last
         successful grouped payload from tally_cached_ledgers and use that
         instead. Caller learns it's degraded via the `from_cache` flag.

    The returned dict always includes the same `party_ledgers`,
    `ledger_groups`, `discount_candidates` keys — plus `from_cache` and
    `cache_refreshed_at` so downstream callers (and ultimately the frontend)
    can show the user a "Tally offline — showing cached suggestions" badge.
    """
    mapping = VOUCHER_GROUP_MAP.get(voucher_type, VOUCHER_GROUP_MAP["auto"])
    party_groups = mapping["party_groups"]
    ledger_groups = mapping["ledger_groups"]

    empty = {
        "party_ledgers": [],
        "ledger_groups": {},
        "discount_candidates": [],
        "from_cache": False,
        "cache_refreshed_at": None,
    }

    if not party_groups and not ledger_groups:
        return empty

    def _from_cache_or_empty(reason: str) -> Dict[str, Any]:
        if not firebase_uid or not company:
            return empty
        try:
            cached = tally_cache_store.get_cached_grouped_ledgers(
                user_id=firebase_uid, company_name=company,
            )
        except Exception as exc:
            logger.warning("Cache read failed (%s): %s", reason, exc)
            return empty
        if not cached or not cached.get("grouped_ledgers"):
            logger.info("No cached grouped ledgers for user=%s company=%s (%s)",
                        firebase_uid, company, reason)
            return empty
        shaped = _shape_grouped_for_voucher(
            cached["grouped_ledgers"], party_groups, ledger_groups,
        )
        refreshed_at = cached.get("grouped_refreshed_at")
        shaped["from_cache"] = True
        shaped["cache_refreshed_at"] = refreshed_at.isoformat() if refreshed_at else None
        logger.info(
            "Using cached grouped ledgers (%s) for user=%s company=%s; refreshed_at=%s; party=%d, cats=%d",
            reason, firebase_uid, company, shaped["cache_refreshed_at"],
            len(shaped["party_ledgers"]),
            sum(len(v) for v in shaped["ledger_groups"].values()),
        )
        return shaped

    # Check if Electron client is connected — if not, go straight to cache.
    if not firebase_uid or firebase_uid not in ws_manager.electron_connections:
        return _from_cache_or_empty("electron_offline")

    try:
        # The default WebSocket timeout (60s) is far longer than the frontend
        # axios budget — if Tally is slow or the Electron app stopped
        # responding mid-request, the user sees the suggestion spinner stuck.
        # 15s fails fast into the cache fallback while still covering typical
        # Tally fetch latencies (~1-3s).
        grouped_data = await ws_manager.get_tally_grouped_ledgers(
            firebase_uid, company, timeout=15,
        )
    except Exception as e:
        logger.error("Error fetching ledgers from Tally via WebSocket: %s", e)
        return _from_cache_or_empty("ws_error")

    # Write-through cache: every successful live fetch warms the offline
    # universe so the next disconnect doesn't strand suggestions.
    if company and grouped_data:
        try:
            tally_cache_store.upsert_grouped_ledgers(
                user_id=firebase_uid, company_name=company, grouped=grouped_data,
            )
        except Exception as exc:
            logger.warning("Cache write-through failed: %s", exc)

    shaped = _shape_grouped_for_voucher(grouped_data, party_groups, ledger_groups)
    shaped["from_cache"] = False
    shaped["cache_refreshed_at"] = None

    logger.info(
        "Fetched %d party ledgers, %d category ledgers, %d discount candidates via WebSocket bridge",
        len(shaped["party_ledgers"]),
        sum(len(v) for v in shaped["ledger_groups"].values()),
        len(shaped["discount_candidates"]),
    )

    return shaped


# ──────────────────────────────────────────────────────────────
# Discount ledger picker — keyword-scored, deterministic
# ──────────────────────────────────────────────────────────────
# Tier ordering matters: a ledger that matches an earlier (more specific)
# keyword beats one matching a later (more generic) keyword. The confidence
# floats are picked so any "discount"-named ledger surfaces above the
# auto-fill threshold (>=0.85) used by the AI dropdown UI when the name is
# clearly a discount account, but stays below the certainty of a vendor
# history hit (>=0.95).
_DISCOUNT_KEYWORD_TIERS = [
    ("discount allowed", 0.95),
    ("discount given", 0.93),
    ("discount received", 0.92),
    ("trade discount", 0.90),
    ("cash discount", 0.88),
    ("discount", 0.82),
]


def _suggest_discount_ledger(candidates):
    """Pick the most-likely discount ledger from a flat list of Indirect
    Expense ledgers.

    Uses name keyword scoring instead of an LLM call — explicit discount
    ledgers almost always have the word "discount" in the name, the cost of
    a wrong LLM pick here (mis-posted journal entry) is far higher than the
    win, and the user can override via the dropdown in one click.

    Accepts ledger entries as either bare names or {name: ...} dicts.
    """
    best_name = None
    best_priority = len(_DISCOUNT_KEYWORD_TIERS)
    best_conf = 0.0
    alternatives = []

    for ledger in candidates or []:
        name = ledger.get("name") if isinstance(ledger, dict) else str(ledger)
        if not name:
            continue
        name_lower = name.lower().strip()
        for i, (keyword, conf) in enumerate(_DISCOUNT_KEYWORD_TIERS):
            if keyword in name_lower:
                if i < best_priority:
                    if best_name and best_name not in alternatives:
                        alternatives.insert(0, best_name)
                    best_name = name
                    best_priority = i
                    best_conf = conf
                elif name not in alternatives and name != best_name:
                    alternatives.append(name)
                break

    if best_name:
        return {
            "recommended_ledger": best_name,
            "confidence": best_conf,
            "match_type": "keyword",
            "reasoning": f"Matched '{best_name}' in Indirect Expenses by name.",
            "category": "Indirect Expenses",
            "alternatives": alternatives[:4],
            "create_new": False,
            "suggested_new_name": None,
        }

    return {
        "recommended_ledger": "",
        "confidence": 0.0,
        "match_type": "no_match",
        "reasoning": (
            "No discount-named ledger exists under Indirect Expenses. "
            "Create one (e.g. 'Discount Allowed') or pick an existing one."
        ),
        "category": "Indirect Expenses",
        "alternatives": [],
        "create_new": True,
        "suggested_new_name": "Discount Allowed",
    }


def _empty_discount_suggestion():
    """Returned when Tally isn't reachable — keeps the response shape stable
    so the frontend doesn't have to special-case missing fields."""
    return {
        "recommended_ledger": "",
        "confidence": 0.0,
        "match_type": "no_match",
        "reasoning": "Indirect Expense ledgers not available — Tally may be disconnected.",
        "category": "Indirect Expenses",
        "alternatives": [],
        "create_new": True,
        "suggested_new_name": "Discount Allowed",
    }


# ──────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────
async def suggest_ledgers(
    bill_data: Dict[str, Any],
    company: str,
    voucher_type: str = "auto",
    firebase_uid: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Hybrid ledger suggestion using the unified 4-layer matcher.

    Args:
        bill_data:     Extracted bill/invoice data.
        company:       Tally company name.
        voucher_type:  Type of voucher (sales_invoice, purchase_bill, etc.).
        firebase_uid:  User's Firebase UID for WebSocket bridge to Electron app.
        user_id:       User ID for MappingService (Layer 1).  Falls back to
                       firebase_uid if not provided.
    """
    vendor_name = bill_data.get("vendor_name", "")
    vendor_gstin = bill_data.get("vendor_gstin", "")
    uid = user_id or firebase_uid

    vtype = voucher_type if voucher_type in VOUCHER_GROUP_MAP else "auto"
    bill_data["voucher_type"] = vtype

    # Fetch relevant ledger branches from Tally via WebSocket bridge
    fetched = await _fetch_ledgers_for_voucher_type(company, vtype, firebase_uid)
    party_ledgers = fetched["party_ledgers"]
    ledger_groups = fetched["ledger_groups"]
    discount_candidates = fetched.get("discount_candidates", [])
    from_cache = bool(fetched.get("from_cache"))
    cache_refreshed_at = fetched.get("cache_refreshed_at")

    # Discount ledger suggestion is computed once up front — it's a cheap,
    # deterministic keyword scan, so doing it before the branchy party/category
    # logic keeps every return path consistent.
    discount_suggestion = (
        _suggest_discount_ledger(discount_candidates)
        if discount_candidates
        else _empty_discount_suggestion()
    )

    # ledger_source = 'live' when the bridge served the request, 'cache' when
    # we fell back to the persisted snapshot. Frontend reads this to show a
    # "Tally offline — using cached ledgers" indicator without having to
    # second-guess based on confidence scores.
    ledger_source = "cache" if from_cache else "live"

    if not party_ledgers and not ledger_groups:
        fallback = _fallback_response(vendor_name, create_new=True)
        fallback["discount_ledger_suggestion"] = discount_suggestion
        fallback["ledger_source"] = ledger_source
        fallback["cache_refreshed_at"] = cache_refreshed_at
        return fallback

    # Instantiate the unified matcher
    matcher = LedgerMatcher(
        user_id=uid,
        company_name=company,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_model=settings.OPENAI_MODEL or "gpt-4.1-mini",
    )

    result: Dict[str, Any] = {
        "discount_ledger_suggestion": discount_suggestion,
        "ledger_source": ledger_source,
        "cache_refreshed_at": cache_refreshed_at,
    }

    # Build name universes once.
    party_names = {l["name"] for l in party_ledgers} if party_ledgers else set()
    party_names_list = [l["name"] for l in party_ledgers if l.get("name")] if party_ledgers else []
    all_expense_names: set = set()
    expense_names_list: List[str] = []
    if ledger_groups:
        for grp_ledgers in ledger_groups.values():
            for l in grp_ledgers:
                name = l["name"] if isinstance(l, dict) else str(l)
                if name and name not in all_expense_names:
                    all_expense_names.add(name)
                    expense_names_list.append(name)

    document_type = _doc_type_for_voucher(vtype)
    history_ctx = _build_history_context(bill_data, vtype, vendor_gstin)
    total_universe = len(party_names_list) + len(expense_names_list)

    party_filled = False  # whether result["party_suggestion"] has been populated
    expense_filled = False

    # ──────────────────────────────────────────────────────────────────────
    # PRIORITY 1 — User-defined mapping rules
    # ──────────────────────────────────────────────────────────────────────
    # Rules express explicit user intent ("always map vendor X to ledger Y").
    # They beat every other signal — including high-frequency historical
    # patterns — because the user has *said* they want this. The rule
    # evaluator returns a hit only when the target ledger is present in
    # the supplied ledger universe, so we run it twice: once with party
    # ledger names, once with expense names. Whichever returns a hit fills
    # that slot.
    if vendor_name and party_names_list:
        party_rule = matcher._layer1_mapping_rules(
            query_name=vendor_name,
            document_type=document_type,
            available_names=party_names_list,
            vendor_gstin=vendor_gstin,
            bill_context=bill_data,
            ledger_slot="party",
        )
        if party_rule:
            result["party_suggestion"] = _match_result_to_party_suggestion(party_rule, vtype)
            result["action"] = _determine_action(party_rule.confidence)
            party_filled = True
            logger.info(
                "Rule hit (party): vendor='%s' → '%s' (rule)",
                vendor_name, party_rule.recommended_ledger,
            )

    if expense_names_list:
        expense_rule = matcher._layer1_mapping_rules(
            query_name=vendor_name or "",
            document_type=document_type,
            available_names=expense_names_list,
            vendor_gstin=vendor_gstin,
            bill_context=bill_data,
            ledger_slot="expense",
        )
        if expense_rule:
            result["expense_revenue_suggestion"] = _match_result_to_expense_suggestion(expense_rule)
            expense_filled = True
            logger.info(
                "Rule hit (expense): vendor='%s' → '%s' (rule)",
                vendor_name, expense_rule.recommended_ledger,
            )

    if party_filled and expense_filled:
        result["ledgers_searched"] = total_universe
        return result

    # ──────────────────────────────────────────────────────────────────────
    # PRIORITY 2 — Historical patterns
    # ──────────────────────────────────────────────────────────────────────
    # Tier-based scorer in tally_history_service ranks past mappings for
    # this vendor by GSTIN match → frequency dominance → recency. Picks the
    # top-ranked candidate that still exists in the current ledger universe.
    if vendor_name and uid:
        if not party_filled and party_names_list:
            party_ranked = lookup_history_ranked(
                uid, company, "party_ledger", vendor_name, context=history_ctx, top_n=3,
            )
            party_top = _first_valid_candidate(party_ranked, party_names)
            if party_top:
                result["party_suggestion"] = _candidate_to_party_suggestion(
                    party_top, party_ranked, vtype,
                )
                result["action"] = _determine_action(party_top.confidence)
                party_filled = True
                logger.info(
                    "History hit (party): vendor='%s' → '%s' (conf=%.2f, tier=%s)",
                    vendor_name, party_top.target_value, party_top.confidence,
                    (party_top.breakdown or {}).get("tier"),
                )

        if not expense_filled and expense_names_list:
            expense_ranked = lookup_history_ranked(
                uid, company, "expense_ledger", vendor_name, context=history_ctx, top_n=3,
            )
            expense_top = _first_valid_candidate(expense_ranked, all_expense_names)
            if expense_top:
                result["expense_revenue_suggestion"] = _candidate_to_expense_suggestion(
                    expense_top, expense_ranked,
                )
                expense_filled = True
                logger.info(
                    "History hit (expense): vendor='%s' → '%s' (conf=%.2f, tier=%s)",
                    vendor_name, expense_top.target_value, expense_top.confidence,
                    (expense_top.breakdown or {}).get("tier"),
                )

    if party_filled and expense_filled:
        result["ledgers_searched"] = total_universe
        return result

    # ──────────────────────────────────────────────────────────────────────
    # PRIORITY 3 — Matcher pipeline (GSTIN exact → fuzzy → LLM)
    # ──────────────────────────────────────────────────────────────────────
    # Whatever the rule + history layers couldn't fill, hand off to the
    # matcher. Internally it does: GSTIN exact match → rule check (will
    # miss again since we already ran it) → fuzzy → LLM. The redundant
    # rule check is one cached DB lookup — cheap.
    if not party_filled:
        if party_ledgers and vendor_name:
            match = await matcher.match_party(
                query_name=vendor_name,
                ledgers=party_ledgers,
                vendor_gstin=vendor_gstin,
                document_type=document_type,
                bill_context=bill_data,
            )
            result["party_suggestion"] = _match_result_to_party_suggestion(match, vtype)
            result["action"] = _determine_action(match.confidence)
        else:
            result["party_suggestion"] = _empty_party_suggestion(vendor_name)
            result["action"] = "create_new"

    # ── Category matching (LLM with CoT) ──
    # Skipped if Priority 1 (rules) or Priority 2 (history) already filled
    # the expense slot — avoids a wasteful LLM call when we already know
    # the answer.
    if not expense_filled:
        if ledger_groups:
            cat = await matcher.match_category(bill_data, ledger_groups)
            result["expense_revenue_suggestion"] = {
                "recommended_ledger": cat.recommended_ledger,
                "confidence": cat.confidence,
                "category": cat.category,
                "reasoning": cat.reasoning,
                "gst_rate_detected": cat.gst_rate_detected,
                "alternatives": cat.alternatives,
            }
        else:
            result["expense_revenue_suggestion"] = _empty_ledger_suggestion()

    result["ledgers_searched"] = total_universe

    logger.info(
        "Suggestion: vendor='%s', company='%s', party_conf=%.2f, action=%s",
        vendor_name, company,
        result["party_suggestion"]["confidence"],
        result["action"],
    )
    return result


# ──────────────────────────────────────────────────────────────
# MatchResult → API dict conversion
# ──────────────────────────────────────────────────────────────
# Pulled out so the multi-priority pipeline writes the response shape in
# exactly one place — adds new fields once, every layer sees them.

def _match_result_to_party_suggestion(
    match: Any, voucher_type: str
) -> Dict[str, Any]:
    return {
        "recommended_ledger": match.recommended_ledger,
        "confidence": match.confidence,
        "match_type": match.match_type,
        "reasoning": match.reasoning,
        "group": getattr(match, "group", None) or _infer_party_group(voucher_type),
        "alternatives": getattr(match, "alternatives", []) or [],
        "create_new": getattr(match, "create_new", False),
        "suggested_new_name": getattr(match, "suggested_new_name", None),
    }


def _match_result_to_expense_suggestion(match: Any) -> Dict[str, Any]:
    return {
        "recommended_ledger": match.recommended_ledger,
        "confidence": match.confidence,
        "category": getattr(match, "category", "") or "",
        "match_type": match.match_type,
        "reasoning": match.reasoning,
        "gst_rate_detected": getattr(match, "gst_rate_detected", "unknown") or "unknown",
        "alternatives": getattr(match, "alternatives", []) or [],
    }


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _extract_hsn_codes(bill_data: Dict[str, Any]) -> List[str]:
    """Pull HSN/SAC codes out of bill_data.line_items / hsn_codes top-level."""
    codes: List[str] = []
    for item in bill_data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        code = item.get("hsn_code") or item.get("hsn") or item.get("sac_code") or item.get("sac")
        if code:
            codes.append(str(code).strip())
    for code in bill_data.get("hsn_codes") or []:
        if code:
            codes.append(str(code).strip())
    # Dedup, preserve order.
    seen, out = set(), []
    for c in codes:
        if c and c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _build_history_context(
    bill_data: Dict[str, Any], voucher_type: str, vendor_gstin: str
) -> HistoryContext:
    amount = bill_data.get("total_amount") or bill_data.get("subtotal") or bill_data.get("amount")
    return HistoryContext(
        voucher_type=voucher_type,
        amount=float(amount) if amount is not None else None,
        gstin=vendor_gstin or None,
        hsn_codes=_extract_hsn_codes(bill_data),
    )


def _first_valid_candidate(
    candidates: List[HistoryCandidate], available_names: set
) -> Optional[HistoryCandidate]:
    """Return the highest-scored candidate whose ledger still exists in Tally."""
    for c in candidates:
        if c.target_value in available_names:
            return c
    return None


def _candidate_reason(c: HistoryCandidate) -> str:
    """Human-readable explanation for the AISuggestedDropdown's reasoning chip.

    Reads the simplified tier-based breakdown emitted by the new history
    scorer (tier / gstin_match / frequency_share / days_since_used).
    """
    bd = c.breakdown or {}
    bits = [f"used {c.usage_count}× for this vendor"]
    if bd.get("gstin_match"):
        bits.append("GSTIN matches")
    freq_share = bd.get("frequency_share")
    if isinstance(freq_share, (int, float)) and freq_share >= 0.70:
        bits.append(f"consistent ({int(freq_share * 100)}% of past mappings)")
    elif isinstance(freq_share, (int, float)) and freq_share < 0.40:
        bits.append("ambiguous (mixed past mappings)")
    if c.confirmation_source == "user_corrected":
        bits.append("user-corrected previously")
    days_old = bd.get("days_since_used")
    if isinstance(days_old, (int, float)) and days_old > 180:
        bits.append("older — may be stale")
    return "; ".join(bits)


def _candidate_to_alternatives(
    chosen: HistoryCandidate, ranked: List[HistoryCandidate], available: set
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in ranked:
        if c.target_value == chosen.target_value:
            continue
        if available and c.target_value not in available:
            continue
        out.append({
            "ledger": c.target_value,
            "confidence": c.confidence,
            "reason": _candidate_reason(c),
            "match_type": "history",
        })
        if len(out) >= 3:
            break
    return out


def _candidate_to_party_suggestion(
    chosen: HistoryCandidate, ranked: List[HistoryCandidate], voucher_type: str
) -> Dict[str, Any]:
    return {
        "recommended_ledger": chosen.target_value,
        "confidence": chosen.confidence,
        "match_type": "history",
        "reasoning": _candidate_reason(chosen),
        "group": (chosen.metadata or {}).get("parent_group") or _infer_party_group(voucher_type),
        "alternatives": _candidate_to_alternatives(
            chosen, ranked, {c.target_value for c in ranked}
        ),
        "create_new": False,
        "suggested_new_name": None,
        "usage_count": chosen.usage_count,
        "score_breakdown": chosen.breakdown,
    }


def _candidate_to_expense_suggestion(
    chosen: HistoryCandidate, ranked: List[HistoryCandidate]
) -> Dict[str, Any]:
    return {
        "recommended_ledger": chosen.target_value,
        "confidence": chosen.confidence,
        "category": (chosen.metadata or {}).get("category", ""),
        "reasoning": _candidate_reason(chosen),
        "gst_rate_detected": "unknown",
        "alternatives": _candidate_to_alternatives(
            chosen, ranked, {c.target_value for c in ranked}
        ),
        "match_type": "history",
        "usage_count": chosen.usage_count,
        "score_breakdown": chosen.breakdown,
    }


def _doc_type_for_voucher(voucher_type: str) -> str:
    """Map voucher_type to mapping_rules document_type."""
    return {
        "purchase_bill": "purchase_invoice",
        "sales_invoice": "sales_invoice",
    }.get(voucher_type, "purchase_invoice")


def _determine_action(confidence: float) -> str:
    if confidence >= 0.85:
        return "auto_select"
    if confidence >= 0.50:
        return "suggest"
    return "create_new"


def _infer_party_group(voucher_type: str) -> str:
    if voucher_type in ("sales_invoice", "receipt_voucher"):
        return "Sundry Debtors"
    return "Sundry Creditors"


def _empty_party_suggestion(vendor_name: str) -> Dict[str, Any]:
    return {
        "recommended_ledger": "",
        "confidence": 0.0,
        "match_type": "no_match",
        "reasoning": "No matching ledger found.",
        "group": "Sundry Creditors",
        "alternatives": [],
        "create_new": bool(vendor_name),
        "suggested_new_name": vendor_name or None,
    }


def _empty_ledger_suggestion() -> Dict[str, Any]:
    return {
        "recommended_ledger": "",
        "confidence": 0.0,
        "category": "",
        "reasoning": "No ledger suggestion available.",
        "gst_rate_detected": "unknown",
        "alternatives": [],
    }


def _fallback_response(vendor_name: str, create_new: bool = False) -> Dict[str, Any]:
    return {
        "party_suggestion": _empty_party_suggestion(vendor_name),
        "expense_revenue_suggestion": _empty_ledger_suggestion(),
        "action": "create_new" if create_new else "suggest",
        "ledgers_searched": 0,
    }
