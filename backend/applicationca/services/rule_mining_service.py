"""
Rule mining service — turns observed history + feedback into proposed
mapping_rules so users don't have to write rules by hand.

Reads from:
  * tally_transaction_history  → stable vendor→ledger and gstin-prefix patterns
  * ledger_suggestion_feedback → correction patterns (user overrode AI)

Returns a ranked list of `{kind, rule_proposal, evidence, explanation}`
dicts. The caller (mapping_rules controller) renders these as suggestions
in MappingRulesPanel; user clicks Accept to materialize a row in
mapping_rules.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import (
    get_connection,
    return_connection,
    get_mapping_rules,
)

logger = logging.getLogger(__name__)


# Confidence threshold below which we don't propose a vendor rule even if
# usage_count is high — too much disagreement on what the vendor maps to.
_CONSISTENCY_THRESHOLD = 0.80
_MIN_SUPPORT_DEFAULT = 3
_CORRECTION_MIN_SUPPORT = 2


def suggest_rules(
    user_id: str,
    company_name: str,
    document_type: Optional[str] = None,
    min_support: int = _MIN_SUPPORT_DEFAULT,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Top entry point. Returns ranked rule proposals.

    document_type filters which voucher contexts to mine (purchase_invoice,
    sales_invoice, or None for both).
    """
    if not user_id or not company_name:
        return []

    existing = _existing_rule_signatures(user_id, company_name, document_type)

    proposals: List[Dict[str, Any]] = []
    proposals.extend(_mine_correction_patterns(user_id, company_name, document_type, _CORRECTION_MIN_SUPPORT))
    proposals.extend(_mine_vendor_to_ledger(user_id, company_name, document_type, min_support))
    proposals.extend(_mine_gstin_prefix(user_id, company_name, document_type, min_support))
    proposals.extend(_mine_amount_band_voucher(user_id, company_name, document_type, min_support))

    # Dedupe against existing rules and against each other (the highest-evidence
    # proposal wins when two proposals point to the same target).
    seen: Dict[str, Dict[str, Any]] = {}
    for p in proposals:
        sig = _signature(p["rule_proposal"])
        if sig in existing:
            continue
        existing_p = seen.get(sig)
        if existing_p is None or p["evidence"]["occurrences"] > existing_p["evidence"]["occurrences"]:
            seen[sig] = p

    ranked = sorted(
        seen.values(),
        key=lambda p: (
            p["evidence"]["occurrences"],
            p["evidence"].get("consistency", 0),
        ),
        reverse=True,
    )
    return ranked[:limit]


# ---------------------------------------------------------------------------
# Strategy 1 — vendor → ledger (party + expense)
# ---------------------------------------------------------------------------

def _mine_vendor_to_ledger(
    user_id: str, company_name: str, document_type: Optional[str], min_support: int
) -> List[Dict[str, Any]]:
    """Propose a 'vendor'-type rule when a vendor consistently maps to one ledger."""
    voucher_filter = _voucher_filter(document_type)

    sql = f"""
    WITH per_vendor AS (
        SELECT source_identifier, mapping_type, target_value,
               usage_count, last_used_at, voucher_type,
               SUM(usage_count) OVER (PARTITION BY source_identifier, mapping_type) AS total_uses,
               COUNT(*)         OVER (PARTITION BY source_identifier, mapping_type) AS distinct_targets
          FROM tally_transaction_history
         WHERE user_id = %s
           AND company_name = %s
           AND mapping_type IN ('party_ledger', 'expense_ledger')
           {voucher_filter}
    )
    SELECT source_identifier, mapping_type, target_value, usage_count,
           total_uses, distinct_targets, last_used_at, voucher_type
      FROM per_vendor
     WHERE total_uses >= %s
       AND (usage_count::float / NULLIF(total_uses, 0)) >= %s
     ORDER BY total_uses DESC, usage_count DESC
    """

    rows = _query(sql, (user_id, company_name, min_support, _CONSISTENCY_THRESHOLD))
    out: List[Dict[str, Any]] = []
    for r in rows:
        consistency = (r["usage_count"] or 0) / (r["total_uses"] or 1)
        out.append({
            "kind": "vendor_to_ledger",
            "rule_proposal": {
                "rule_name": f"Auto · {r['source_identifier'][:30]} → {r['target_value'][:30]}",
                "rule_type": "vendor",
                "document_type": _doc_type_for_history(r["mapping_type"], r.get("voucher_type"), document_type),
                "vendor_name": r["source_identifier"],
                "target_ledger": r["target_value"],
                "priority": 6 if consistency >= 0.95 else 5,
            },
            "evidence": {
                "occurrences": int(r["total_uses"] or 0),
                "consistency": round(consistency, 3),
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
                "distinct_targets": int(r["distinct_targets"] or 1),
                "mapping_type": r["mapping_type"],
            },
            "explanation": (
                f"Vendor '{r['source_identifier']}' → "
                f"{'party' if r['mapping_type'] == 'party_ledger' else 'expense'} "
                f"ledger '{r['target_value']}' in "
                f"{r['usage_count']} of {r['total_uses']} past invoices "
                f"({round(consistency * 100)}% consistency)."
            ),
        })
    return out


# ---------------------------------------------------------------------------
# Strategy 2 — correction-driven (user override patterns)
# ---------------------------------------------------------------------------

def _mine_correction_patterns(
    user_id: str, company_name: str, document_type: Optional[str], min_support: int
) -> List[Dict[str, Any]]:
    """Propose a high-priority rule when the user has overridden the AI to the
    same ledger ≥ N times for the same vendor."""
    voucher_filter = _voucher_filter(document_type, column="voucher_type")
    sql = f"""
    SELECT vendor_name, field, picked_ledger,
           COUNT(*) AS overrides,
           MAX(created_at) AS last_used_at,
           MAX(voucher_type) AS voucher_type
      FROM ledger_suggestion_feedback
     WHERE user_id = %s
       AND company_name = %s
       AND was_override = TRUE
       AND vendor_name IS NOT NULL AND vendor_name <> ''
       {voucher_filter}
     GROUP BY vendor_name, field, picked_ledger
    HAVING COUNT(*) >= %s
     ORDER BY overrides DESC
    """

    rows = _query(sql, (user_id, company_name, min_support))
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "kind": "correction_pattern",
            "rule_proposal": {
                "rule_name": f"Correction · {r['vendor_name'][:25]} → {r['picked_ledger'][:25]}",
                "rule_type": "vendor",
                "document_type": _doc_type_for_voucher_field(r["field"], r.get("voucher_type"), document_type),
                "vendor_name": r["vendor_name"],
                "target_ledger": r["picked_ledger"],
                "priority": 8,   # High priority — user has actively pushed back on the AI
            },
            "evidence": {
                "occurrences": int(r["overrides"] or 0),
                "consistency": 1.0,
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            },
            "explanation": (
                f"You corrected the AI's pick for '{r['vendor_name']}' to "
                f"'{r['picked_ledger']}' {r['overrides']} time(s). Lock it in?"
            ),
        })
    return out


# ---------------------------------------------------------------------------
# Strategy 3 — GSTIN prefix → expense ledger
# ---------------------------------------------------------------------------

def _mine_gstin_prefix(
    user_id: str, company_name: str, document_type: Optional[str], min_support: int
) -> List[Dict[str, Any]]:
    """Propose a composite rule when a GSTIN prefix (state code) consistently
    maps to one expense ledger."""
    voucher_filter = _voucher_filter(document_type)
    sql = f"""
    WITH per_prefix AS (
        SELECT SUBSTR(gstin, 1, 2) AS prefix, target_value, mapping_type, voucher_type,
               SUM(usage_count) AS uses,
               MAX(last_used_at) AS last_used_at
          FROM tally_transaction_history
         WHERE user_id = %s
           AND company_name = %s
           AND gstin IS NOT NULL AND LENGTH(gstin) >= 2
           AND mapping_type IN ('expense_ledger')
           {voucher_filter}
         GROUP BY SUBSTR(gstin, 1, 2), target_value, mapping_type, voucher_type
    ),
    totals AS (
        SELECT prefix, SUM(uses) AS total_uses
          FROM per_prefix
         GROUP BY prefix
    )
    SELECT p.prefix, p.target_value, p.uses, t.total_uses, p.last_used_at, p.voucher_type
      FROM per_prefix p
      JOIN totals t USING (prefix)
     WHERE t.total_uses >= %s
       AND (p.uses::float / NULLIF(t.total_uses, 0)) >= %s
     ORDER BY t.total_uses DESC
    """

    rows = _query(sql, (user_id, company_name, min_support, _CONSISTENCY_THRESHOLD))
    out: List[Dict[str, Any]] = []
    for r in rows:
        consistency = (r["uses"] or 0) / (r["total_uses"] or 1)
        out.append({
            "kind": "gstin_prefix",
            "rule_proposal": {
                "rule_name": f"GSTIN state {r['prefix']} → {r['target_value'][:25]}",
                "rule_type": "composite",
                "document_type": _doc_type_for_voucher_field("expense_ledger", r.get("voucher_type"), document_type),
                "conditions": {
                    "op": "AND",
                    "clauses": [
                        {"field": "gstin_prefix", "operator": "equals", "value": r["prefix"]},
                    ],
                },
                "target_ledger": r["target_value"],
                "priority": 4,
            },
            "evidence": {
                "occurrences": int(r["total_uses"] or 0),
                "consistency": round(consistency, 3),
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            },
            "explanation": (
                f"Vendors with GSTIN starting '{r['prefix']}' ({_state_name(r['prefix'])}) "
                f"posted to '{r['target_value']}' in {r['uses']} of {r['total_uses']} cases "
                f"({round(consistency * 100)}% consistency)."
            ),
        })
    return out


# ---------------------------------------------------------------------------
# Strategy 4 — amount band + voucher type → ledger
# ---------------------------------------------------------------------------

def _mine_amount_band_voucher(
    user_id: str, company_name: str, document_type: Optional[str], min_support: int
) -> List[Dict[str, Any]]:
    """Propose a composite rule when an (amount_band, voucher_type) pair
    consistently maps to one expense ledger across many distinct vendors."""
    voucher_filter = _voucher_filter(document_type)
    sql = f"""
    WITH per_band AS (
        SELECT amount_band, voucher_type, target_value,
               SUM(usage_count) AS uses,
               COUNT(DISTINCT source_identifier) AS distinct_vendors,
               MAX(last_used_at) AS last_used_at
          FROM tally_transaction_history
         WHERE user_id = %s
           AND company_name = %s
           AND amount_band IS NOT NULL
           AND voucher_type IS NOT NULL
           AND mapping_type = 'expense_ledger'
           {voucher_filter}
         GROUP BY amount_band, voucher_type, target_value
    ),
    totals AS (
        SELECT amount_band, voucher_type, SUM(uses) AS total_uses
          FROM per_band
         GROUP BY amount_band, voucher_type
    )
    SELECT p.amount_band, p.voucher_type, p.target_value,
           p.uses, t.total_uses, p.distinct_vendors, p.last_used_at
      FROM per_band p
      JOIN totals t USING (amount_band, voucher_type)
     WHERE t.total_uses >= %s
       AND p.distinct_vendors >= 2
       AND (p.uses::float / NULLIF(t.total_uses, 0)) >= %s
     ORDER BY t.total_uses DESC
    """

    rows = _query(sql, (user_id, company_name, min_support, _CONSISTENCY_THRESHOLD))
    out: List[Dict[str, Any]] = []
    for r in rows:
        consistency = (r["uses"] or 0) / (r["total_uses"] or 1)
        lo, hi = _band_range(r["amount_band"])
        if lo is None:
            continue
        clauses = [
            {"field": "voucher_type", "operator": "equals", "value": r["voucher_type"]},
        ]
        if hi is None:
            clauses.append({"field": "amount", "operator": "gte", "value": lo})
        else:
            clauses.append({"field": "amount", "operator": "between", "value": [lo, hi]})
        out.append({
            "kind": "amount_band_voucher",
            "rule_proposal": {
                "rule_name": f"{r['voucher_type']} {r['amount_band']} → {r['target_value'][:20]}",
                "rule_type": "composite",
                "document_type": _doc_type_for_voucher_field("expense_ledger", r["voucher_type"], document_type),
                "conditions": {"op": "AND", "clauses": clauses},
                "target_ledger": r["target_value"],
                "priority": 3,
            },
            "evidence": {
                "occurrences": int(r["total_uses"] or 0),
                "consistency": round(consistency, 3),
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
                "distinct_vendors": int(r["distinct_vendors"] or 0),
            },
            "explanation": (
                f"{r['voucher_type']} invoices in {_band_label(r['amount_band'])} mapped to "
                f"'{r['target_value']}' across {r['distinct_vendors']} vendors "
                f"({round(consistency * 100)}% consistency)."
            ),
        })
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query(sql: str, params: tuple) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall() or []
    except psycopg2.Error as exc:
        logger.warning("rule_mining query failed: %s", exc)
        return []
    finally:
        return_connection(conn)


def _voucher_filter(document_type: Optional[str], column: str = "voucher_type") -> str:
    """Build a SQL fragment that filters by the voucher_type for the requested
    document_type. Empty string when no filter.
    """
    if not document_type:
        return ""
    mapping = {
        "purchase_invoice": "purchase_bill",
        "sales_invoice": "sales_invoice",
    }
    target = mapping.get(document_type)
    if not target:
        return ""
    # NB: the value is hard-coded literal — never user input — so it's safe in the f-string.
    return f"AND ({column} = '{target}' OR {column} IS NULL)"


def _existing_rule_signatures(
    user_id: str, company_name: str, document_type: Optional[str]
) -> set:
    rules = get_mapping_rules(user_id, company_name, document_type, active_only=False) or []
    return {_signature(r) for r in rules}


def _signature(rule: Dict[str, Any]) -> str:
    """A stable signature so we can dedup proposals against existing rules."""
    return "|".join([
        (rule.get("rule_type") or ""),
        (rule.get("vendor_name") or ""),
        str(rule.get("target_ledger") or ""),
        str((rule.get("conditions") or {}).get("clauses") if isinstance(rule.get("conditions"), dict) else ""),
    ]).lower()


def _doc_type_for_history(mapping_type: str, voucher_type: Optional[str],
                          fallback: Optional[str]) -> str:
    if voucher_type == "sales_invoice":
        return "sales_invoice"
    if voucher_type == "purchase_bill":
        return "purchase_invoice"
    if fallback:
        return fallback
    return "purchase_invoice"


def _doc_type_for_voucher_field(field: str, voucher_type: Optional[str],
                                fallback: Optional[str]) -> str:
    return _doc_type_for_history(field, voucher_type, fallback)


def _band_range(band: str) -> Tuple[Optional[float], Optional[float]]:
    return {
        "lt_5k": (0.0, 5000.0),
        "5k_50k": (5000.0, 50000.0),
        "50k_500k": (50000.0, 500000.0),
        "gt_500k": (500000.0, None),
    }.get(band, (None, None))


def _band_label(band: str) -> str:
    return {
        "lt_5k": "under ₹5K",
        "5k_50k": "₹5K–₹50K",
        "50k_500k": "₹50K–₹5L",
        "gt_500k": "over ₹5L",
    }.get(band, band)


# GSTIN state codes — small lookup so the explanation reads like English.
_STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
    "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "36": "Telangana", "37": "Andhra Pradesh",
}


def _state_name(prefix: str) -> str:
    return _STATE_CODES.get(prefix, "Unknown state")
