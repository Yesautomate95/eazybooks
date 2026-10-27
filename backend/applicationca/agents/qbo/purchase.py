"""
QuickBooks Online purchase-bill pipeline.

Mirrors sales.py but targets QBO Bill instead of Invoice. Lines are
account-based expenses (DetailType=AccountBasedExpenseLineDetail) rather
than item-based, which fits the typical bill where you book each line to a
GL expense account.
"""

import logging
from typing import Any, Dict, List, Optional

from applicationca.services.qbo_aliases import get_aliases
from applicationca.services.qbo_connector import qbo_connector
from applicationca.services.qbo_doc_extractor import extract_bill
from applicationca.services.qbo_suggestion_service import suggest_for_purchase
from applicationca.services.qbo_duplicate_check import check_bill
from applicationca.services import qbo_tax

from ._llm import make_llm_async

logger = logging.getLogger(__name__)


_FALLBACK_ACCOUNT_TYPES = ("Expense", "OtherCurrentLiability")
_FALLBACK_ACCOUNT_NAME_HINTS = ("Office Supplies", "Miscellaneous", "Other Expense", "Uncategorized Expense")


def _pick_fallback_account(accounts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for hint in _FALLBACK_ACCOUNT_NAME_HINTS:
        for a in accounts:
            if hint.lower() in (a.get("name") or "").lower():
                return a
    for a in accounts:
        if a.get("account_type") in _FALLBACK_ACCOUNT_TYPES:
            return a
    return accounts[0] if accounts else None


async def process_purchase_pdf(
    pdf_bytes: bytes,
    *,
    user_id: str,
    realm_id: Optional[str] = None,
) -> Dict[str, Any]:
    extracted = await extract_bill(pdf_bytes)
    if "error" in extracted:
        return {"ok": False, "stage": "extract", **extracted}

    raw_vendors = await qbo_connector.list_vendors(user_id, realm_id)
    raw_accounts = await qbo_connector.list_accounts(user_id, realm_id)

    vendor_cards = [
        {
            "list_id": v.get("Id"),
            "name": v.get("DisplayName") or v.get("FullyQualifiedName"),
            "company": v.get("CompanyName"),
            "email": (v.get("PrimaryEmailAddr") or {}).get("Address"),
        }
        for v in raw_vendors
    ]
    # Only expense-side accounts are realistic targets for bill lines.
    expense_account_cards = [
        {
            "list_id": a.get("Id"),
            "name": a.get("Name") or a.get("FullyQualifiedName"),
            "account_type": a.get("AccountType"),
        }
        for a in raw_accounts
        if a.get("AccountType") in ("Expense", "CostOfGoodsSold", "OtherExpense", "OtherCurrentLiability")
    ]

    llm_async = make_llm_async()

    # Tier-4 alias maps for vendor + expense-account refs, threaded through
    # the orchestrator so alias hits short-circuit fuzzy and history lookups.
    vendor_aliases = get_aliases(user_id=user_id, realm_id=realm_id or "", entity_type="vendor")
    account_aliases = get_aliases(user_id=user_id, realm_id=realm_id or "", entity_type="account")

    line_descriptions = [li.get("description", "") for li in extracted.get("line_items", [])]
    suggestion = await suggest_for_purchase(
        user_id=user_id,
        realm_id=realm_id,
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

        account_id = lm.get("list_id") or (fallback_account.get("list_id") if fallback_account else None)
        account_name = lm.get("name") or (fallback_account.get("name") if fallback_account else "Office Supplies")
        if not account_id:
            pending_actions.append({
                "type": "account",
                "name": li.get("description") or lm.get("candidate_name") or account_name,
                "reason": "no_match_no_fallback",
            })

        line = {
            "Amount": float(amount or 0),
            "DetailType": "AccountBasedExpenseLineDetail",
            "Description": li.get("description") or account_name,
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": account_id, "name": account_name} if account_id else {"name": account_name},
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
            "match": lm,
            "qbo_line": line,
        })

    qbo_payload: Dict[str, Any] = {"Line": qbo_lines}
    if vendor_match.get("list_id"):
        qbo_payload["VendorRef"] = {"value": vendor_match["list_id"], "name": vendor_match["name"]}
    elif extracted.get("vendor_or_customer"):
        qbo_payload["VendorRef"] = {"name": extracted["vendor_or_customer"]}
        pending_actions.append({
            "type": "vendor",
            "name": extracted["vendor_or_customer"],
            "reason": "no_match",
        })

    if extracted.get("doc_date"):
        qbo_payload["TxnDate"] = extracted["doc_date"]
    if extracted.get("due_date"):
        qbo_payload["DueDate"] = extracted["due_date"]
    if extracted.get("doc_number"):
        qbo_payload["DocNumber"] = str(extracted["doc_number"])
    # CurrencyRef intentionally omitted — see same comment in sales.py.
    # QBO rejects (code 2410) when CurrencyRef differs from realm home
    # currency without an ExchangeRate. Inferring home currency is the safe
    # default for single-currency books, which is the demo / sandbox case.

    # Tax posting (gated by QBO_POST_TAX; default off). When enabled, attach the
    # extracted tax so the QBO bill total isn't understated.
    if qbo_tax.tax_posting_enabled():
        tax_codes = await qbo_connector.list_tax_codes(user_id, realm_id)
        qbo_tax.apply_tax(
            qbo_payload, tax_amount=extracted.get("tax_total"),
            tax_codes=tax_codes, line_detail_key="AccountBasedExpenseLineDetail",
        )

    # Duplicate detection — runs against local imports + QBO. Looser key:
    # date + amount alone flags a hit; doc # / vendor match bump confidence.
    duplicate_check = await check_bill(
        user_id=user_id,
        realm_id=realm_id,
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
        "qbo_payload": qbo_payload,
        "needs_review": needs_review,
        "pending_actions": pending_actions,
        "duplicate_check": duplicate_check,
    }
