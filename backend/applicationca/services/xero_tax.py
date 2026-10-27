"""
Xero tax posting (Phase 1).

The Xero analog of qbo_tax. Xero invoices/bills were being posted NET of tax (no
tax type on the lines, LineAmountTypes defaulting such that the Xero document
total understated the source document by the tax amount). This module attaches
the extracted tax to the payload by picking a tax rate and setting the line
``TaxType`` + the invoice ``LineAmountTypes``.

Getting tax to post correctly is org-dependent (VAT/GST tax-rate setup,
inclusive vs exclusive amounts), so this is GATED behind the XERO_POST_TAX env
flag and defaults OFF — enabling it without a real tenant to test against could
make Xero reject the push. When OFF, behavior is unchanged (tax stays
display-only, as surfaced in the UI). Turn it on once verified against the
target tenant.

Xero specifics vs QBO:
  * Tax is referenced per-line by ``TaxType`` (a TaxRate's TaxType string), NOT
    a transaction-level TxnTaxDetail.
  * ``LineAmountTypes`` controls whether the line UnitAmounts are tax-exclusive
    or -inclusive; we post Exclusive (lines are net, Xero adds the tax) to match
    how qbo_tax sets GlobalTaxCalculation=TaxExcluded.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def tax_posting_enabled() -> bool:
    return os.getenv("XERO_POST_TAX", "false").strip().lower() in ("1", "true", "yes", "on")


def _pick_taxable_rate(tax_rates: List[Dict[str, Any]], *, side: str) -> Optional[Dict[str, Any]]:
    """Prefer an active rate applicable to the document side (revenue for
    invoices, expense for bills) with a positive effective rate; else the first
    active non-zero, non-exempt rate.

    ``side`` is 'revenue' or 'expense'."""
    apply_flag = "CanApplyToRevenue" if side == "revenue" else "CanApplyToExpenses"

    def _is_active(tr: Dict[str, Any]) -> bool:
        status = (tr.get("Status") or "ACTIVE").upper()
        return status == "ACTIVE"

    def _effective(tr: Dict[str, Any]) -> float:
        try:
            return float(tr.get("EffectiveRate") or tr.get("DisplayTaxRate") or 0)
        except (TypeError, ValueError):
            return 0.0

    for tr in tax_rates or []:
        if tr.get("TaxType") and _is_active(tr) and tr.get(apply_flag) and _effective(tr) > 0:
            return tr
    for tr in tax_rates or []:
        name = (tr.get("Name") or "").upper()
        if (
            tr.get("TaxType") and _is_active(tr) and _effective(tr) > 0
            and name not in ("NONE", "NO TAX", "TAX EXEMPT", "EXEMPT", "ZERO RATED")
        ):
            return tr
    return None


def apply_tax(
    payload: Dict[str, Any],
    *,
    tax_amount: Any,
    tax_rates: List[Dict[str, Any]],
    side: str = "revenue",
) -> Dict[str, Any]:
    """Attach tax to a Xero invoice/bill payload IN PLACE. No-op unless
    XERO_POST_TAX is enabled and there's a positive tax amount.

    ``side``: 'revenue' (invoice / ACCREC) or 'expense' (bill / ACCPAY).
    """
    if not tax_posting_enabled():
        return payload
    try:
        amt = round(float(tax_amount or 0), 2)
    except (TypeError, ValueError):
        amt = 0.0
    if amt <= 0:
        return payload

    rate = _pick_taxable_rate(tax_rates, side=side)
    if not rate:
        return payload

    # Lines are tax-exclusive (net); Xero adds the tax from each line's TaxType.
    payload["LineAmountTypes"] = "Exclusive"
    tax_type = rate["TaxType"]
    for line in payload.get("LineItems", []) or []:
        if isinstance(line, dict):
            line["TaxType"] = tax_type
    return payload
