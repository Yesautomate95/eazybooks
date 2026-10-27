"""
Tests for the offline ledger-cache fallback in the suggestion service.

The cache layer is the critical reliability primitive — when the desktop
bridge to Tally is down (firewall, app crash, laptop closed), the user
should still see suggestions backed by the last-known snapshot. These
tests pin the contract:

  1. _shape_grouped_for_voucher transforms the same shape from cache and
     from live identically (the property the offline path leans on).
  2. _fetch_ledgers_for_voucher_type returns from_cache=True with the
     correct payload when the bridge is offline AND we have a snapshot.
  3. _fetch_ledgers_for_voucher_type writes through to the cache on
     every successful live fetch (so the next outage isn't catastrophic).

Runnable two ways:
    python -m tests.test_ledger_cache_fallback
    pytest tests/test_ledger_cache_fallback.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from applicationca.services import ledger_suggestion_service as suggestion_svc


SAMPLE_GROUPED = {
    "party": {
        "Sundry Creditors": [
            {"name": "ACME Industries", "gstin": "27AAACA1234M1Z5"},
            {"name": "Beta Traders", "gstin": ""},
        ],
        "Sundry Debtors": [
            {"name": "Customer Co", "gstin": ""},
        ],
    },
    "purchase": {
        "Purchase Accounts": [{"name": "Purchase 18%"}],
        "Direct Expenses": [{"name": "Freight Inward"}],
        "Indirect Expenses": [
            {"name": "Office Supplies"},
            {"name": "Discount Allowed"},
        ],
    },
    "sales": {
        "Sales Accounts": [{"name": "Sales 18%"}],
    },
}


def test_shape_grouped_for_voucher_purchase_bill():
    """The transform should pick the right slices for purchase_bill."""
    mapping = suggestion_svc.VOUCHER_GROUP_MAP["purchase_bill"]
    shaped = suggestion_svc._shape_grouped_for_voucher(
        SAMPLE_GROUPED, mapping["party_groups"], mapping["ledger_groups"],
    )
    party_names = [l["name"] for l in shaped["party_ledgers"]]
    assert "ACME Industries" in party_names
    assert "Beta Traders" in party_names
    assert "Customer Co" in party_names
    assert "purchase_accounts" in shaped["ledger_groups"]
    assert "direct_expenses" in shaped["ledger_groups"]
    assert "indirect_expenses" in shaped["ledger_groups"]
    discount_names = [l["name"] for l in shaped["discount_candidates"]]
    assert "Discount Allowed" in discount_names


def test_shape_grouped_for_voucher_handles_empty():
    """Empty grouped input must not crash — used to short-circuit the
    no-cache path."""
    shaped = suggestion_svc._shape_grouped_for_voucher({}, ["Sundry Creditors"], ["Purchase Accounts"])
    assert shaped["party_ledgers"] == []
    assert shaped["ledger_groups"] == {}
    assert shaped["discount_candidates"] == []


def test_fetch_falls_back_to_cache_when_bridge_offline():
    """When the Electron bridge isn't connected, the suggestion service
    must serve from cache instead of returning empty — that's the whole
    point of the cache."""
    refreshed_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    # Empty electron_connections — simulates the bridge being offline.
    fake_ws_manager = MagicMock()
    fake_ws_manager.electron_connections = {}

    with patch.object(suggestion_svc, "ws_manager", fake_ws_manager), \
         patch.object(
             suggestion_svc.tally_cache_store,
             "get_cached_grouped_ledgers",
             return_value={
                 "grouped_ledgers": SAMPLE_GROUPED,
                 "grouped_refreshed_at": refreshed_at,
             },
         ) as mock_get:
        result = asyncio.run(
            suggestion_svc._fetch_ledgers_for_voucher_type(
                "ACME Co", "purchase_bill", firebase_uid="uid-123",
            )
        )

    mock_get.assert_called_once_with(user_id="uid-123", company_name="ACME Co")
    assert result["from_cache"] is True
    assert result["cache_refreshed_at"] == refreshed_at.isoformat()
    party_names = [l["name"] for l in result["party_ledgers"]]
    assert "ACME Industries" in party_names
    # Discount candidates also flow through the cache path.
    discount_names = [l["name"] for l in result["discount_candidates"]]
    assert "Discount Allowed" in discount_names


def test_fetch_returns_empty_when_no_cache_and_offline():
    """No bridge AND no cache = legitimately empty. Don't crash."""
    fake_ws_manager = MagicMock()
    fake_ws_manager.electron_connections = {}
    with patch.object(suggestion_svc, "ws_manager", fake_ws_manager), \
         patch.object(
             suggestion_svc.tally_cache_store,
             "get_cached_grouped_ledgers",
             return_value=None,
         ):
        result = asyncio.run(
            suggestion_svc._fetch_ledgers_for_voucher_type(
                "Unknown Co", "purchase_bill", firebase_uid="uid-123",
            )
        )
    assert result["from_cache"] is False
    assert result["party_ledgers"] == []
    assert result["ledger_groups"] == {}


def test_fetch_writes_through_to_cache_on_successful_live_fetch():
    """Every successful live fetch must warm the cache for the next outage.
    Without this, a user disconnect right after first sync would still
    leave them stranded."""
    fake_ws_manager = MagicMock()
    fake_ws_manager.electron_connections = {"uid-123": object()}

    async def fake_get_grouped(uid, company, timeout=15):
        return SAMPLE_GROUPED

    fake_ws_manager.get_tally_grouped_ledgers = fake_get_grouped

    with patch.object(suggestion_svc, "ws_manager", fake_ws_manager), \
         patch.object(
             suggestion_svc.tally_cache_store,
             "upsert_grouped_ledgers",
         ) as mock_upsert:
        result = asyncio.run(
            suggestion_svc._fetch_ledgers_for_voucher_type(
                "ACME Co", "purchase_bill", firebase_uid="uid-123",
            )
        )

    mock_upsert.assert_called_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["user_id"] == "uid-123"
    assert kwargs["company_name"] == "ACME Co"
    assert kwargs["grouped"] == SAMPLE_GROUPED
    assert result["from_cache"] is False
    assert result["cache_refreshed_at"] is None
    assert any(l["name"] == "ACME Industries" for l in result["party_ledgers"])


def test_fetch_falls_back_to_cache_on_ws_exception():
    """If the bridge is registered but the call fails (timeout, Tally
    crash), we still fall back to cache — same behavior as offline."""
    refreshed_at = datetime(2026, 5, 30, 9, 0, tzinfo=timezone.utc)
    fake_ws_manager = MagicMock()
    fake_ws_manager.electron_connections = {"uid-123": object()}

    async def fake_get_grouped_raises(uid, company, timeout=15):
        raise RuntimeError("timeout")

    fake_ws_manager.get_tally_grouped_ledgers = fake_get_grouped_raises

    with patch.object(suggestion_svc, "ws_manager", fake_ws_manager), \
         patch.object(
             suggestion_svc.tally_cache_store,
             "get_cached_grouped_ledgers",
             return_value={
                 "grouped_ledgers": SAMPLE_GROUPED,
                 "grouped_refreshed_at": refreshed_at,
             },
         ):
        result = asyncio.run(
            suggestion_svc._fetch_ledgers_for_voucher_type(
                "ACME Co", "purchase_bill", firebase_uid="uid-123",
            )
        )

    assert result["from_cache"] is True
    assert result["cache_refreshed_at"] == refreshed_at.isoformat()
    assert any(l["name"] == "ACME Industries" for l in result["party_ledgers"])


def _run_all():
    """Tiny stdlib runner so this file works without pytest installed."""
    failures = []
    tests = [
        test_shape_grouped_for_voucher_purchase_bill,
        test_shape_grouped_for_voucher_handles_empty,
        test_fetch_falls_back_to_cache_when_bridge_offline,
        test_fetch_returns_empty_when_no_cache_and_offline,
        test_fetch_writes_through_to_cache_on_successful_live_fetch,
        test_fetch_falls_back_to_cache_on_ws_exception,
    ]
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as exc:  # noqa: BLE001 — surface every failure
            print(f"  FAIL  {t.__name__}: {exc!r}")
            failures.append(t.__name__)
    if failures:
        print(f"\n{len(failures)} test(s) failed: {failures}")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    _run_all()
