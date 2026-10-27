"""
Unified Hybrid Ledger Matching Engine
======================================
4-layer pipeline for matching vendor/party names and expense/revenue
categories to Tally ledgers.

Layer 1: Mapping rules (MappingService)     → conf 0.99
Layer 2: GSTIN exact match                  → conf 0.99
Layer 3: Fuzzy string matching (rapidfuzz)  → conf = actual fuzzy score
Layer 4: LLM with Chain-of-Thought         → conf = fuzzy score of LLM's pick

Key principle: confidence is ALWAYS computed locally (fuzzy score),
never taken from the LLM.  LLM decides *which* ledger; fuzzy decides
*how confident*.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from config.settings import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────

@dataclass
class FuzzyCandidate:
    name: str
    score: float  # 0.0-1.0


@dataclass
class MatchResult:
    recommended_ledger: str
    confidence: float
    match_type: str  # mapping_rule | gstin_exact | fuzzy | llm_assisted | fuzzy_fallback | no_match
    reasoning: str
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    create_new: bool = False
    suggested_new_name: Optional[str] = None
    group: str = ""


@dataclass
class CategoryMatchResult:
    recommended_ledger: str
    confidence: float
    category: str
    reasoning: str
    gst_rate_detected: str = "unknown"
    alternatives: List[Dict[str, Any]] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────
# Name normalization
# ──────────────────────────────────────────────────────────────

# Only LEGAL-FORM suffixes — these never distinguish one company from another.
# Business-type words (Enterprises, Industries, Trading, Services, etc.) are
# kept because "Ravi Enterprises" and "Ravi Industries" can be different companies.
LEGAL_FORM_SUFFIXES = [
    r"\b\(opc\)\b",
    r"\b\(india\)\b",
    r"\b\(p\)\s*ltd\.?\b",
    r"\bpvt\.?\s*ltd\.?\b",
    r"\bprivate\s+limited\b",
    r"\blimited\b",
    r"\bltd\.?\b",
    r"\bllp\b",
    r"\binc\.?\b",
    r"\bcorporation\b",
    r"\bcompany\b",
    r"\b&\s*co\.?\b",
    r"\bm/?s\.?\b",
]

_SUFFIX_RE = re.compile(
    "|".join(LEGAL_FORM_SUFFIXES), re.IGNORECASE
)


def normalize_name(name: str) -> str:
    """Strip business suffixes, punctuation, extra spaces; lowercase."""
    if not name:
        return ""
    result = name.lower().strip()
    result = re.sub(r"[.,\-&/\\()'\"\[\]]+", " ", result)
    result = _SUFFIX_RE.sub(" ", result)
    return re.sub(r"\s+", " ", result).strip()


def compute_fuzzy_score(query: str, candidate: str) -> float:
    """Weighted combination of rapidfuzz metrics + word-level Jaccard.

    Returns a float 0.0-1.0.

    The word-level Jaccard similarity is critical for Indian business names:
    it ensures "Bhagwati Motors" ranks higher against "Bhagwati Prestige Motors"
    (2/3 words overlap) than against "Bhagwati Enterprises" (1/2 words overlap),
    even when token_set_ratio gives both 100%.
    """
    q = normalize_name(query)
    c = normalize_name(candidate)
    if not q or not c:
        return 0.0

    ratio = fuzz.ratio(q, c) / 100.0
    token_sort = fuzz.token_sort_ratio(q, c) / 100.0
    token_set = fuzz.token_set_ratio(q, c) / 100.0
    partial = fuzz.partial_ratio(q, c) / 100.0

    # Word-level Jaccard similarity
    q_words = set(q.split())
    c_words = set(c.split())
    union = q_words | c_words
    word_jaccard = len(q_words & c_words) / len(union) if union else 0.0

    combined = (
        ratio * 0.10
        + token_sort * 0.20
        + token_set * 0.20
        + partial * 0.15
        + word_jaccard * 0.35
    )
    return round(combined, 4)


# ──────────────────────────────────────────────────────────────
# CoT prompts
# ──────────────────────────────────────────────────────────────

PARTY_COT_SYSTEM = """You are a ledger-matching engine for an Indian accounting application (Tally Prime).

Given a party name extracted from a bill/invoice (possibly via OCR) and a SHORT LIST of candidate ledgers, find the best match.

FOLLOW THESE STEPS:

STEP 1 — TOKEN ANALYSIS
  List every significant word (3+ characters, ignoring Pvt/Ltd/etc) from the invoice party name.
  For each candidate, list which of those words appear.

STEP 2 — ABBREVIATION & OCR CHECK
  Could the invoice name be an abbreviation of any candidate?
  Check common OCR errors: & ↔ 8, I ↔ l ↔ 1, 0 ↔ O, rn ↔ m, missing/extra spaces, singular ↔ plural.

STEP 3 — PICK BEST MATCH
  Based on your analysis, which candidate has the strongest overlap?
  If NONE of the candidates share meaningful word overlap with the invoice name, pick null.

STEP 4 — VERIFY
  Re-read your chosen ledger name. Confirm it appears EXACTLY (same case, spacing, punctuation) in the candidate list below.
  If it does not, set selected_ledger to null.

OUTPUT — strict JSON, no markdown:
{
  "analysis": "Your step-by-step reasoning (2-4 sentences)",
  "selected_ledger": "EXACT name from candidate list, or null",
  "is_no_match": false,
  "suggested_new_name": null
}"""


CATEGORY_COT_SYSTEM = """You are an Indian accounting expert for TallyPrime. Your job is to pick the BEST expense/revenue ledger based on WHAT WAS BOUGHT or SOLD.

FOLLOW THESE STEPS:

STEP 1 — ANALYZE THE ITEMS (most important step)
  Read EVERY line item name carefully. What exactly was purchased/sold?
  Examples of item → ledger mapping:
  - Air tickets, flight charges, travel bookings → Travelling Expenses / Conveyance
  - Hotel stay, accommodation → Travelling Expenses / Hotel Expenses
  - Security guard salary, manpower supply → Labour Charges / Security Charges / Salary
  - Office supplies, pens, paper, stationery → Printing & Stationery / Office Expenses
  - Phone bill, internet, broadband → Telephone Expenses / Communication
  - Rent, lease payment → Rent / Office Rent
  - Fuel, diesel, petrol → Vehicle Expenses / Fuel / Conveyance
  - Computer, laptop, printer → Computer Expenses / Fixed Assets
  - Steel, cement, raw materials, goods → Purchase / Raw Materials
  - Goods for resale, trading stock → Purchase Accounts
  - Audit, legal, consulting fees → Audit Fees / Professional Fees / Consultancy Fees
  - Electricity, water, utility → Electricity Charges / Utility Expenses
  - Courier, postage, shipping → Postage & Courier / Freight
  - Insurance premium → Insurance
  - AMC, maintenance, repair → Repairs & Maintenance
  - Advertisement, marketing → Advertisement Expenses

STEP 2 — MATCH ITEM TO LEDGER NAME
  Look at the available ledger list. Find the ledger whose NAME best describes what the items are.
  Prefer SPECIFIC ledgers over generic ones:
  - "Travelling Expenses" is better than "Indirect Expenses" for flight tickets
  - "Printing & Stationery" is better than "Purchase" for stationery items
  - "Vehicle Maintenance" is better than "Direct Expenses" for car servicing

STEP 3 — DETERMINE ACCOUNTING CATEGORY
  If multiple ledgers seem relevant, use the accounting category to disambiguate:
  - Purchase Accounts: Physical goods bought for resale, raw materials, trading goods
  - Direct Expenses: Costs directly tied to service delivery — labour, freight, subcontractor, manufacturing wages, security services, uniforms
  - Indirect Expenses: Business overhead — rent, electricity, telephone, office supplies, printing, audit fees, insurance, travel, vehicle maintenance, computer repairs, bank charges, annual fees
  - Sales Accounts: Revenue from selling goods/services
  - Direct Incomes: Core business revenue
  - Indirect Incomes: Other income — bank interest, discounts received, commission

STEP 4 — CHECK GST RATE
  If there's a rate-specific ledger (e.g., "Purchase - GST 18%") and the bill's GST rate matches, prefer it.

STEP 5 — PICK AND VERIFY
  Pick the ledger that most specifically describes what the items are.
  Confirm the name appears EXACTLY in the available list.

OUTPUT — strict JSON, no markdown:
{
  "analysis": "Step-by-step: 1) Items are [what]. 2) This matches ledger [name] because [reason]. 3) Category: [category].",
  "selected_ledger": "EXACT name from the ledger list",
  "category": "Purchase Accounts|Direct Expenses|Indirect Expenses|Sales Accounts|Direct Incomes|Indirect Incomes",
  "gst_rate_detected": "18%|12%|5%|0%|exempt|unknown",
  "alternatives": [{"name": "...", "reason": "..."}]
}"""


# ──────────────────────────────────────────────────────────────
# LedgerMatcher
# ──────────────────────────────────────────────────────────────

# Default/placeholder vendor names from upstream extraction failures.
# When these arrive as vendor_name, skip party matching — they're not real names.
_DEFAULT_PLACEHOLDERS = frozenset({
    "cash", "purchase", "sales", "unknown", "n/a", "na", "nil", "none", "",
})

# Threshold above which Layer 3 (fuzzy) is accepted without calling the LLM
FUZZY_AUTO_ACCEPT = 0.90

# Minimum gap between #1 and #2 fuzzy scores to auto-accept.
# If two candidates are within this margin, the match is ambiguous
# and we defer to the LLM for disambiguation.
FUZZY_MIN_GAP = 0.07

# How many fuzzy candidates to send to the LLM in Layer 4
LLM_CANDIDATE_COUNT = 20

# Per-call OpenAI timeout. Default in the SDK is 600s — way longer than the
# frontend's axios budget for /suggest-ledger. With two LLM calls per
# suggestion (party + category) and the WebSocket ledger fetch on top, an
# unbounded timeout would routinely leave the suggestion spinner stuck on
# the user's side. 12s comfortably covers typical gpt-4.1-mini latencies
# (~3-8s) and times out cleanly into the fuzzy fallback otherwise.
LLM_TIMEOUT_SECONDS = 12.0


class LedgerMatcher:
    """Unified 4-layer hybrid ledger matching engine."""

    def __init__(
        self,
        user_id: Optional[str] = None,
        company_name: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_model: str = "gpt-4.1-mini",
    ):
        self.user_id = user_id
        self.company_name = company_name
        self._openai_api_key = openai_api_key or settings.OPENAI_API_KEY
        self._openai_model = openai_model or settings.OPENAI_MODEL or "gpt-4.1-mini"

        # Lazy-init MappingService only when we have user context
        self._mapping_service = None
        if user_id and company_name:
            try:
                from applicationca.services.mapping_service import MappingService
                self._mapping_service = MappingService(user_id, company_name)
            except Exception as exc:
                logger.warning("Could not init MappingService: %s", exc)

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────

    async def match_party(
        self,
        query_name: str,
        ledgers: List[Dict[str, Any]],
        vendor_gstin: Optional[str] = None,
        document_type: str = "purchase_invoice",
        bill_context: Optional[Dict[str, Any]] = None,
    ) -> MatchResult:
        """Full 4-layer pipeline for party/vendor name matching.

        Args:
            query_name:    Party name extracted from the document.
            ledgers:       List of dicts with at minimum {"name": str}.
                           May also contain "gstin", "parent", "closing_balance".
            vendor_gstin:  15-char GSTIN from the document (Layer 2).
            document_type: For MappingService rule filtering.
            bill_context:  Optional dict with line_items, amounts, bill_text
                           used as extra context for the LLM.
        """
        if not query_name or not ledgers:
            return MatchResult(
                recommended_ledger="",
                confidence=0.0,
                match_type="no_match",
                reasoning="No input provided.",
                create_new=bool(query_name),
                suggested_new_name=query_name or None,
            )

        # Layer 2 — GSTIN exact match (checked FIRST, even before placeholder
        # guard, because GSTIN is deterministic regardless of vendor name)
        gstin_result = self._layer2_gstin_match(vendor_gstin, ledgers)
        if gstin_result:
            logger.info("Layer 2 hit (GSTIN): %s → %s", vendor_gstin, gstin_result.recommended_ledger)
            return gstin_result

        # Guard: skip fuzzy/LLM matching if vendor name is a default placeholder
        # (GSTIN match above still runs — it's deterministic and trustworthy)
        if query_name.strip().lower() in _DEFAULT_PLACEHOLDERS:
            logger.warning(
                "Vendor name '%s' is a default placeholder — skipping fuzzy/LLM match",
                query_name,
            )
            return MatchResult(
                recommended_ledger="",
                confidence=0.0,
                match_type="no_match",
                reasoning=f"'{query_name}' is a default placeholder, not a real vendor name.",
                create_new=False,
            )

        ledger_names = [l["name"] for l in ledgers if l.get("name")]
        if not ledger_names:
            return MatchResult(
                recommended_ledger="",
                confidence=0.0,
                match_type="no_match",
                reasoning="Ledger list is empty.",
                create_new=True,
                suggested_new_name=query_name,
            )

        # Layer 1 — Mapping rules
        rule_result = self._layer1_mapping_rules(
            query_name, document_type, ledger_names,
            vendor_gstin=vendor_gstin, bill_context=bill_context,
        )
        if rule_result:
            logger.info("Layer 1 hit (mapping rule): %s → %s", query_name, rule_result.recommended_ledger)
            return rule_result

        # Layer 3 — Fuzzy string matching
        fuzzy_candidates = self._layer3_fuzzy_match(query_name, ledger_names)

        if fuzzy_candidates and fuzzy_candidates[0].score >= FUZZY_AUTO_ACCEPT:
            top = fuzzy_candidates[0]
            # Gap check: if the second-best is very close, the match is
            # ambiguous — send to LLM to disambiguate instead of auto-accepting.
            runner_up = fuzzy_candidates[1].score if len(fuzzy_candidates) > 1 else 0.0
            gap = top.score - runner_up

            if gap >= FUZZY_MIN_GAP:
                logger.info(
                    "Layer 3 hit (fuzzy %.2f, gap %.2f): %s → %s",
                    top.score, gap, query_name, top.name,
                )
                return MatchResult(
                    recommended_ledger=top.name,
                    confidence=top.score,
                    match_type="fuzzy",
                    reasoning=(
                        f"High fuzzy match: '{query_name}' ≈ '{top.name}' "
                        f"(score {top.score:.0%})"
                    ),
                    alternatives=self._alternatives_from_fuzzy(fuzzy_candidates[1:4]),
                    create_new=False,
                )
            else:
                logger.info(
                    "Layer 3 ambiguous (top %.2f, runner-up %.2f, gap %.2f < %.2f): "
                    "sending to LLM — %s",
                    top.score, runner_up, gap, FUZZY_MIN_GAP, query_name,
                )

        # Layer 4 — LLM with CoT (top-N candidates)
        if fuzzy_candidates:
            llm_result = await self._layer4_llm_party_match(
                query_name, fuzzy_candidates[:LLM_CANDIDATE_COUNT], bill_context,
            )
            logger.info(
                "Layer 4 result (LLM): %s → %s (conf %.2f)",
                query_name,
                llm_result.recommended_ledger,
                llm_result.confidence,
            )
            return llm_result

        # Nothing at all
        return MatchResult(
            recommended_ledger="",
            confidence=0.0,
            match_type="no_match",
            reasoning="No matching ledgers found.",
            create_new=True,
            suggested_new_name=query_name,
        )

    async def match_category(
        self,
        bill_data: Dict[str, Any],
        ledger_groups: Dict[str, List[Dict[str, Any]]],
    ) -> CategoryMatchResult:
        """Match transaction to the best expense/revenue category ledger.

        Category selection is inherently semantic (what was bought/sold?),
        so it always uses the LLM with CoT.  The candidate list is already
        small (grouped by category, typically 20-30 ledgers total).
        """
        if not ledger_groups:
            return CategoryMatchResult(
                recommended_ledger="",
                confidence=0.0,
                category="",
                reasoning="No category ledgers available.",
            )

        return await self._layer4_llm_category_match(bill_data, ledger_groups)

    async def match_single(
        self,
        query_name: str,
        available_names: List[str],
        match_type: str = "party",
    ) -> str:
        """Simplified matching — returns just the ledger name string.

        Drop-in replacement for the old match_single_ledger() in
        purchase.py / sales.py.

        For tax/charge types: fuzzy only (no LLM).
        For party/vendor/expense: full pipeline.
        """
        defaults = {
            "party": "Cash",
            "vendor": "Cash",
            "expense": "Purchase",
            "tax": "CGST",
            "charge": "Charges",
        }
        if not query_name or not available_names:
            return defaults.get(match_type, "Cash")

        # Tax & charge ledgers: fuzzy only — they're known exact names
        if match_type in ("tax", "charge"):
            candidates = self._layer3_fuzzy_match(query_name, available_names, top_n=3)
            if candidates and candidates[0].score >= 0.50:
                return candidates[0].name
            # No match found — return the ORIGINAL extracted name (e.g., "SGST",
            # "IGST") instead of defaulting to "CGST" for all taxes.
            return query_name

        # Party / vendor / expense: full pipeline
        ledger_dicts = [{"name": n} for n in available_names]
        result = await self.match_party(
            query_name=query_name,
            ledgers=ledger_dicts,
            document_type="purchase_invoice",
        )
        if result.recommended_ledger and result.recommended_ledger in available_names:
            return result.recommended_ledger

        return defaults.get(match_type, "Cash")

    # ──────────────────────────────────────────────────────────
    # Layer 1 — Mapping rules
    # ──────────────────────────────────────────────────────────

    def _layer1_mapping_rules(
        self,
        query_name: str,
        document_type: str,
        available_names: List[str],
        vendor_gstin: Optional[str] = None,
        bill_context: Optional[Dict[str, Any]] = None,
        ledger_slot: Optional[str] = None,
    ) -> Optional[MatchResult]:
        """Check user-defined mapping rules via MappingService.

        Passes the full bill context (GSTIN, amount, line items, voucher_type)
        so composite rules can branch on these fields instead of just the
        vendor name.

        ledger_slot ('party' | 'expense' | None) filters which rules are
        eligible. When set, only rules whose ledger_slot matches (or is
        NULL — legacy "either" rules) are considered. The
        available_names guard below still applies as a final safety net
        so a slot-labeled rule pointing at a now-missing ledger
        gracefully misses instead of recommending a ghost.
        """
        if not self._mapping_service:
            return None

        try:
            ctx = bill_context or {}
            tx = {
                "vendor_name": query_name,
                "party_name": query_name,
                "description": query_name,
                "vendor_gstin": vendor_gstin or ctx.get("vendor_gstin") or "",
                "amount": ctx.get("total_amount") or ctx.get("subtotal") or ctx.get("amount"),
                "total_amount": ctx.get("total_amount"),
                "voucher_type": ctx.get("voucher_type") or document_type,
                "narration": ctx.get("narration") or "",
                "line_items": ctx.get("line_items") or [],
                "hsn_codes": ctx.get("hsn_codes") or [],
            }
            matched = self._mapping_service.apply_rules(
                [tx], document_type, ledger_slot=ledger_slot,
            )

            suggested = matched[0].get("suggested_ledger") if matched else None
            if not suggested:
                return None

            # Verify the rule's target ledger still exists in Tally
            if suggested not in available_names:
                logger.info(
                    "Mapping rule suggested '%s' but it's not in available ledgers, skipping",
                    suggested,
                )
                return None

            return MatchResult(
                recommended_ledger=suggested,
                confidence=0.99,
                match_type="mapping_rule",
                reasoning=f"Matched by mapping rule: '{matched[0].get('rule_applied', '')}'",
                create_new=False,
            )
        except Exception as exc:
            logger.warning("Layer 1 mapping rules failed: %s", exc)
            return None

    # ──────────────────────────────────────────────────────────
    # Layer 2 — GSTIN exact match
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _layer2_gstin_match(
        vendor_gstin: Optional[str],
        ledgers: List[Dict[str, Any]],
    ) -> Optional[MatchResult]:
        """Exact GSTIN comparison — deterministic, instant."""
        if not vendor_gstin or len(vendor_gstin.strip()) < 15:
            return None

        gstin_upper = vendor_gstin.strip().upper()
        for ledger in ledgers:
            lg = (ledger.get("gstin") or "").strip().upper()
            if lg and lg == gstin_upper:
                return MatchResult(
                    recommended_ledger=ledger["name"],
                    confidence=0.99,
                    match_type="gstin_exact",
                    reasoning=f"GSTIN {gstin_upper} matched exactly.",
                    group=ledger.get("parent", ""),
                    create_new=False,
                )
        return None

    # ──────────────────────────────────────────────────────────
    # Layer 3 — Fuzzy string matching
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _layer3_fuzzy_match(
        query_name: str,
        ledger_names: List[str],
        top_n: int = LLM_CANDIDATE_COUNT,
    ) -> List[FuzzyCandidate]:
        """Score every ledger name and return the top-N sorted desc."""
        candidates = [
            FuzzyCandidate(name=name, score=compute_fuzzy_score(query_name, name))
            for name in ledger_names
        ]
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:top_n]

    # ──────────────────────────────────────────────────────────
    # Layer 4 — LLM with Chain-of-Thought
    # ──────────────────────────────────────────────────────────

    async def _layer4_llm_party_match(
        self,
        query_name: str,
        candidates: List[FuzzyCandidate],
        bill_context: Optional[Dict[str, Any]] = None,
    ) -> MatchResult:
        """LLM picks from pre-filtered candidates.

        Confidence = fuzzy score of the LLM's chosen ledger (never from
        the LLM itself).
        """
        candidate_names = {c.name for c in candidates}
        candidate_score_map = {c.name: c.score for c in candidates}

        # Build user prompt
        candidate_lines = "\n".join(
            f"  {i+1}. {c.name} (fuzzy score: {c.score:.0%})"
            for i, c in enumerate(candidates)
        )

        context_str = ""
        if bill_context:
            items = bill_context.get("line_items") or []
            item_names = [
                it.get("name", it.get("description", ""))
                for it in items[:5]
            ]
            items_summary = ", ".join(n for n in item_names if n) or "N/A"
            amount = bill_context.get("total_amount", "unknown")
            gstin = bill_context.get("vendor_gstin", "") or "not available"
            bill_text = (bill_context.get("bill_text") or "")[:200]
            context_str = (
                f"\nGSTIN on invoice: {gstin}"
                f"\nInvoice amount: {amount}"
                f"\nItems on bill: {items_summary}"
            )
            if bill_text:
                context_str += f"\nRaw bill text (first 200 chars): {bill_text}"

        user_prompt = (
            f'PARTY NAME ON INVOICE: "{query_name}"{context_str}\n\n'
            f"CANDIDATE LEDGERS ({len(candidates)} from {LLM_CANDIDATE_COUNT} shortlisted):\n"
            f"{candidate_lines}\n\n"
            f"Pick the best match following the steps above."
        )

        try:
            from openai import AsyncOpenAI
            # Cap the LLM call so a slow OpenAI response can't blow past the
            # frontend's 30s axios timeout and leave the suggestion spinner
            # stuck. On timeout we fall back to the top fuzzy candidate so the
            # user still gets a sensible default.
            client = AsyncOpenAI(api_key=self._openai_api_key, timeout=LLM_TIMEOUT_SECONDS)

            resp = await client.chat.completions.create(
                model=self._openai_model,
                temperature=0,
                max_tokens=600,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": PARTY_COT_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
            )
            ai = json.loads(resp.choices[0].message.content)
        except Exception as exc:
            logger.error("LLM party matching failed: %s", exc)
            return self._fuzzy_fallback(query_name, candidates)

        selected = ai.get("selected_ledger")
        reasoning = ai.get("analysis", "")
        is_no_match = ai.get("is_no_match", False)

        # LLM said no match
        if is_no_match or not selected:
            suggested = ai.get("suggested_new_name") or query_name
            return MatchResult(
                recommended_ledger="",
                confidence=0.0,
                match_type="no_match",
                reasoning=reasoning or "LLM found no suitable match.",
                alternatives=self._alternatives_from_fuzzy(candidates[:3]),
                create_new=True,
                suggested_new_name=suggested,
            )

        # Validate LLM's pick exists in our candidate list
        if selected in candidate_names:
            score = candidate_score_map[selected]
            return MatchResult(
                recommended_ledger=selected,
                confidence=score,
                match_type="llm_assisted",
                reasoning=reasoning,
                alternatives=self._alternatives_from_fuzzy(
                    [c for c in candidates[:4] if c.name != selected]
                ),
                create_new=False,
            )

        # LLM returned something not in the candidate list
        logger.warning(
            "LLM returned '%s' which is not in candidates, falling back to top fuzzy",
            selected,
        )
        return self._fuzzy_fallback(query_name, candidates, reasoning)

    async def _layer4_llm_category_match(
        self,
        bill_data: Dict[str, Any],
        ledger_groups: Dict[str, List[Dict[str, Any]]],
    ) -> CategoryMatchResult:
        """LLM-based expense/revenue ledger categorization with CoT."""
        # Build the prompt
        items = bill_data.get("line_items") or []
        if items:
            items_str = "\n".join(
                f"  - {it.get('name', it.get('description', 'Item'))}: "
                f"Qty {it.get('qty', it.get('quantity', 0))}, "
                f"Rate {it.get('rate', 0)}, Amt {it.get('amount', 0)}"
                for it in items
            )
        else:
            items_str = "  (no items extracted)"

        tax = bill_data.get("tax_details", {})

        # Flatten all category ledger names for validation
        all_category_names: set[str] = set()
        groups_str = ""
        for group_key, ledgers in ledger_groups.items():
            label = group_key.replace("_", " ").title()
            ledger_lines = []
            for l in ledgers:
                name = l["name"] if isinstance(l, dict) else str(l)
                all_category_names.add(name)
                bal = ""
                if isinstance(l, dict):
                    b = l.get("closing_balance", "0")
                    if b and b != "0" and b != "0.00":
                        bal = f" (Bal: {b})"
                ledger_lines.append(f"  - {name}{bal}")
            groups_str += f"\n--- {label} ---\n" + "\n".join(ledger_lines) + "\n"

        hsn_codes = ", ".join(bill_data.get("hsn_codes", [])) or "N/A"

        # Determine voucher context for better matching
        vtype = bill_data.get("voucher_type", "auto")
        if vtype in ("purchase_bill", "purchase"):
            context_hint = "This is a PURCHASE bill — classify the expense/purchase type based on the items."
        elif vtype in ("sales_invoice", "sales"):
            context_hint = "This is a SALES invoice — classify the income/revenue type based on the items."
        else:
            context_hint = "Classify based on the items below."

        user_prompt = f"""INVOICE DETAILS:
Vendor/Party: {bill_data.get('vendor_name', 'Unknown')}
{context_hint}

LINE ITEMS (analyze these to determine the correct ledger):
{items_str}

Totals: Subtotal={bill_data.get('subtotal', bill_data.get('total_amount', 0))}, Tax={tax.get('total_tax', 0)}, Total={bill_data.get('total_amount', 0)}
GST: CGST={tax.get('cgst', 0)} | SGST={tax.get('sgst', 0)} | IGST={tax.get('igst', 0)}
HSN/SAC: {hsn_codes}

AVAILABLE LEDGERS (pick the one whose NAME best describes the items above):
{groups_str}

Which ledger best matches what was bought/sold in the items above?"""

        try:
            from openai import AsyncOpenAI
            # See LLM_TIMEOUT_SECONDS — bounded so the suggestion spinner
            # can't hang behind a slow OpenAI response.
            client = AsyncOpenAI(api_key=self._openai_api_key, timeout=LLM_TIMEOUT_SECONDS)

            resp = await client.chat.completions.create(
                model=self._openai_model,
                temperature=0,
                max_tokens=500,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": CATEGORY_COT_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
            )
            ai = json.loads(resp.choices[0].message.content)
        except Exception as exc:
            logger.error("LLM category matching failed: %s", exc)
            return CategoryMatchResult(
                recommended_ledger="",
                confidence=0.0,
                category="",
                reasoning=f"LLM categorization failed: {exc}",
            )

        selected = ai.get("selected_ledger", "")
        category = ai.get("category", "")
        reasoning = ai.get("analysis", "")
        gst_rate = ai.get("gst_rate_detected", "unknown")
        raw_alts = ai.get("alternatives", [])

        logger.info(
            "Category match: items=%s → selected='%s', category='%s', reasoning='%s'",
            [it.get("name", it.get("description", "")) for it in items[:3]],
            selected, category, reasoning,
        )

        # Validate
        if selected and selected not in all_category_names:
            logger.warning(
                "LLM category returned '%s' not in ledger list, attempting fuzzy recovery",
                selected,
            )
            all_names_list = list(all_category_names)
            recovery = self._layer3_fuzzy_match(selected, all_names_list, top_n=1)
            if recovery and recovery[0].score >= 0.60:
                selected = recovery[0].name
            else:
                selected = ""

        # Compute confidence for the category match
        # For category matching the "query" is the bill description, not a name,
        # so we use a heuristic: if the LLM found a match, confidence is based
        # on how specific the selection is.
        if selected:
            confidence = 0.85  # base confidence for a valid LLM category pick
            # Boost if there's a GST-rate-specific ledger match
            if gst_rate not in ("unknown", "exempt") and gst_rate.replace("%", "") in selected.lower():
                confidence = 0.92
        else:
            confidence = 0.0

        alternatives = [
            {"ledger": a.get("name", ""), "confidence": 0.0, "reason": a.get("reason", "")}
            for a in raw_alts[:3]
            if a.get("name") in all_category_names
        ]

        return CategoryMatchResult(
            recommended_ledger=selected,
            confidence=confidence,
            category=category,
            reasoning=reasoning,
            gst_rate_detected=gst_rate,
            alternatives=alternatives,
        )

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _fuzzy_fallback(
        query_name: str,
        candidates: List[FuzzyCandidate],
        extra_reasoning: str = "",
    ) -> MatchResult:
        """Fall back to the top fuzzy candidate when the LLM is
        unavailable or returns an invalid result."""
        if not candidates:
            return MatchResult(
                recommended_ledger="",
                confidence=0.0,
                match_type="no_match",
                reasoning="No candidates available.",
                create_new=True,
                suggested_new_name=query_name,
            )
        top = candidates[0]
        reasoning = (
            f"Fuzzy fallback (LLM unavailable or invalid): "
            f"'{query_name}' ≈ '{top.name}' (score {top.score:.0%})"
        )
        if extra_reasoning:
            reasoning = f"{extra_reasoning}. {reasoning}"

        return MatchResult(
            recommended_ledger=top.name,
            confidence=top.score,
            match_type="fuzzy_fallback",
            reasoning=reasoning,
            alternatives=LedgerMatcher._alternatives_from_fuzzy(candidates[1:4]),
            create_new=False,
        )

    @staticmethod
    def _alternatives_from_fuzzy(
        candidates: List[FuzzyCandidate],
    ) -> List[Dict[str, Any]]:
        return [
            {"ledger": c.name, "confidence": c.score, "reason": f"fuzzy score {c.score:.0%}"}
            for c in candidates
            if c.score > 0.0
        ]
