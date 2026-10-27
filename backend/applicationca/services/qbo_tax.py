"""
QBO tax posting (Phase 1).

QB invoices/bills were being posted NET of tax (no TxnTaxDetail), so the QBO
document total understated the source document by the tax amount. This module
attaches the extracted tax to the payload.

Getting tax to post correctly is realm-dependent (US Automated Sales Tax vs
EU/UK VAT, tax-code setup), so this is GATED behind the QBO_POST_TAX env flag
and defaults OFF — enabling it without a real realm to test against could make
QBO reject the push. When OFF, behavior is unchanged (tax stays display-only,
as surfaced in the UI). Turn it on once verified against the target realm.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def tax_posting_enabled() -> bool:
    return os.getenv("QBO_POST_TAX", "false").strip().lower() in ("1", "true", "yes", "on")


def _pick_taxable_code(tax_codes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Prefer an explicitly taxable code; else the first non-exempt code."""
    for tc in tax_codes or []:
        if tc.get("Taxable") and tc.get("Id"):
            return tc
    for tc in tax_codes or []:
        name = (tc.get("Name") or "").upper()
        if tc.get("Id") and name not in ("NON", "NONTAXABLE", "EXEMPT", "OUT OF SCOPE"):
            return tc
    return None


def apply_tax(
    payload: Dict[str, Any],
    *,
    tax_amount: Any,
    tax_codes: List[Dict[str, Any]],
    line_detail_key: str,
) -> Dict[str, Any]:
    """Attach tax to a QBO invoice/bill payload IN PLACE. No-op unless
    QBO_POST_TAX is enabled and there's a positive tax amount.

    line_detail_key: 'SalesItemLineDetail' (invoice) or
                     'AccountBasedExpenseLineDetail' (bill).
    """
    if not tax_posting_enabled():
        return payload
    try:
        amt = round(float(tax_amount or 0), 2)
    except (TypeError, ValueError):
        amt = 0.0
    if amt <= 0:
        return payload

    # Transaction-level total tax — accepted by non-AST realms as an override;
    # AST realms recompute from line tax codes, which we also set below.
    payload["TxnTaxDetail"] = {"TotalTax": amt}

    code = _pick_taxable_code(tax_codes)
    if code:
        payload["TxnTaxDetail"]["TxnTaxCodeRef"] = {"value": code["Id"]}
        payload["GlobalTaxCalculation"] = "TaxExcluded"
        ref = {"value": code["Id"], "name": code.get("Name")}
        for line in payload.get("Line", []) or []:
            detail = line.get(line_detail_key)
            if isinstance(detail, dict):
                detail["TaxCodeRef"] = ref
    return payload
