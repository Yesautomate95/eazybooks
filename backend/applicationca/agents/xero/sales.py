"""
Xero sales-invoice pipeline (Type=ACCREC).

Mirrors agents/zoho/sales.py. Xero specifics:
  * Line items reference items by ItemCode and revenue accounts by AccountCode
    (string codes), NOT GUIDs — so after matching we look the matched card up by
    list_id (the ItemID/AccountID) to read its `code`.
  * A Xero invoice line needs EITHER an ItemCode OR an AccountCode. Matched lines
    use ItemCode; unmatched lines fall back to a revenue AccountCode.
  * The Contact is referenced by ContactID.
  * Display-only fields (_item_id/_item_name, customer_name) ride inline for the
    UI / history and are stripped by the connector before POST.
"""

import logging
from typing import Any, Dict, List, Optional

from applicationca.services.xero_aliases import get_aliases
from applicationca.services.xero_connector import xero_connector
from applicationca.services.qbo_doc_extractor import extract_invoice
from applicationca.services.qbo_matcher import match_entity, match_many
from applicationca.services.xero_duplicate_check import check_invoice
from applicationca.services import xero_tax

from ._llm import make_llm_async

logger = logging.getLogger(__name__)

_REVENUE_TYPES = ("REVENUE", "SALES", "OTHERINCOME")


def _pick_revenue_account(accounts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for a in accounts:
        if (a.get("account_type") or "").upper() in _REVENUE_TYPES:
            return a
    return accounts[0] if accounts else None


async def process_sales_pdf(
    pdf_bytes: bytes,
    *,
    user_id: str,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    extracted = await extract_invoice(pdf_bytes)
    if "error" in extracted:
        return {"ok": False, "stage": "extract", **extracted}

    customer_cards = await xero_connector.list_customers(user_id, tenant_id)
    item_cards = await xero_connector.list_items(user_id, tenant_id)
    accounts = await xero_connector.list_accounts(user_id, tenant_id)
    item_by_id = {c["list_id"]: c for c in item_cards if c.get("list_id")}
    fallback_revenue = _pick_revenue_account(accounts)

    llm_async = make_llm_async()
    customer_aliases = get_aliases(user_id=user_id, tenant_id=tenant_id or "", entity_type="customer")
    item_aliases = get_aliases(user_id=user_id, tenant_id=tenant_id or "", entity_type="item")

    cust_match = await match_entity(
        extracted.get("vendor_or_customer", ""),
        extracted.get("vendor_or_customer_email"),
        customer_cards,
        entity_label="customer",
        llm_async=llm_async,
        aliases=customer_aliases,
    )

    line_input = [{"name": li.get("description", ""), "email": None} for li in extracted.get("line_items", [])]
    line_matches = await match_many(line_input, item_cards, entity_label="item", llm_async=llm_async, aliases=item_aliases)

    xero_lines: List[Dict[str, Any]] = []
    review_rows: List[Dict[str, Any]] = []
    pending_actions: List[Dict[str, Any]] = []
    for li, lm in zip(extracted.get("line_items", []), line_matches):
        amount = li.get("amount")
        qty = li.get("qty") or 1
        rate = li.get("rate")
        if rate is None:
            try:
                rate = float(amount) / float(qty) if amount is not None and float(qty) else (amount or 0)
            except Exception:
                rate = amount or 0
        if amount is None:
            try:
                amount = float(qty) * float(rate or 0)
            except Exception:
                amount = 0.0

        matched_item = item_by_id.get(lm.get("list_id"))
        line: Dict[str, Any] = {
            "Description": li.get("description") or (matched_item.get("name") if matched_item else "Item"),
            "Quantity": float(qty or 1),
            "UnitAmount": float(rate or 0),
        }
        if matched_item and matched_item.get("code"):
            line["ItemCode"] = matched_item["code"]
            line["_item_id"] = matched_item.get("list_id")
            line["_item_name"] = matched_item.get("name")
        else:
            # No item match -> line needs a revenue AccountCode.
            if fallback_revenue and fallback_revenue.get("code"):
                line["AccountCode"] = fallback_revenue["code"]
            pending_actions.append({
                "type": "item",
                "name": li.get("description") or lm.get("candidate_name") or "Item",
                "reason": "no_match",
            })
        xero_lines.append(line)
        review_rows.append({
            "description": li.get("description"), "qty": qty, "rate": rate,
            "amount": amount, "tax_amount": li.get("tax_amount"),
            "match": lm, "xero_line": line,
        })

    xero_payload: Dict[str, Any] = {"Type": "ACCREC", "LineItems": xero_lines, "Status": "DRAFT"}
    if cust_match.get("list_id"):
        xero_payload["Contact"] = {"ContactID": cust_match["list_id"]}
        xero_payload["customer_name"] = cust_match.get("name")
    elif extracted.get("vendor_or_customer"):
        xero_payload["Contact"] = {"Name": extracted["vendor_or_customer"]}
        xero_payload["customer_name"] = extracted["vendor_or_customer"]
        pending_actions.append({"type": "customer", "name": extracted["vendor_or_customer"], "reason": "no_match"})

    if extracted.get("doc_date"):
        xero_payload["Date"] = extracted["doc_date"]
    if extracted.get("due_date"):
        xero_payload["DueDate"] = extracted["due_date"]
    if extracted.get("doc_number"):
        xero_payload["InvoiceNumber"] = str(extracted["doc_number"])

    # Tax posting (gated by XERO_POST_TAX; default off). When enabled, attach the
    # extracted tax so the Xero invoice total isn't understated.
    if xero_tax.tax_posting_enabled():
        tax_rates = await xero_connector.list_tax_rates(user_id, tenant_id)
        xero_tax.apply_tax(
            xero_payload, tax_amount=extracted.get("tax_total"),
            tax_rates=tax_rates, side="revenue",
        )

    duplicate_check = await check_invoice(
        user_id=user_id, tenant_id=tenant_id,
        doc_date=extracted.get("doc_date"), total_amount=extracted.get("total"),
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
        "ok": True, "extracted": extracted, "customer_match": cust_match,
        "line_review": review_rows, "xero_payload": xero_payload,
        "needs_review": needs_review, "pending_actions": pending_actions,
        "duplicate_check": duplicate_check,
    }
