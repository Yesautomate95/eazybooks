"""
Table-driven tests for bank_narration_tokenizer.

Runnable two ways:
    python -m tests.test_bank_narration_tokenizer       # plain stdlib runner
    pytest tests/test_bank_narration_tokenizer.py       # if pytest is installed
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from applicationca.services.bank_narration_tokenizer import tokenize


CASES = [
    # (raw narration, expected cleaned token-stream)
    ("UPI ZOMATO HDFC 223020100330 54322",          "ZOMATO"),
    ("NEFT-ICICI-AMAZON-IN-N123456789",             "AMAZON IN"),
    ("POS HDFC SWIGGY BANGALORE 01/05/2026",        "SWIGGY BANGALORE"),
    ("IMPS/UBER INDIA/SYSTEMS/HDFC/202604051245",   "UBER INDIA SYSTEMS"),
    ("RTGS PAYMENT TO RELIANCE INDUSTRIES LTD",     "PAYMENT TO RELIANCE INDUSTRIES LTD"),
    ("NETFLIX SUBSCRIPTION",                        "NETFLIX SUBSCRIPTION"),
]


def test_cleaned_outputs():
    failures = []
    for raw, expected in CASES:
        got = tokenize(raw)["cleaned"]
        if got != expected:
            failures.append(f"  {raw!r}\n    expected: {expected!r}\n    got:      {got!r}")
    assert not failures, "Tokenizer mismatches:\n" + "\n".join(failures)


def test_empty_input_returns_empty():
    out = tokenize("")
    assert out == {"cleaned": "", "tokens": [], "stripped": []}, out


def test_pure_junk_falls_back_to_original():
    # Everything strips out — caller still needs *some* string to match against.
    raw = "UPI HDFC 223020100330"
    out = tokenize(raw)
    assert out["cleaned"] == raw, out
    assert out["tokens"] == [], out


def test_long_digit_block_dropped():
    out = tokenize("ZOMATO 223020100330")
    assert out["cleaned"] == "ZOMATO", out
    assert "223020100330" in out["stripped"]


def test_pure_digit_tokens_dropped():
    # Pure-numeric tokens are never business names — strip them all,
    # regardless of length. Real merchant tags ("M1", "A1A") survive
    # because they contain a letter.
    out = tokenize("STORE 1234 PURCHASE")
    assert out["cleaned"] == "STORE PURCHASE", out
    assert "1234" in out["stripped"]


def test_date_substring_dropped():
    # Whole-date substrings are stripped before tokenization, so the
    # day/month/year fragments don't survive as short tokens.
    out = tokenize("SWIGGY 15/04/2026")
    assert out["cleaned"] == "SWIGGY", out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}\n{exc}")
    if failed:
        sys.exit(1)
    print(f"\n{len(fns)} passed")
