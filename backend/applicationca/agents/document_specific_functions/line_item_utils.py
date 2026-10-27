import logging
import math
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float:
    """Coerce LLM-returned numerics (str, None, '5%', '1,200') to a float.

    Returns 0.0 on any failure or non-finite result. Strips commas, currency
    symbols, percent signs, and surrounding whitespace before parsing.
    """
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else 0.0
    try:
        s = str(value).strip().replace(",", "").replace("₹", "").replace("Rs", "").replace("INR", "")
        if s.endswith("%"):
            s = s[:-1].strip()
        if not s:
            return 0.0
        f = float(s)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def derive_discount(item: Dict[str, Any], quantity: float, rate: float) -> Tuple[float, float, List[str]]:
    """Resolve a line item's discount to a canonical (amount, percent) pair.

    Invoices express discounts in two mutually-exclusive ways: a flat rupee
    amount (e.g. "Discount: ₹100") or a percentage (e.g. "Discount: 5%"). The
    LLM extractor may have populated either field, both, or neither. This
    function picks the explicit one and computes the other so downstream
    code never has to second-guess.

    Returns: (discount_amount, discount_percentage, warnings)

    Resolution rules:
    - Both > 0 → trust amount, recompute percent from it (amount is unambiguous).
    - Only amount > 0 → use it, compute percent.
    - Only percent > 0 → compute amount from quantity × rate × percent / 100.
    - Neither → (0, 0).

    Clamping:
    - Negatives → 0 (warning).
    - Percent > 100 → 100 (warning).
    - Amount > quantity × rate → clamped to qty × rate (warning); a discount
      that erases more than the line itself usually means the LLM mis-read
      the column.
    """
    warnings: List[str] = []
    qty = max(_safe_float(quantity), 0.0)
    r = max(_safe_float(rate), 0.0)
    line_gross = qty * r

    extracted_amount = _safe_float(item.get("discount_amount"))
    # Accept either field name from the LLM — some prompts ask for
    # `discount_percent`, others `discount_percentage`.
    extracted_percent = _safe_float(
        item.get("discount_percentage") if item.get("discount_percentage") not in (None, "", 0)
        else item.get("discount_percent")
    )

    if extracted_amount < 0:
        warnings.append(f"discount_amount {extracted_amount} < 0 — reset to 0")
        extracted_amount = 0.0
    if extracted_percent < 0:
        warnings.append(f"discount_percentage {extracted_percent} < 0 — reset to 0")
        extracted_percent = 0.0
    if extracted_percent > 100:
        warnings.append(f"discount_percentage {extracted_percent} > 100 — clamped to 100")
        extracted_percent = 100.0

    if extracted_amount > 0:
        if line_gross > 0 and extracted_amount > line_gross:
            warnings.append(
                f"discount_amount {extracted_amount:.2f} exceeds line gross {line_gross:.2f} — clamped"
            )
            extracted_amount = line_gross
        final_amount = round(extracted_amount, 2)
        final_percent = round((final_amount / line_gross) * 100, 4) if line_gross > 0 else 0.0
    elif extracted_percent > 0:
        if line_gross > 0:
            final_amount = round(line_gross * extracted_percent / 100.0, 2)
        else:
            final_amount = 0.0
        final_percent = extracted_percent
    else:
        final_amount = 0.0
        final_percent = 0.0

    return final_amount, final_percent, warnings


def fix_line_items(
    items: List[Dict[str, Any]],
    subtotal: float = 0,
    item_name_key: str = "item_name",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Fix common LLM extraction errors in line items:
    1. Qty/Rate swap detection — catches cases the prompt heuristic misses
    2. Amount recalculation — ensures amount == qty × rate
    3. Subtotal cross-validation — warns when sum of amounts diverges from document subtotal

    Returns (fixed_items, warnings).
    """
    warnings: List[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        try:
            qty = float(item.get("quantity") or 0)
            rate = float(item.get("rate") or 0)
            amount = float(item.get("amount") or 0)
        except (TypeError, ValueError):
            continue

        name = item.get(item_name_key) or item.get("item_name") or item.get("name") or "item"

        # --- Pass 1: heuristic swap detection ---
        # These patterns indicate the LLM placed the total amount in qty and left rate=1,
        # or confused the columns in a multi-column invoice table.
        swapped = False
        if qty > 0 and rate > 0:
            # Heuristic A: rate is ≤ 1 (LLM default sentinel) but qty is non-trivial
            if rate <= 1.0 and qty > 10:
                swapped = True
            # Heuristic B: qty is more than 100× rate and qty itself is large — extreme ratio
            elif qty > rate * 100 and qty > 100:
                swapped = True

        if swapped:
            qty, rate = rate, qty
            item["quantity"] = qty
            item["rate"] = rate
            warnings.append(
                f"'{name}': qty/rate appear swapped — corrected to qty={qty}, rate={rate}"
            )

        # --- Pass 2: recalculate amount from (possibly corrected) qty × rate ---
        if qty > 0 and rate > 0:
            calculated = round(qty * rate, 2)
            if amount > 0:
                pct_diff = abs(calculated - amount) / max(amount, 1)
                if pct_diff <= 0.05:
                    # Within 5% — trust the extracted amount (could include rounding)
                    item["amount"] = amount
                else:
                    warnings.append(
                        f"'{name}': amount {amount} → recalculated as {calculated} (qty={qty} × rate={rate})"
                    )
                    item["amount"] = calculated
            else:
                item["amount"] = calculated

    # --- Pass 3: cross-validate sum of amounts against document subtotal ---
    if subtotal and subtotal > 0:
        items_sum = sum(float(i.get("amount") or 0) for i in items if isinstance(i, dict))
        if items_sum > 0:
            mismatch_pct = abs(items_sum - subtotal) / subtotal
            if mismatch_pct > 0.05:
                warnings.append(
                    f"Line item total {items_sum:.2f} differs from document subtotal {subtotal:.2f} "
                    f"({mismatch_pct:.1%} gap) — please review amounts"
                )
                logger.warning(
                    "Subtotal mismatch: items_sum=%.2f, document_subtotal=%.2f (%.1f%%)",
                    items_sum, subtotal, mismatch_pct * 100,
                )

    return items, warnings
