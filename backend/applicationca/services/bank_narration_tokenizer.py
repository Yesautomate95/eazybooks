"""
Tokenize raw bank/UPI transaction narrations to recover the underlying party name.

Raw bank lines look like:
    "UPI ZOMATO HDFC 223020100330 54322"
    "NEFT-ICICI-AMAZON-IN-N123456789"
    "POS HDFC SWIGGY BANGALORE 01/05/2026"

The fuzzy matcher needs "ZOMATO" / "AMAZON IN" / "SWIGGY BANGALORE" — not the
boilerplate payment rail prefix, bank code, or reference number. This module
strips that boilerplate so the matcher gets a clean candidate name.

Result shape:
    {
        "cleaned":  "ZOMATO",          # joined remaining tokens (matcher input)
        "tokens":   ["ZOMATO"],         # individual surviving tokens
        "stripped": ["UPI", "HDFC", "223020100330", "54322"],
    }

If everything would be stripped, "cleaned" falls back to the original input so
the downstream matcher still gets a chance.
"""

from __future__ import annotations

import re
from typing import Dict, List


PAYMENT_RAIL_TOKENS = frozenset({
    "UPI", "NEFT", "IMPS", "RTGS", "POS", "ECS", "ACH",
    "ATM", "CHQ", "INB", "TPT", "MMT", "BIL", "EMI",
})

INDIAN_BANK_TOKENS = frozenset({
    "HDFC", "ICICI", "SBI", "AXIS", "KOTAK", "YES", "IDFC", "IDBI",
    "PNB", "BOB", "CANARA", "INDUSIND", "RBL", "FEDERAL", "KARNATAKA",
    "UCO", "UNION", "CITI", "HSBC", "DBS", "BARCLAYS", "DEUTSCHE",
    "BANDHAN", "CSB", "DCB", "TMB", "SOUTH", "INDIAN",
})

# Strip whole dates *before* splitting, so the parts (DD, MM, YYYY) don't
# survive as separate short tokens.
_DATE_SUBSTR_RE = re.compile(
    r"\b("
    r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"     # 01/05/2026, 1-5-26
    r"|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"      # 2026-05-01
    r"|\d{8}"                                # 20260501
    r")\b"
)

_PURE_DIGITS_RE = re.compile(r"^\d+$")
# Alphanumeric tokens where digits dominate (e.g. ref numbers like N123456789,
# CMS3456234, REF202604051245). Treat them as boilerplate refs.
_DIGIT_HEAVY_RE = re.compile(r"^(?=.*\d)[A-Z0-9]+$")

_SPLIT_RE = re.compile(r"[\s\-_/\\|,;:.()\[\]]+")

_BOILERPLATE_TOKENS = PAYMENT_RAIL_TOKENS | INDIAN_BANK_TOKENS


def _is_digit_heavy(tok: str) -> bool:
    """Token is alphanumeric AND digits make up at least half of it."""
    if not _DIGIT_HEAVY_RE.match(tok):
        return False
    digits = sum(1 for c in tok if c.isdigit())
    return digits * 2 >= len(tok)


def tokenize(narration: str) -> Dict[str, object]:
    if not narration:
        return {"cleaned": "", "tokens": [], "stripped": []}

    original = narration.strip()
    # Drop full date substrings first, then split.
    pre = _DATE_SUBSTR_RE.sub(" ", original.upper())
    raw_tokens = [t for t in _SPLIT_RE.split(pre) if t]

    kept: List[str] = []
    stripped: List[str] = []
    for tok in raw_tokens:
        if len(tok) <= 1:
            stripped.append(tok)
            continue
        if tok in _BOILERPLATE_TOKENS:
            stripped.append(tok)
            continue
        if _PURE_DIGITS_RE.match(tok):
            stripped.append(tok)
            continue
        if _is_digit_heavy(tok):
            stripped.append(tok)
            continue
        kept.append(tok)

    cleaned = " ".join(kept) if kept else original
    return {"cleaned": cleaned, "tokens": kept, "stripped": stripped}
