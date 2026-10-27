"""
Xero purchase-bill pipeline (Type=ACCPAY).

Mirrors agents/zoho/purchase.py. Xero specifics:
  * A bill is an Invoice with Type=ACCPAY. Lines are account-based and reference
    the expense account by AccountCode (the string Code), NOT the AccountID — so
    after matching we read the matched account card's `code`.
  * The Contact (vendor) is referenced by ContactID.
  * Display-only fields ride inline for history/UI (vendor_name top-level;
    _account_id / _account_name on each line) and are stripped before POST.
"""

import logging
from typing import Any, Dict, List, Optional

from applicationca.services.xero_aliases import get_aliases
from applicationca.services.xero_connector import xero_connector
from applicationca.services.qbo_doc_extractor import extract_bill
from applicationca.services.xero_suggestion_service import suggest_for_purchase
from applicationca.services.xero_duplicate_check import check_bill
from applicationca.services import xero_tax

from ._llm import make_llm_async

logger = logging.getLogger(__name__)

# Xero account Types that are valid expense targets for bill lines.
_EXPENSE_TYPES = ("EXPENSE", "DIRECTCOSTS", "OVERHEADS", "CURRLIAB")
_FALLBACK_ACCOUNT_NAME_HINTS = ("office", "miscellaneous", "general expense", "uncategor")


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
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    extracted = await extract_bill(pdf_bytes)
    if "error" in extracted:
        return {"ok": False, "stage": "extract", **extracted}

    vendor_cards = await xero_connector.list_vendors(user_id, tenant_id)
    all_accounts = await xero_connector.list_accounts(user_id, tenant_id)
    expense_account_cards = [
        a for a in all_accounts if (a.get("account_type") or "").upper() in _EXPENSE_TYPES
    ] or all_accounts
    account_by_id = {c["list_id"]: c for c in expense_account_cards if c.get("list_id")}

    llm_async = make_llm_async()
    vendor_aliases = get_aliases(user_id=user_id, tenant_id=tenant_id or "", entity_type="vendor")
    account_aliases = get_aliases(user_id=user_id, tenant_id=tenant_id or "", entity_type="account")

    line_descriptions = [li.get("description", "") for li in extracted.get("line_items", [])]
    suggestion = await suggest_for_purchase(
        user_id=user_id,
        tenant_id=tenant_id,
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

        matched = account_by_id.get(lm.get("list_id"))
        account_card = matched or fallback_account
        if not matched:
            pending_actions.append({
                "type": "account",
                "name": li.get("description") or lm.get("candidate_name") or "account",
                "reason": "no_match_no_fallback" if not fallback_account else "fallback",
            })

        line: Dict[str, Any] = {
            "Description": li.get("description") or (account_card.get("name") if account_card else "Expense"),
            "Quantity": float(qty or 1),
            "UnitAmount": float(rate or 0),
        }
        if account_card and account_card.get("code"):
            line["AccountCode"] = account_card["code"]
            line["_account_id"] = account_card.get("list_id")   # AccountID, for history
            line["_account_name"] = account_card.get("name")
        xero_lines.append(line)
        review_rows.append({
            "description": li.get("description"), "qty": qty, "rate": rate,
            "amount": amount, "tax_amount": li.get("tax_amount"),
            "match": lm, "xero_line": line,
        })

    xero_payload: Dict[str, Any] = {"Type": "ACCPAY", "LineItems": xero_lines, "Status": "DRAFT"}
    if vendor_match.get("list_id"):
        xero_payload["Contact"] = {"ContactID": vendor_match["list_id"]}
        xero_payload["vendor_name"] = vendor_match.get("name")
    elif extracted.get("vendor_or_customer"):
        xero_payload["Contact"] = {"Name": extracted["vendor_or_customer"]}
        xero_payload["vendor_name"] = extracted["vendor_or_customer"]
        pending_actions.append({"type": "vendor", "name": extracted["vendor_or_customer"], "reason": "no_match"})

    if extracted.get("doc_date"):
        xero_payload["Date"] = extracted["doc_date"]
    if extracted.get("due_date"):
        xero_payload["DueDate"] = extracted["due_date"]
    if extracted.get("doc_number"):
        xero_payload["InvoiceNumber"] = str(extracted["doc_number"])

    # Tax posting (gated by XERO_POST_TAX; default off). When enabled, attach the
    # extracted tax so the Xero bill total isn't understated.
    if xero_tax.tax_posting_enabled():
        tax_rates = await xero_connector.list_tax_rates(user_id, tenant_id)
        xero_tax.apply_tax(
            xero_payload, tax_amount=extracted.get("tax_total"),
            tax_rates=tax_rates, side="expense",
        )

    duplicate_check = await check_bill(
        user_id=user_id, tenant_id=tenant_id,
        doc_date=extracted.get("doc_date"), total_amount=extracted.get("total"),
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
        "ok": True, "extracted": extracted, "vendor_match": vendor_match,
        "line_review": review_rows, "xero_payload": xero_payload,
        "needs_review": needs_review, "pending_actions": pending_actions,
        "duplicate_check": duplicate_check,
    }
