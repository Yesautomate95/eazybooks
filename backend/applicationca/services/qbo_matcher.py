"""
Match extracted entity names to existing QBO records.

Layered resolution (each fires only if previous misses):
  0. Alias lookup       — user-confirmed mappings persisted in qbo_aliases.
                          Hit = confidence 1.0, method="alias".
  1. Canonical exact    — both sides run through `_canonicalize` (strips
                          Pvt/Ltd/Private/Limited, periods, parens, case,
                          whitespace). Hit = confidence 1.0, method="exact".
  2. Email              — when extraction supplied one. Confidence 0.95.
  3. Fuzzy              — rapidfuzz token_set_ratio on canonical forms.
                          Auto-accept above 0.90, LLM-disambiguate 0.60-0.90.

Returns a dict per match:
    {
        "matched": bool,
        "list_id": str | None,
        "name": str,
        "confidence": float,
        "method": "alias" | "exact" | "email" | "fuzzy" | "llm" | "new",
        "candidate_name": str | None,
        "top_candidates": [{"list_id": ..., "name": ..., "score": ...}, ...]
                          # Top-3 fuzzy candidates regardless of whether
                          # `matched` is True; lets the UI offer Tier-2
                          # "Did you mean one of these?" suggestions.
    }

If no good match exists, the caller can either create the entity (returning
method="new") or surface the row to the UI for user choice.
"""

import json
import logging
import re
from typing import Any, Dict, List, Mapping, Optional

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


_FUZZY_AUTO_ACCEPT = 0.90
_FUZZY_MIN_LLM = 0.60
_TOP_CANDIDATE_COUNT = 3
_TOP_CANDIDATE_MIN_SCORE = 0.40  # don't surface candidates below this — pure noise


# --- Canonicalization -------------------------------------------------------
# Suffix and noise patterns we strip so cosmetic variants collapse to one key.
# Order matters: longer patterns first (e.g. "private limited" before "limited"
# to avoid leaving a stray "private").
_NOISE_SUFFIXES = [
    "private limited",
    "private ltd",
    "pvt limited",
    "pvt ltd",
    "pvt. ltd.",
    "p ltd",
    "p. ltd.",
    "limited",
    "ltd",
    "ltd.",
    "incorporated",
    "inc",
    "inc.",
    "llp",
    "l.l.p.",
    "co",
    "co.",
    "company",
    "corporation",
    "corp",
    "corp.",
    "and company",
    "& co",
    "& co.",
]

# Sorted longest first to avoid partial matches
_NOISE_SUFFIXES_SORTED = sorted(set(_NOISE_SUFFIXES), key=lambda s: -len(s))

_PUNCT_RE = re.compile(r"[^\w\s&]")  # keep & since "X & Y" carries meaning
_SPACE_RE = re.compile(r"\s+")
_AMP_RE = re.compile(r"\s*&\s*")  # normalize " & " spacing


def _canonicalize(name: str) -> str:
    """Lowercase, drop punctuation, strip common legal-entity suffixes, collapse
    whitespace. Both extracted names and QBO master names are passed through
    this before comparison so cosmetic variants land on the same key.

    Examples:
      "AGC Asia Pacific (India) Pvt. Ltd."  → "agc asia pacific india"
      "AGC ASIA PACIFIC INDIA PVT LTD"       → "agc asia pacific india"
      "M/s. Acme & Co."                     → "ms acme & co" → "ms acme"
    """
    if not name:
        return ""
    s = name.lower()
    # Drop punctuation early — easier to match suffixes
    s = _PUNCT_RE.sub(" ", s)
    s = _AMP_RE.sub(" & ", s)
    s = _SPACE_RE.sub(" ", s).strip()
    # Iteratively strip trailing suffixes (some names have stacked: "X Pvt Ltd Co")
    changed = True
    while changed:
        changed = False
        for suf in _NOISE_SUFFIXES_SORTED:
            if s == suf:
                # name was *only* the suffix — keep it rather than blanking
                break
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
                break
    return s


def _norm(s: str) -> str:
    """Legacy alias kept for callers; canonical form is the new normalizer."""
    return _canonicalize(s)


def _exact_index(records: List[Dict[str, Any]], key: str = "name") -> Dict[str, Dict[str, Any]]:
    """Map canonical(name) -> record. Multiple inputs that canonicalize the
    same way collapse — last one wins (acceptable for a sandbox-grade matcher;
    a stricter version would flag the ambiguity)."""
    return {_canonicalize(r.get(key, "")): r for r in records if r.get(key)}


def _email_index(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in records:
        email = (r.get("email") or "").lower().strip()
        if email:
            out[email] = r
    return out


def _ranked_fuzzy(name: str, records: List[Dict[str, Any]], top_n: int = _TOP_CANDIDATE_COUNT) -> List[Dict[str, Any]]:
    """Return up to top_n records ranked by fuzzy similarity, each annotated
    with `score` in [0, 1]. Filters scores below _TOP_CANDIDATE_MIN_SCORE.
    """
    if not name or not records:
        return []
    target = _canonicalize(name)
    scored: List[Dict[str, Any]] = []
    for r in records:
        cand_name = r.get("name") or ""
        if not cand_name:
            continue
        score = fuzz.token_set_ratio(target, _canonicalize(cand_name)) / 100.0
        if score < _TOP_CANDIDATE_MIN_SCORE:
            continue
        scored.append({**r, "score": round(score, 3)})
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_n]


def _top_candidates_payload(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact, UI-friendly shape — drop fields the picker doesn't need."""
    return [
        {
            "list_id": r.get("list_id"),
            "name": r.get("name"),
            "score": r.get("score"),
        }
        for r in ranked
        if r.get("list_id")
    ]


async def _llm_disambiguate(
    name: str,
    candidates: List[Dict[str, Any]],
    *,
    entity_label: str,
    llm_async,  # async-callable: prompt -> str
) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None

    prompt = (
        f"You are matching a {entity_label} extracted from a document to an "
        f"existing record in QuickBooks Online.\n\n"
        f"Extracted name: {name!r}\n\n"
        f"Candidates (top fuzzy matches):\n"
        + json.dumps(
            [{"list_id": c.get("list_id"), "name": c.get("name"), "company": c.get("company")} for c in candidates],
            indent=2,
        )
        + "\n\nReturn STRICT JSON: {\"list_id\": <chosen list_id or null>, "
        "\"reason\": <one short sentence>}. If none of the candidates clearly "
        "refer to the same entity, return {\"list_id\": null, ...}."
    )

    try:
        raw = await llm_async(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"LLM disambiguation failed: {exc}")
        return None
    try:
        decoded = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return None
    chosen_id = decoded.get("list_id")
    if not chosen_id:
        return None
    return next((c for c in candidates if c.get("list_id") == chosen_id), None)


async def match_entity(
    extracted_name: str,
    extracted_email: Optional[str],
    candidates: List[Dict[str, Any]],
    *,
    entity_label: str = "customer",
    llm_async=None,
    aliases: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> Dict[str, Any]:
    """Match `extracted_name` against `candidates`.

    `aliases`: optional map of {alias_normalized -> {qbo_entity_id, qbo_entity_name, ...}}
    keyed by the same normalization _norm()/_canonicalize() produces. When the
    matcher finds a hit here it short-circuits with confidence 1.0. Caller is
    expected to pre-filter by entity_type so a customer alias doesn't bleed
    into an item match.
    """
    base = {
        "matched": False,
        "list_id": None,
        # name is None until we actually match something. Previously this was the
        # raw extracted query, which leaked the line DESCRIPTION into the account
        # ledger when no account matched. Callers read `candidate_name` for the
        # "did you mean / create new" UI; matched branches set `name` explicitly.
        "name": None,
        "confidence": 0.0,
        "method": "new",
        "candidate_name": extracted_name,
        "top_candidates": [],
    }

    if not extracted_name:
        return base

    # Precompute fuzzy ranking once — used for both top_candidates payload and
    # the fuzzy decision below. (Same call so we don't pay the cost twice.)
    fuzzy_ranked = _ranked_fuzzy(extracted_name, candidates)
    top_payload = _top_candidates_payload(fuzzy_ranked)

    # Layer 0: alias hit
    if aliases:
        canon = _canonicalize(extracted_name)
        alias_hit = aliases.get(canon)
        if alias_hit and alias_hit.get("qbo_entity_id"):
            return {
                **base,
                "matched": True,
                "list_id": alias_hit["qbo_entity_id"],
                "name": alias_hit.get("qbo_entity_name") or extracted_name,
                "confidence": 1.0,
                "method": "alias",
                "top_candidates": top_payload,
            }

    # Layer 1: canonical exact
    idx = _exact_index(candidates)
    hit = idx.get(_canonicalize(extracted_name))
    if hit:
        return {
            **base,
            "matched": True,
            "list_id": hit.get("list_id"),
            "name": hit.get("name") or extracted_name,
            "confidence": 1.0,
            "method": "exact",
            "top_candidates": top_payload,
        }

    # Layer 2: email (when both sides have it)
    if extracted_email:
        eidx = _email_index(candidates)
        hit = eidx.get(extracted_email.lower().strip())
        if hit:
            return {
                **base,
                "matched": True,
                "list_id": hit.get("list_id"),
                "name": hit.get("name") or extracted_name,
                "confidence": 0.95,
                "method": "email",
                "top_candidates": top_payload,
            }

    # Layer 3: fuzzy
    if not fuzzy_ranked:
        return {**base, "top_candidates": top_payload}
    best = fuzzy_ranked[0]
    score = best["score"]

    if score >= _FUZZY_AUTO_ACCEPT:
        return {
            **base,
            "matched": True,
            "list_id": best.get("list_id"),
            "name": best.get("name") or extracted_name,
            "confidence": round(score, 3),
            "method": "fuzzy",
            "top_candidates": top_payload,
        }

    if score >= _FUZZY_MIN_LLM and llm_async is not None:
        chosen = await _llm_disambiguate(extracted_name, fuzzy_ranked, entity_label=entity_label, llm_async=llm_async)
        if chosen:
            return {
                **base,
                "matched": True,
                "list_id": chosen.get("list_id"),
                "name": chosen.get("name") or extracted_name,
                "confidence": round(score, 3),
                "method": "llm",
                "top_candidates": top_payload,
            }

    # No confident match — caller will either prompt the user or auto-create.
    # Surface the ranked candidates so the UI can offer "Did you mean…".
    return {
        **base,
        "candidate_name": extracted_name,
        "confidence": round(score, 3),
        "top_candidates": top_payload,
    }


async def match_account_for_line(
    description: str,
    candidates: List[Dict[str, Any]],
    *,
    item_meta: Optional[Dict[str, Any]] = None,
    llm_async=None,
) -> Dict[str, Any]:
    """Semantically map a purchase/bank line (WHAT was bought) to the best
    expense ACCOUNT — the QB analog of Tally's ledger_matcher.match_category.

    Fuzzy string-matching a free-text line description against GL account names
    fails (a description rarely shares tokens with an account name like
    "Office Supplies"), so we ask an LLM to reason "what was purchased -> which
    expense account". Falls back to a no-match (name=None) when the LLM is
    unavailable or unsure — never leaks the description as the account name.
    """
    base = {
        "matched": False, "list_id": None, "name": None, "confidence": 0.0,
        "method": "new", "candidate_name": description, "top_candidates": [],
    }
    desc = (description or "").strip()
    account_list = [
        {"list_id": c.get("list_id"), "name": c.get("name"), "type": c.get("account_type")}
        for c in candidates if c.get("list_id") and c.get("name")
    ]
    if not desc or not account_list or llm_async is None:
        return base

    meta_line = ""
    if item_meta:
        bits = [f"{k}={item_meta[k]}" for k in ("qty", "rate", "amount", "hsn_code")
                if item_meta.get(k) not in (None, "")]
        if bits:
            meta_line = f"\nLine details: {', '.join(bits)}"

    prompt = (
        "You are an accountant categorizing a purchase line into the correct "
        "EXPENSE ACCOUNT in QuickBooks Online. Reason about WHAT was purchased "
        "and which account it belongs to.\n\n"
        f"Line description: {desc!r}{meta_line}\n\n"
        "Available expense accounts:\n"
        + json.dumps(account_list, indent=2)
        + "\n\nReturn STRICT JSON: {\"list_id\": <chosen account list_id or null>, "
        "\"reason\": <one short sentence>}. Pick the single best account; if none "
        "is a reasonable fit, return {\"list_id\": null}."
    )
    try:
        raw = await llm_async(prompt)
        decoded = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception as exc:  # noqa: BLE001
        logger.warning("account categorization LLM failed: %s", exc)
        return base
    chosen_id = decoded.get("list_id")
    if not chosen_id:
        return base
    chosen = next((c for c in candidates if str(c.get("list_id")) == str(chosen_id)), None)
    if not chosen:
        return base
    return {
        **base,
        "matched": True,
        "list_id": chosen.get("list_id"),
        "name": chosen.get("name"),
        "confidence": 0.8,
        "method": "llm_category",
        "reason": decoded.get("reason"),
    }


async def match_many(
    rows: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    *,
    entity_label: str = "customer",
    llm_async=None,
    aliases: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Match a list of {name, email} pairs against QBO records. Used for line
    items / bulk customer matching."""
    out = []
    for row in rows:
        name = row.get("name") or row.get("description") or ""
        email = row.get("email")
        out.append(await match_entity(name, email, candidates, entity_label=entity_label, llm_async=llm_async, aliases=aliases))
    return out
