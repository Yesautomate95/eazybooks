"""
QuickBooks Online sales-invoice pipeline.

Steps:
  1. extract_invoice(pdf) -> structured fields via vision
  2. Pull QBO customers + items for the user's realm
  3. Match the invoice's customer (qbo_matcher)
  4. Match each line item against QBO Items (fuzzy) — line items that don't
     match get DetailType=SalesItemLineDetail with ItemRef pointing to a
     placeholder "Services" item, OR can be left as a free-text description
     with a "match_method=new" flag for the UI to surface.
  5. Build a QBO Invoice JSON resource ready to POST to /v3/company/{realm}/invoice.

The pipeline does not actually push — the controller (or user) does that
after they've reviewed/edited the matched table. We return everything needed
to render that review screen.
"""

import logging
from typing import Any, Dict, List, Optional

from applicationca.services.qbo_aliases import get_aliases
from applicationca.services.qbo_connector import qbo_connector
from applicationca.services.qbo_doc_extractor import extract_invoice
from applicationca.services.qbo_matcher import match_entity, match_many
from applicationca.services.qbo_duplicate_check import check_invoice
from applicationca.services import qbo_tax

from ._llm import make_llm_async

logger = logging.getLogger(__name__)


# Default fallback item when an extracted line description cannot be matched.
# QBO requires every line to reference a real Item, so we surface this as the
# default and let the UI override before pushing.
_FALLBACK_ITEM_NAME_HINTS = ("Services", "Sales", "Service")


def _pick_fallback_item(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the first QBO item whose name contains one of the hints, else
    the first available item, else None."""
    for hint in _FALLBACK_ITEM_NAME_HINTS:
        for it in items:
            if hint.lower() in (it.get("name") or "").lower():
                return it
    return items[0] if items else None


async def process_sales_pdf(
    pdf_bytes: bytes,
    *,
    user_id: str,
    realm_id: Optional[str] = None,
) -> Dict[str, Any]:
    extracted = await extract_invoice(pdf_bytes)
    if "error" in extracted:
        return {"ok": False, "stage": "extract", **extracted}

    # Pull existing master data (cards already in normalized {list_id, name, ...} shape
    # via _qbo_*_card adapters in qb_controller, but we call the connector directly
    # here to also retain QBO-native fields if needed).
    raw_customers = await qbo_connector.list_customers(user_id, realm_id)
    raw_items = await qbo_connector.list_items(user_id, realm_id)

    # Normalize for the matcher
    customer_cards = [
        {
            "list_id": c.get("Id"),
            "name": c.get("DisplayName") or c.get("FullyQualifiedName"),
            "company": c.get("CompanyName"),
            "email": (c.get("PrimaryEmailAddr") or {}).get("Address"),
        }
        for c in raw_customers
    ]
    item_cards = [
        {
            "list_id": i.get("Id"),
            "name": i.get("Name"),
            "rate": i.get("UnitPrice"),
            "type": i.get("Type"),
        }
        for i in raw_items
    ]

    llm_async = make_llm_async()

    # Tier-4 alias maps: scoped per entity type so a customer alias doesn't
    # leak into an item lookup. realm_id may be None for a "default realm"
    # caller — the alias query falls back to an empty dict gracefully.
    customer_aliases = get_aliases(user_id=user_id, realm_id=realm_id or "", entity_type="customer")
    item_aliases = get_aliases(user_id=user_id, realm_id=realm_id or "", entity_type="item")

    # Step 3: customer match
    cust_match = await match_entity(
        extracted.get("vendor_or_customer", ""),
        extracted.get("vendor_or_customer_email"),
        customer_cards,
        entity_label="customer",
        llm_async=llm_async,
        aliases=customer_aliases,
    )

    # Step 4: line-item matching (fuzzy + LLM only when ambiguous)
    line_input = [
        {"name": li.get("description", ""), "email": None}
        for li in extracted.get("line_items", [])
    ]
    line_matches = await match_many(
        line_input, item_cards, entity_label="item", llm_async=llm_async, aliases=item_aliases
    )

    fallback_item = _pick_fallback_item(item_cards)

    # Step 5: build a QBO Invoice payload
    qbo_lines: List[Dict[str, Any]] = []
    review_rows: List[Dict[str, Any]] = []
    pending_actions: List[Dict[str, Any]] = []
    for li, lm in zip(extracted.get("line_items", []), line_matches):
        amount = li.get("amount")
        if amount is None:
            qty = li.get("qty") or 1
            rate = li.get("rate") or 0
            try:
                amount = float(qty) * float(rate)
            except Exception:
                amount = 0.0
        # Resolve item to use
        item_id = lm.get("list_id") or (fallback_item.get("list_id") if fallback_item else None)
        item_name = lm.get("name") or (fallback_item.get("name") if fallback_item else "Services")
        if not item_id:
            pending_actions.append({
                "type": "item",
                "name": li.get("description") or lm.get("candidate_name") or item_name,
                "reason": "no_match_no_fallback",
            })

        line = {
            "Amount": float(amount or 0),
            "DetailType": "SalesItemLineDetail",
            "Description": li.get("description") or item_name,
            "SalesItemLineDetail": {
                "ItemRef": {"value": item_id, "name": item_name} if item_id else {"name": item_name},
                "Qty": float(li.get("qty") or 1),
                "UnitPrice": float(li.get("rate") or amount or 0),
            },
        }
        qbo_lines.append(line)

        review_rows.append({
            "description": li.get("description"),
            "qty": li.get("qty"),
            "rate": li.get("rate"),
            "amount": amount,
            "discount_amount": li.get("discount_amount"),
            "tax_amount": li.get("tax_amount"),
            "match": lm,                    # for UI to show / let user override
            "qbo_line": line,               # editable
        })

    qbo_payload: Dict[str, Any] = {"Line": qbo_lines}
    if cust_match.get("list_id"):
        qbo_payload["CustomerRef"] = {"value": cust_match["list_id"], "name": cust_match["name"]}
    elif extracted.get("vendor_or_customer"):
        # Customer must exist in QBO; UI flags this and offers create-customer.
        qbo_payload["CustomerRef"] = {"name": extracted["vendor_or_customer"]}
        pending_actions.append({
            "type": "customer",
            "name": extracted["vendor_or_customer"],
            "reason": "no_match",
        })

    if extracted.get("doc_date"):
        qbo_payload["TxnDate"] = extracted["doc_date"]
    if extracted.get("due_date"):
        qbo_payload["DueDate"] = extracted["due_date"]
    if extracted.get("doc_number"):
        qbo_payload["DocNumber"] = str(extracted["doc_number"])
    # CurrencyRef intentionally omitted: QBO requires ExchangeRate alongside
    # any foreign-currency txn, and only realms with Multicurrency enabled can
    # receive non-home-currency refs at all. Letting QBO infer the realm's
    # home currency is safe for single-currency books and avoids the QBO 2410
    # "Currency, ExchangeRate is missing" rejection. Re-enable here only after
    # adding a realm-currency lookup + live FX rate fetch.

    # Tax posting (gated by QBO_POST_TAX; default off). When enabled, attach the
    # extracted tax so the QBO invoice total isn't understated.
    if qbo_tax.tax_posting_enabled():
        tax_codes = await qbo_connector.list_tax_codes(user_id, realm_id)
        qbo_tax.apply_tax(
            qbo_payload, tax_amount=extracted.get("tax_total"),
            tax_codes=tax_codes, line_detail_key="SalesItemLineDetail",
        )

    duplicate_check = await check_invoice(
        user_id=user_id,
        realm_id=realm_id,
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
        "qbo_payload": qbo_payload,
        "needs_review": needs_review,
        "pending_actions": pending_actions,
        "duplicate_check": duplicate_check,
    }
