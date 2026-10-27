"""
Pins the suggestion pipeline's priority order:

  Priority 1 — User-defined mapping rules  (matcher._layer1_mapping_rules)
  Priority 2 — Historical patterns         (lookup_history_ranked)
  Priority 3 — Matcher pipeline            (GSTIN exact → fuzzy → LLM)

A rule hit MUST short-circuit history. A history hit MUST short-circuit
the LLM-driven category match. These tests construct mocks that fail
loudly if a later layer runs when an earlier one already filled the slot.

Runnable two ways:
    python -m tests.test_suggestion_priority_order
    pytest tests/test_suggestion_priority_order.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from applicationca.services import ledger_suggestion_service as svc
from applicationca.services.ledger_matcher import MatchResult


SAMPLE_FETCHED = {
    "party_ledgers": [
        {"name": "ACME Industries", "gstin": "27AAACA1234M1Z5"},
        {"name": "Beta Traders", "gstin": ""},
    ],
    "ledger_groups": {
        "purchase_accounts": [{"name": "Purchase 18%"}],
        "indirect_expenses": [{"name": "Office Supplies"}, {"name": "Discount Allowed"}],
    },
    "discount_candidates": [{"name": "Discount Allowed"}],
    "from_cache": False,
    "cache_refreshed_at": None,
}


def _patch_fetch():
    """Stub the upstream ledger fetcher so we don't need a live Tally bridge."""
    async def fake_fetch(company, voucher_type, firebase_uid=None):
        return SAMPLE_FETCHED
    return patch.object(svc, "_fetch_ledgers_for_voucher_type", side_effect=fake_fetch)


def _async_run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────
# Priority 1 — rule short-circuits history and LLM
# ──────────────────────────────────────────────────────────────────────────

def test_rule_hit_for_party_skips_history_lookup():
    """A party-side rule hit MUST stop us from consulting history for the
    same slot. Verified by mocking lookup_history_ranked and asserting it
    isn't called for the party slot."""
    rule_hit = MatchResult(
        recommended_ledger="ACME Industries",
        confidence=0.99,
        match_type="mapping_rule",
        reasoning="Vendor=ACME → ACME Industries",
        create_new=False,
    )

    party_history_calls = []

    def fake_lookup(uid, company, mapping_type, source_key, context=None, top_n=3):
        party_history_calls.append(mapping_type)
        return []  # no history

    with _patch_fetch(), \
         patch.object(svc, "lookup_history_ranked", side_effect=fake_lookup), \
         patch.object(svc.LedgerMatcher, "_layer1_mapping_rules") as mock_l1, \
         patch.object(svc.LedgerMatcher, "match_party") as mock_party, \
         patch.object(svc.LedgerMatcher, "match_category") as mock_cat:

        # Rule fires for party slot (first call), misses for expense (second)
        mock_l1.side_effect = [rule_hit, None]

        # match_party and match_category should not be reached for the party
        # slot, but match_category might run for the expense slot. Wire async
        # mocks that just raise if hit unexpectedly.
        async def party_should_not_run(**_):
            raise AssertionError("match_party was called even though a rule hit")
        async def cat_returns(*_args, **_kwargs):
            res = MagicMock()
            res.recommended_ledger = "Purchase 18%"
            res.confidence = 0.7
            res.category = "Purchase Accounts"
            res.reasoning = "LLM picked"
            res.gst_rate_detected = "18%"
            res.alternatives = []
            return res
        mock_party.side_effect = party_should_not_run
        mock_cat.side_effect = cat_returns

        result = _async_run(svc.suggest_ledgers(
            bill_data={"vendor_name": "ACME", "vendor_gstin": "27AAACA1234M1Z5"},
            company="ACME Co",
            voucher_type="purchase_bill",
            firebase_uid="uid-1",
            user_id="uid-1",
        ))

    assert result["party_suggestion"]["recommended_ledger"] == "ACME Industries"
    assert result["party_suggestion"]["match_type"] == "mapping_rule"
    # History must not have been consulted for the party slot
    assert "party_ledger" not in party_history_calls, (
        f"history layer ran for party even though rule hit: {party_history_calls}"
    )


def test_rule_hit_for_both_slots_skips_history_and_llm_entirely():
    """When rules fill BOTH party AND expense slots, we should never
    consult history OR the LLM. Saves latency on the common case where a
    user has fully ruled a vendor."""
    party_rule = MatchResult(
        recommended_ledger="ACME Industries", confidence=0.99,
        match_type="mapping_rule", reasoning="rule",
    )
    expense_rule = MatchResult(
        recommended_ledger="Office Supplies", confidence=0.99,
        match_type="mapping_rule", reasoning="rule",
    )

    history_calls = []

    def fake_lookup(*args, **kwargs):
        history_calls.append(args)
        return []

    with _patch_fetch(), \
         patch.object(svc, "lookup_history_ranked", side_effect=fake_lookup), \
         patch.object(svc.LedgerMatcher, "_layer1_mapping_rules") as mock_l1, \
         patch.object(svc.LedgerMatcher, "match_party") as mock_party, \
         patch.object(svc.LedgerMatcher, "match_category") as mock_cat:

        mock_l1.side_effect = [party_rule, expense_rule]

        async def party_fail(**_):
            raise AssertionError("match_party should not run")
        async def cat_fail(*_args, **_kwargs):
            raise AssertionError("match_category should not run")
        mock_party.side_effect = party_fail
        mock_cat.side_effect = cat_fail

        result = _async_run(svc.suggest_ledgers(
            bill_data={"vendor_name": "ACME", "vendor_gstin": "27A"},
            company="ACME Co",
            voucher_type="purchase_bill",
            firebase_uid="uid-1",
            user_id="uid-1",
        ))

    assert result["party_suggestion"]["recommended_ledger"] == "ACME Industries"
    assert result["expense_revenue_suggestion"]["recommended_ledger"] == "Office Supplies"
    assert history_calls == [], f"history was consulted: {history_calls}"


# ──────────────────────────────────────────────────────────────────────────
# Priority 2 — history runs when rules miss, short-circuits LLM
# ──────────────────────────────────────────────────────────────────────────

def test_history_runs_when_rule_misses():
    """No rule hit → history layer is consulted for both slots."""
    from applicationca.services.tally_history_service import HistoryCandidate

    party_hist = HistoryCandidate(
        target_value="ACME Industries",
        usage_count=8,
        metadata={},
        voucher_type="purchase_bill",
        amount_band="5k_50k",
        gstin="27AAACA1234M1Z5",
        hsn_codes=[],
        confirmation_source="auto_pushed",
        last_used_at=None,
        confidence=0.97,
        composite_score=5.0,
        breakdown={"tier": "A", "gstin_match": True, "frequency_share": 0.8, "days_since_used": 30},
    )
    expense_hist = HistoryCandidate(
        target_value="Office Supplies",
        usage_count=7,
        metadata={},
        voucher_type="purchase_bill",
        amount_band="5k_50k",
        gstin="27AAACA1234M1Z5",
        hsn_codes=[],
        confirmation_source="auto_pushed",
        last_used_at=None,
        confidence=0.92,
        composite_score=4.0,
        breakdown={"tier": "B", "gstin_match": True, "frequency_share": 0.4, "days_since_used": 30},
    )

    def fake_lookup(uid, company, mapping_type, source_key, context=None, top_n=3):
        if mapping_type == "party_ledger":
            return [party_hist]
        if mapping_type == "expense_ledger":
            return [expense_hist]
        return []

    with _patch_fetch(), \
         patch.object(svc, "lookup_history_ranked", side_effect=fake_lookup), \
         patch.object(svc.LedgerMatcher, "_layer1_mapping_rules", return_value=None), \
         patch.object(svc.LedgerMatcher, "match_party") as mock_party, \
         patch.object(svc.LedgerMatcher, "match_category") as mock_cat:

        async def party_fail(**_):
            raise AssertionError("match_party ran when history hit")
        async def cat_fail(*_args, **_kwargs):
            raise AssertionError("match_category ran when history hit")
        mock_party.side_effect = party_fail
        mock_cat.side_effect = cat_fail

        result = _async_run(svc.suggest_ledgers(
            bill_data={"vendor_name": "ACME", "vendor_gstin": "27AAACA1234M1Z5"},
            company="ACME Co",
            voucher_type="purchase_bill",
            firebase_uid="uid-1",
            user_id="uid-1",
        ))

    assert result["party_suggestion"]["recommended_ledger"] == "ACME Industries"
    assert result["party_suggestion"]["match_type"] == "history"
    assert result["expense_revenue_suggestion"]["recommended_ledger"] == "Office Supplies"
    assert result["expense_revenue_suggestion"]["match_type"] == "history"


def _run_all():
    failures = []
    tests = [
        test_rule_hit_for_party_skips_history_lookup,
        test_rule_hit_for_both_slots_skips_history_and_llm_entirely,
        test_history_runs_when_rule_misses,
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
