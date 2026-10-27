"""
Tests for run_group_matching — the QB recon "smart" matcher (reference,
date-window, fuzzy description, one-to-many aggregation, contra detection, tiers).

Runnable two ways:
    python -m tests.test_group_matching      # plain stdlib runner
    pytest tests/test_group_matching.py      # if pytest is installed
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Load bank_reconciliation.py directly by path. Importing it through the package
# (applicationca.agents.document_specific_functions) triggers that package's
# __init__, which eagerly imports openai and other heavy runtime deps we don't
# need — and may not be installed — just to unit-test the pure matcher.
_MOD_PATH = (
    Path(__file__).resolve().parent.parent
    / "applicationca" / "agents" / "document_specific_functions" / "bank_reconciliation.py"
)
_spec = importlib.util.spec_from_file_location("_bank_recon_under_test", _MOD_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
run_group_matching = _mod.run_group_matching


def _book(debit=None, credit=None, desc="", ref="", d=(2026, 3, 1)):
    return {
        "date": date(*d),
        "description": desc,
        "reference": ref,
        "debit_amount": Decimal(str(debit)) if debit else None,
        "credit_amount": Decimal(str(credit)) if credit else None,
    }


# bank rows have the identical shape
_bank = _book


# ── Pass A: reference match wins, ignores the date window ──────────────────

def test_reference_match_is_auto_and_ignores_date():
    # book debit (money in) <-> bank credit, same reference, 10 days apart
    book = [_book(debit=500, desc="anything", ref="UTR123", d=(2026, 3, 1))]
    bank = [_bank(credit=500, desc="totally different", ref="UTR123", d=(2026, 3, 11))]
    groups = run_group_matching(book, bank)
    assert len(groups) == 1, groups
    g = groups[0]
    assert g["book_idxs"] == [0] and g["bank_idxs"] == [0], g
    assert g["confidence"] == 1.0 and g["tier"] == "auto", g
    assert "Reference" in g["reason"], g


# ── Pass B: scored 1:1 ─────────────────────────────────────────────────────

def test_exact_date_and_description_is_auto():
    # book credit (money out) <-> bank debit, same date, identical description
    book = [_book(credit=100, desc="corporation tax payment", d=(2026, 3, 5))]
    bank = [_bank(debit=100, desc="corporation tax payment", d=(2026, 3, 5))]
    groups = run_group_matching(book, bank)
    assert len(groups) == 1, groups
    assert groups[0]["tier"] == "auto", groups[0]
    assert groups[0]["confidence"] >= 0.95, groups[0]


def test_same_date_exact_amount_weak_desc_is_auto():
    # The retune intent: a same-date, exact cross-amount pair auto-reconciles even
    # when the QBO memo doesn't resemble the bank narration (the common case).
    book = [_book(credit=2500, desc="bill payment 7731 acct a/p", d=(2026, 3, 5))]
    bank = [_bank(debit=2500, desc="ach debit vendorco ref 99", d=(2026, 3, 5))]
    groups = run_group_matching(book, bank)
    assert len(groups) == 1, groups
    assert groups[0]["tier"] == "auto", groups[0]
    assert groups[0]["confidence"] >= 0.90, groups[0]


def test_date_offset_weak_desc_is_suggest():
    book = [_book(debit=200, desc="transfer from account 4336 to 3482", d=(2026, 3, 5))]
    bank = [_bank(credit=200, desc="1bank internet debit", d=(2026, 3, 7))]
    groups = run_group_matching(book, bank)
    assert len(groups) == 1, groups
    assert groups[0]["tier"] == "suggest", groups[0]
    assert 0.60 <= groups[0]["confidence"] < 0.95, groups[0]


def test_no_match_when_amounts_differ():
    book = [_book(debit=100, desc="x", d=(2026, 3, 5))]
    bank = [_bank(credit=200, desc="x", d=(2026, 3, 5))]
    assert run_group_matching(book, bank) == []


def test_date_outside_window_not_matched_without_reference():
    book = [_book(debit=300, desc="acme ltd", d=(2026, 3, 1))]
    bank = [_bank(credit=300, desc="acme ltd", d=(2026, 3, 20))]  # 19 days, no ref
    assert run_group_matching(book, bank) == []


# ── Pass C: one-to-many aggregation ────────────────────────────────────────

def test_one_bank_to_many_book_aggregation():
    # one bank credit 1366.62 == three book debits summing to it
    book = [
        _book(debit=426.62, desc="general office expenses", d=(2026, 3, 31)),
        _book(debit=350.00, desc="annual levy", d=(2026, 3, 31)),
        _book(debit=590.00, desc="tickets trip", d=(2026, 3, 31)),
    ]
    bank = [_bank(credit=1366.62, desc="1bank internet debit jaytour", d=(2026, 3, 30))]
    groups = run_group_matching(book, bank)
    assert len(groups) == 1, groups
    g = groups[0]
    assert sorted(g["book_idxs"]) == [0, 1, 2], g
    assert g["bank_idxs"] == [0], g
    assert g["tier"] == "suggest" and "Aggregate" in g["reason"], g


def test_one_book_to_many_bank_aggregation():
    book = [_book(credit=1000.00, desc="supplier payment", d=(2026, 3, 10))]
    bank = [
        _bank(debit=600.00, desc="part 1", d=(2026, 3, 10)),
        _bank(debit=400.00, desc="part 2", d=(2026, 3, 11)),
    ]
    groups = run_group_matching(book, bank)
    assert len(groups) == 1, groups
    g = groups[0]
    assert g["book_idxs"] == [0] and sorted(g["bank_idxs"]) == [0, 1], g
    assert "Aggregate" in g["reason"], g


# ── Pass D: contra / offset pairs ──────────────────────────────────────────

def test_contra_pair_on_book_side():
    # equal-and-opposite book entries to the same party net to zero, no bank line
    book = [
        _book(credit=2600.69, desc="cerc international ltd", d=(2026, 7, 10)),
        _book(debit=2600.69, desc="cerc international ltd", d=(2026, 7, 10)),
    ]
    bank = [_bank(credit=9999.00, desc="unrelated deposit", d=(2026, 7, 10))]  # noise
    groups = run_group_matching(book, bank)
    contra = [g for g in groups if "Contra" in g["reason"]]
    assert len(contra) == 1, groups
    g = contra[0]
    assert sorted(g["book_idxs"]) == [0, 1] and g["bank_idxs"] == [], g


def test_empty_inputs():
    assert run_group_matching([], []) == []
    assert run_group_matching([_book(debit=1)], []) == []


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
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        sys.exit(1)
    print(f"\n{len(fns)} passed")
