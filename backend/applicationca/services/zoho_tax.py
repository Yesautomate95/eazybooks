"""
Zoho Books tax posting (Phase 1) — Zoho analog of qbo_tax.

Zoho invoices/bills were being posted NET of tax (no tax lines), so the Zoho
document total understated the source document by the tax amount. This module
attaches the extracted tax to the payload.

Getting tax to post correctly is org-dependent (GST vs VAT vs no tax, tax-code
setup), so this is GATED behind the ZOHO_POST_TAX env flag and defaults OFF —
enabling it without a real org to test against could make Zoho reject the push.
When OFF, behavior is unchanged (tax stays display-only, as surfaced in the UI).
Turn it on once verified against the target organization.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def tax_posting_enabled() -> bool:
    return os.getenv("ZOHO_POST_TAX", "false").strip().lower() in ("1", "true", "yes", "on")


def _pick_taxable_code(tax_codes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Prefer a code with a positive percentage; else the first non-exempt code.

    Zoho tax objects look like {tax_id, tax_name, tax_percentage, tax_type, ...}.
    """
    for tc in tax_codes or []:
        try:
            pct = float(tc.get("tax_percentage") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        if pct > 0 and tc.get("tax_id"):
            return tc
    for tc in tax_codes or []:
        name = (tc.get("tax_name") or "").upper()
        if tc.get("tax_id") and name not in ("NON", "NONTAXABLE", "EXEMPT", "OUT OF SCOPE", "NO TAX"):
            return tc
    return None


def apply_tax(
    payload: Dict[str, Any],
    *,
    tax_amount: Any,
    tax_codes: List[Dict[str, Any]],
    line_detail_key: str,
) -> Dict[str, Any]:
    """Attach tax to a Zoho invoice/bill payload IN PLACE. No-op unless
    ZOHO_POST_TAX is enabled and there's a positive tax amount.

    Zoho applies tax per line via a `tax_id` on each line item; the document tax
    total is then derived by Zoho from the line tax codes. We tag every line with
    the chosen taxable code so the posted total includes tax. `line_detail_key`
    is accepted for signature parity with qbo_tax (Zoho has a single flat line
    shape, so it is unused).
    """
    if not tax_posting_enabled():
        return payload
    try:
        amt = round(float(tax_amount or 0), 2)
    except (TypeError, ValueError):
        amt = 0.0
    if amt <= 0:
        return payload

    code = _pick_taxable_code(tax_codes)
    if code:
        tax_id = code["tax_id"]
        # is_inclusive_tax=False => tax is added on top of the line rate
        # (parity with QBO's TaxExcluded), matching "extracted total = net + tax".
        payload["is_inclusive_tax"] = False
        for line in payload.get("line_items", []) or []:
            if isinstance(line, dict):
                line["tax_id"] = tax_id
                if code.get("tax_name"):
                    line["tax_name"] = code.get("tax_name")  # display-only; stripped before POST
    else:
        # No usable code — record the total so the UI can still surface it.
        payload["_tax_total"] = amt
    return payload
