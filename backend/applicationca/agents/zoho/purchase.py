"""
Zoho Books purchase-bill pipeline.

Mirrors agents/qbo/purchase.py but targets a Zoho Bill. Bill lines are
account-based ({account_id, description, rate, quantity}) — each line books to a
GL expense account. Vendor + per-line account suggestions come from
zoho_suggestion_service (vendor→account memory + fuzzy/LLM). Display names ride
inline (vendor_name, line_items[].account_name) for the UI / history; the
connector strips them before POSTing.
"""

import logging
from typing import Any, Dict, List, Optional

from applicationca.services import zoho_tax
from applicationca.services.zoho_aliases import get_aliases
from applicationca.services.zoho_connector import zoho_connector
from applicationca.services.qbo_doc_extractor import extract_bill
from applicationca.services.zoho_suggestion_service import suggest_for_purchase
from applicationca.services.zoho_duplicate_check import check_bill

from ._llm import make_llm_async

logger = logging.getLogger(__name__)

# Zoho chart-of-accounts types that are valid expense targets for bill lines.
_EXPENSE_ACCOUNT_TYPES = ("expense", "cost_of_goods_sold", "other_expense", "other_current_liability")
_FALLBACK_ACCOUNT_NAME_HINTS = ("office supplies", "miscellaneous", "other expense", "uncategorized")


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
        "payload_rate": round(net, 6),
        "discount_amount": round(discount, 2),
        "discount_percent": round(discount_percent, 4),
    }


def _pick_fallback_account(accounts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for hint in _FALLBACK_ACCOUNT_NAME_HINTS:
        for a in accounts:
            if hint in (a.get("name") or "").lower():
                return a
    return accounts[0] if accounts else None


async def process_purchase_pdf(
    pdf_bytes: bytes,
    *,
    user_id: str,
    organization_id: Optional[str] = None,
) -> Dict[str, Any]:
    extracted = await extract_bill(pdf_bytes)
    if "error" in extracted:
        return {"ok": False, "stage": "extract", **extracted}

    vendor_cards = await zoho_connector.list_vendors(user_id, organization_id)
    all_accounts = await zoho_connector.list_accounts(user_id, organization_id)
    expense_account_cards = [
        a for a in all_accounts
        if (a.get("account_type") or "").lower() in _EXPENSE_ACCOUNT_TYPES
    ] or all_accounts

    llm_async = make_llm_async()

    vendor_aliases = get_aliases(user_id=user_id, organization_id=organization_id or "", entity_type="vendor")
    account_aliases = get_aliases(user_id=user_id, organization_id=organization_id or "", entity_type="account")

    line_descriptions = [li.get("description", "") for li in extracted.get("line_items", [])]
    suggestion = await suggest_for_purchase(
        user_id=user_id,
        organization_id=organization_id,
        extracted_vendor_name=extracted.get("vendor_or_customer", ""),
        extracted_vendor_email=extracted.get("vendor_or_customer_email"),
        line_descriptions=line_descriptions,
        vendor_cards=vendor_cards,
        account_cards=expense_account_cards,
        llm_async=llm_async,
        vendor_aliases=vendor_aliases,
        account_aliases=account_aliases,
    )
    vendor_match = suggestion["vendor_match"]
    line_matches = suggestion["line_matches"]

    fallback_account = _pick_fallback_account(expense_account_cards)

    zoho_lines: List[Dict[str, Any]] = []
    review_rows: List[Dict[str, Any]] = []
    pending_actions: List[Dict[str, Any]] = []
    for li, lm in zip(extracted.get("line_items", []), line_matches):
        money = _line_amounts(li)
        qty = money["qty"]
        rate = money["display_rate"]
        amount = money["net_amount"]

        account_id = lm.get("list_id") or (fallback_account.get("list_id") if fallback_account else None)
        account_name = lm.get("name") or (fallback_account.get("name") if fallback_account else "Office Supplies")
        if not account_id:
            pending_actions.append({
                "type": "account",
                "name": li.get("description") or lm.get("candidate_name") or account_name,
                "reason": "no_match_no_fallback",
            })

        line: Dict[str, Any] = {
            "name": li.get("description") or account_name,
            "description": li.get("description") or account_name,
            "rate": money["payload_rate"],
            "quantity": 1,
        }
        if account_id:
            line["account_id"] = account_id
            line["account_name"] = account_name  # display-only; stripped before POST
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
    if vendor_match.get("list_id"):
        zoho_payload["vendor_id"] = vendor_match["list_id"]
        zoho_payload["vendor_name"] = vendor_match.get("name")
    elif extracted.get("vendor_or_customer"):
        zoho_payload["vendor_name"] = extracted["vendor_or_customer"]
        pending_actions.append({
            "type": "vendor",
            "name": extracted["vendor_or_customer"],
            "reason": "no_match",
        })

    if extracted.get("doc_date"):
        zoho_payload["date"] = extracted["doc_date"]
    if extracted.get("due_date"):
        zoho_payload["due_date"] = extracted["due_date"]
    if extracted.get("doc_number"):
        zoho_payload["bill_number"] = str(extracted["doc_number"])

    # Tax posting (gated by ZOHO_POST_TAX; default off). When enabled, attach the
    # extracted tax so the Zoho bill total isn't understated.
    if zoho_tax.tax_posting_enabled():
        tax_codes = await zoho_connector.list_tax_codes(user_id, organization_id)
        zoho_tax.apply_tax(
            zoho_payload, tax_amount=extracted.get("tax_total"),
            tax_codes=tax_codes, line_detail_key="line_items",
        )

    duplicate_check = await check_bill(
        user_id=user_id,
        organization_id=organization_id,
        doc_date=extracted.get("doc_date"),
        total_amount=extracted.get("total"),
        doc_number=extracted.get("doc_number"),
        vendor_name=extracted.get("vendor_or_customer") or (vendor_match.get("name") if vendor_match else None),
    )

    needs_review = (
        not vendor_match.get("matched")
        or any(not m.get("matched") for m in line_matches)
        or bool(pending_actions)
        or duplicate_check.get("is_duplicate", False)
    )

    return {
        "ok": True,
        "extracted": extracted,
        "vendor_match": vendor_match,
        "line_review": review_rows,
        "zoho_payload": zoho_payload,
        "needs_review": needs_review,
        "pending_actions": pending_actions,
        "duplicate_check": duplicate_check,
    }
