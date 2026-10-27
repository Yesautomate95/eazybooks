"""
Tests for the tier-based history scorer.

The scorer used to be a 7-factor weighted blend (recency, frequency,
voucher_match, amount_match, gstin_match, hsn_overlap, consistency,
confirmation). It now collapses to three signals applied in priority order:

  1. GSTIN exact match
  2. Frequency dominance (≥70% of this vendor's history → THIS target)
  3. Recency (tiebreak within tier + stale penalty)

These tests pin the tier boundaries and the stale-penalty behavior.

Runnable two ways:
    python -m tests.test_history_scorer_tiers
    pytest tests/test_history_scorer_tiers.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from applicationca.services.tally_history_service import (
    HistoryCandidate,
    HistoryContext,
    score_history_candidate,
)


def _make(*, usage: int, gstin: str, last_days: int) -> HistoryCandidate:
    return HistoryCandidate(
        target_value="L",
        usage_count=usage,
        metadata={},
        voucher_type="purchase_bill",
        amount_band="5k_50k",
        gstin=gstin,
        hsn_codes=[],
        confirmation_source="auto_pushed",
        last_used_at=datetime.now(timezone.utc) - timedelta(days=last_days),
    )


CTX = HistoryContext(voucher_type="purchase_bill", amount=10000, gstin="27A")


def test_tier_A_gstin_match_plus_dominant():
    """GSTIN match + 80% of mappings → near-certain (0.97)."""
    c = _make(usage=8, gstin="27A", last_days=30)
    score_history_candidate(c, CTX, source_total_uses=10)
    assert c.breakdown["tier"] == "A"
    assert c.confidence == 0.97


def test_tier_B_gstin_match_alone():
    """GSTIN match but ambiguous frequency → 0.92."""
    c = _make(usage=3, gstin="27A", last_days=30)
    score_history_candidate(c, CTX, source_total_uses=10)
    assert c.breakdown["tier"] == "B"
    assert c.confidence == 0.92


def test_tier_C_dominant_alone():
    """Vendor name matches, target consistent, but GSTIN doesn't line up → 0.88."""
    c = _make(usage=8, gstin="99X", last_days=30)
    score_history_candidate(c, CTX, source_total_uses=10)
    assert c.breakdown["tier"] == "C"
    assert c.confidence == 0.88


def test_tier_D_ambiguous():
    """Highest of several similarly-frequent mappings → 0.82."""
    c = _make(usage=5, gstin="99X", last_days=30)
    score_history_candidate(c, CTX, source_total_uses=10)
    assert c.breakdown["tier"] == "D"
    assert c.confidence == 0.82


def test_tier_E_long_tail():
    """A target this vendor has barely used → 0.70."""
    c = _make(usage=1, gstin="99X", last_days=30)
    score_history_candidate(c, CTX, source_total_uses=10)
    assert c.breakdown["tier"] == "E"
    assert c.confidence == 0.70


def test_stale_penalty_drops_confidence():
    """A top-tier mapping that hasn't been used in >180 days loses 0.05."""
    fresh = _make(usage=8, gstin="27A", last_days=30)
    score_history_candidate(fresh, CTX, source_total_uses=10)
    stale = _make(usage=8, gstin="27A", last_days=200)
    score_history_candidate(stale, CTX, source_total_uses=10)
    assert fresh.confidence == 0.97
    assert stale.confidence == 0.92  # 0.97 − 0.05 stale penalty
    assert fresh.composite_score > stale.composite_score  # fresh still sorts first


def test_recency_breaks_ties_within_tier():
    """Two same-tier candidates should sort with the more-recent one first."""
    fresh = _make(usage=8, gstin="27A", last_days=10)
    older = _make(usage=8, gstin="27A", last_days=100)
    score_history_candidate(fresh, CTX, source_total_uses=10)
    score_history_candidate(older, CTX, source_total_uses=10)
    assert fresh.breakdown["tier"] == older.breakdown["tier"] == "A"
    assert fresh.confidence == older.confidence == 0.97
    assert fresh.composite_score > older.composite_score


def test_gstin_required_for_tier_A_or_B():
    """No vendor GSTIN in context → A and B unreachable, even with a stored gstin."""
    no_gstin_ctx = HistoryContext(voucher_type="purchase_bill", amount=10000, gstin=None)
    c = _make(usage=8, gstin="27A", last_days=30)
    score_history_candidate(c, no_gstin_ctx, source_total_uses=10)
    # Falls through to C (dominant alone, no gstin contribution)
    assert c.breakdown["tier"] == "C"
    assert c.breakdown["gstin_match"] is False


def _run_all():
    failures = []
    tests = [
        test_tier_A_gstin_match_plus_dominant,
        test_tier_B_gstin_match_alone,
        test_tier_C_dominant_alone,
        test_tier_D_ambiguous,
        test_tier_E_long_tail,
        test_stale_penalty_drops_confidence,
        test_recency_breaks_ties_within_tier,
        test_gstin_required_for_tier_A_or_B,
    ]
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  {t.__name__}: {exc!r}")
            failures.append(t.__name__)
    if failures:
        print(f"\n{len(failures)} test(s) failed: {failures}")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    _run_all()
