"""
Zoho Books sales-invoice pipeline.

Mirrors agents/qbo/sales.py:
  1. extract_invoice(pdf) -> structured fields via the shared vision extractor.
  2. Pull Zoho customers + items for the org (zoho_connector returns normalized
     cards, so no re-normalization needed here).
  3. Match the customer (shared qbo_matcher).
  4. Match each line against Zoho Items (fuzzy + LLM when ambiguous).
  5. Build a Zoho Invoice payload ready to POST to /invoices.

Zoho invoice lines are ITEM-based ({item_id, name, description, rate, quantity}).
Lines that don't match an item are sent as name+rate (Zoho accepts ad-hoc
lines). Display names ride inline on the payload (customer_name) for the UI /
dedup; the connector strips them before POSTing.

The pipeline does not push — the controller (or user) does that after review.
"""

import logging
from typing import Any, Dict, List, Optional

from applicationca.services import zoho_tax
from applicationca.services.zoho_aliases import get_aliases
from applicationca.services.zoho_connector import zoho_connector
from applicationca.services.qbo_doc_extractor import extract_invoice
from applicationca.services.qbo_matcher import match_entity, match_many
from applicationca.services.zoho_duplicate_check import check_invoice

from ._llm import make_llm_async

logger = logging.getLogger(__name__)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _line_amounts(li: Dict[str, Any]) -> Dict[str, float]:
    qty = _num(li.get("qty"), 1.0) or 1.0
    amount = _num(li.get("amount"), 0.0)
    raw_rate = li.get("rate")
    rate = _num(raw_rate, 0.0)
    discount = _num(li.get("discount_amount"), 0.0)
    discount_percent = _num(
        li.get("discount_percentage") if li.get("discount_percentage") not in (None, "", 0)
        else li.get("discount_percent"),
        0.0,
    )

    if discount > 0 or discount_percent > 0:
        gross_from_rate = qty * rate if raw_rate not in (None, "") and rate > 0 else 0.0
        gross_from_amount = amount + discount if discount > 0 else amount
        gross = max(gross_from_rate, gross_from_amount, amount, 0.0)
        if discount <= 0 and discount_percent > 0 and gross > 0:
            discount = round(gross * discount_percent / 100.0, 2)
        discount = min(discount, gross)
        net = max(gross - discount, 0.0)
    else:
        net = amount if amount > 0 else max(qty * rate, 0.0)
        gross = net

    return {
        "qty": qty,
        "gross_amount": round(gross, 2),
        "net_amount": round(net, 2),
        "display_rate": round(gross / qty, 6) if qty else round(gross, 6),
        "payload_rate": round(net / qty, 6) if qty else round(net, 6),
        "discount_amount": round(discount, 2),
        "discount_percent": round(discount_percent, 4),
    }


async def process_sales_pdf(
    pdf_bytes: bytes,
    *,
    user_id: str,
    organization_id: Optional[str] = None,
) -> Dict[str, Any]:
    extracted = await extract_invoice(pdf_bytes)
    if "error" in extracted:
        return {"ok": False, "stage": "extract", **extracted}

    customer_cards = await zoho_connector.list_customers(user_id, organization_id)
    item_cards = await zoho_connector.list_items(user_id, organization_id)

    llm_async = make_llm_async()

    customer_aliases = get_aliases(user_id=user_id, organization_id=organization_id or "", entity_type="customer")
    item_aliases = get_aliases(user_id=user_id, organization_id=organization_id or "", entity_type="item")

    cust_match = await match_entity(
        extracted.get("vendor_or_customer", ""),
        extracted.get("vendor_or_customer_email"),
        customer_cards,
        entity_label="customer",
        llm_async=llm_async,
        aliases=customer_aliases,
    )

    line_input = [
        {"name": li.get("description", ""), "email": None}
        for li in extracted.get("line_items", [])
    ]
    line_matches = await match_many(
        line_input, item_cards, entity_label="item", llm_async=llm_async, aliases=item_aliases
    )

    zoho_lines: List[Dict[str, Any]] = []
    review_rows: List[Dict[str, Any]] = []
    pending_actions: List[Dict[str, Any]] = []
    for li, lm in zip(extracted.get("line_items", []), line_matches):
        money = _line_amounts(li)
        qty = money["qty"]
        rate = money["display_rate"]
        amount = money["net_amount"]

        item_id = lm.get("list_id")
        item_name = lm.get("name") or li.get("description") or "Item"
        if not item_id:
            pending_actions.append({
                "type": "item",
                "name": li.get("description") or lm.get("candidate_name") or item_name,
                "reason": "no_match",
            })

        line: Dict[str, Any] = {
            "name": item_name,
            "description": li.get("description") or item_name,
            "rate": money["payload_rate"],
            "quantity": qty,
        }
        if item_id:
            line["item_id"] = item_id
        zoho_lines.append(line)

        review_rows.append({
            "description": li.get("description"),
            "qty": qty,
            "rate": rate,
            "amount": amount,
            "gross_amount": money["gross_amount"],
            "discount_amount": money["discount_amount"],
            "discount_percent": money["discount_percent"],
            "tax_amount": li.get("tax_amount"),
            "match": lm,
            "zoho_line": line,
        })

    zoho_payload: Dict[str, Any] = {"line_items": zoho_lines}
    if cust_match.get("list_id"):
        zoho_payload["customer_id"] = cust_match["list_id"]
        zoho_payload["customer_name"] = cust_match.get("name")
    elif extracted.get("vendor_or_customer"):
        # Zoho requires a customer_id; UI flags this and offers create-customer.
        zoho_payload["customer_name"] = extracted["vendor_or_customer"]
        pending_actions.append({
            "type": "customer",
            "name": extracted["vendor_or_customer"],
            "reason": "no_match",
        })

    if extracted.get("doc_date"):
        zoho_payload["date"] = extracted["doc_date"]
    if extracted.get("due_date"):
        zoho_payload["due_date"] = extracted["due_date"]
    if extracted.get("doc_number"):
        zoho_payload["invoice_number"] = str(extracted["doc_number"])

    # Tax posting (gated by ZOHO_POST_TAX; default off). When enabled, attach the
    # extracted tax so the Zoho invoice total isn't understated.
    if zoho_tax.tax_posting_enabled():
        tax_codes = await zoho_connector.list_tax_codes(user_id, organization_id)
        zoho_tax.apply_tax(
            zoho_payload, tax_amount=extracted.get("tax_total"),
            tax_codes=tax_codes, line_detail_key="line_items",
        )

    duplicate_check = await check_invoice(
        user_id=user_id,
        organization_id=organization_id,
        doc_date=extracted.get("doc_date"),
        total_amount=extracted.get("total"),
        doc_number=extracted.get("doc_number"),
        customer_name=extracted.get("vendor_or_customer") or (cust_match.get("name") if cust_match else None),
    )

    needs_review = (
        not cust_match.get("matched")
        or any(not m.get("matched") for m in line_matches)
        or bool(pending_actions)
        or duplicate_check.get("is_duplicate", False)
    )

    return {
        "ok": True,
        "extracted": extracted,
        "customer_match": cust_match,
        "line_review": review_rows,
        "zoho_payload": zoho_payload,
        "needs_review": needs_review,
        "pending_actions": pending_actions,
        "duplicate_check": duplicate_check,
    }
