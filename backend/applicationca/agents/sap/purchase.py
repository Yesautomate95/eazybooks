"""
SAP S/4HANA Cloud purchase / supplier-invoice (A/P) pipeline.

Template: applicationca/agents/zoho/purchase.py — same EXTRACT -> PULL -> MATCH ->
BUILD -> RETURN shape, adapted to the SAP FI data model:

  * The company key is `company_code` (SAP Company Code), not Zoho's
    organization_id / Xero's tenant_id.
  * SAP FI has no item master — the GL accounts ARE the line ledgers. Where the
    Zoho pipeline matched bill lines against expense *accounts*, we do the same
    here (account_cards from sap_connector.list_accounts), but the matched id is
    a GLAccount that goes straight onto a supplier-invoice line.
  * BUILD step differs: instead of a Zoho Bill body we build a SAP supplier
    invoice doc for sap_connector.post_supplier_invoice (OData
    API_SUPPLIERINVOICE_PROCESS_SRV):
        {invoice_id, invoicing_party=<vendor BP id>, document_date, posting_date,
         currency, gross_amount=<total>,
         items:[{gl_account:<expense GL id>, amount:<net>, dc:'S', tax_code?, text?}],
         tax_items?:[{item, tax_code, amount}]}
    Each expense line is a debit ('S'); SAP balances it against the A/P
    reconciliation account of `invoicing_party` automatically. InvoiceGrossAmount
    must equal net + tax.

Display names ride inline in a `_meta` block (vendor_name, line account_name) so
the controller can record vendor->account / item->account history and dedup names;
the controller strips `_meta` before POSTing the doc to SAP. `extracted_total` is
threaded through for dedupe parity with the Zoho pipeline.

The pipeline does not push — the controller (or user) does that after review.
"""

import logging
from typing import Any, Dict, List, Optional

from applicationca.services.sap_aliases import get_aliases
from applicationca.services.sap_connector import sap_connector
from applicationca.services.qbo_doc_extractor import extract_bill
from applicationca.services.sap_suggestion_service import suggest_for_purchase
from applicationca.services.sap_duplicate_check import check_bill

from ._llm import make_llm_async

logger = logging.getLogger(__name__)

# SAP GL accounts carry a normalized `account_type` on the card (when the
# connector can infer it). These are the types that are valid expense targets for
# a supplier-invoice line. If none of the cards carry a type we fall back to the
# full GL list (SAP's account_type inference is best-effort).
_EXPENSE_ACCOUNT_TYPES = ("expense", "cost_of_goods_sold", "other_expense", "other_current_liability")
_FALLBACK_ACCOUNT_NAME_HINTS = ("office supplies", "miscellaneous", "other expense", "uncategorized", "sundry")


def _pick_fallback_account(accounts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for hint in _FALLBACK_ACCOUNT_NAME_HINTS:
        for a in accounts:
            if hint in (a.get("name") or "").lower():
                return a
    return accounts[0] if accounts else None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def process_purchase_pdf(
    pdf_bytes: bytes,
    *,
    user_id: str,
    company_code: str,
) -> Dict[str, Any]:
    extracted = await extract_bill(pdf_bytes)
    if "error" in extracted:
        return {"ok": False, "stage": "extract", **extracted}

    vendor_cards = await sap_connector.list_vendors(user_id, company_code)
    all_accounts = await sap_connector.list_accounts(user_id, company_code)
    expense_account_cards = [
        a for a in all_accounts
        if (a.get("account_type") or "").lower() in _EXPENSE_ACCOUNT_TYPES
    ] or all_accounts

    llm_async = make_llm_async()

    vendor_aliases = get_aliases(user_id=user_id, company_code=company_code or "", entity_type="vendor")
    account_aliases = get_aliases(user_id=user_id, company_code=company_code or "", entity_type="account")

    line_descriptions = [li.get("description", "") for li in extracted.get("line_items", [])]
    suggestion = await suggest_for_purchase(
        user_id=user_id,
        company_code=company_code,
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
    currency = extracted.get("currency") or "INR"

    # ------------------------------------------------------------------
    # BUILD — expense GL lines for the supplier invoice (each a debit 'S').
    # ------------------------------------------------------------------
    invoice_items: List[Dict[str, Any]] = []   # post_supplier_invoice `items`
    meta_lines: List[Dict[str, Any]] = []      # display names for history/_meta
    review_rows: List[Dict[str, Any]] = []
    pending_actions: List[Dict[str, Any]] = []
    net_total = 0.0
    tax_total = 0.0

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

        line_net = _to_float(amount)
        line_tax = _to_float(li.get("tax_amount"))
        net_total += line_net
        tax_total += line_tax

        gl_account = lm.get("list_id") or (fallback_account.get("list_id") if fallback_account else None)
        account_name = lm.get("name") or (fallback_account.get("name") if fallback_account else "Office Supplies")
        if not gl_account:
            pending_actions.append({
                "type": "account",
                "name": li.get("description") or lm.get("candidate_name") or account_name,
                "reason": "no_match_no_fallback",
            })

        item: Dict[str, Any] = {
            "amount": round(line_net, 2),
            "dc": "S",  # expense lines debit; SAP credits the supplier A/P recon account
            "text": (li.get("description") or account_name)[:50],
        }
        if gl_account:
            item["gl_account"] = gl_account
        invoice_items.append(item)
        meta_lines.append({
            "description": li.get("description"),
            "account_id": gl_account,
            "account_name": account_name,
        })

        review_rows.append({
            "description": li.get("description"),
            "qty": qty,
            "rate": rate,
            "amount": amount,
            "tax_amount": li.get("tax_amount"),
            "match": lm,
            "sap_item": item,
        })

    # Gross = extracted total when present, else net + tax we summed from lines.
    extracted_total = extracted.get("total")
    gross_amount = _to_float(extracted_total) if extracted_total is not None else round(net_total + tax_total, 2)

    # SAP rejects a supplier invoice whose InvoiceGrossAmount != net + tax. If the
    # OCR'd total disagrees with our summed lines, flag it for human reconciliation
    # instead of letting SAP reject the post.
    computed_gross = round(net_total + tax_total, 2)
    if extracted_total is not None and abs(round(gross_amount, 2) - computed_gross) > 0.01:
        pending_actions.append({
            "type": "gross_mismatch",
            "reason": "extracted_total_ne_line_sum",
            "extracted_total": round(_to_float(extracted_total), 2),
            "computed": computed_gross,
        })

    # ------------------------------------------------------------------
    # BUILD — supplier-invoice doc for sap_connector.post_supplier_invoice.
    # ------------------------------------------------------------------
    sap_payload: Dict[str, Any] = {
        "currency": currency,
        "items": invoice_items,
        "gross_amount": round(gross_amount, 2),
    }
    if extracted.get("doc_date"):
        sap_payload["document_date"] = extracted["doc_date"]
        # SAP requires a posting date; default it to the document date.
        sap_payload["posting_date"] = extracted["doc_date"]
    if extracted.get("doc_number"):
        sap_payload["invoice_id"] = str(extracted["doc_number"])

    # Tax line — emit a single tax_items entry when the document carried tax and we
    # have a vendor-level tax code (left to the user/controller to fill the code).
    if tax_total > 0:
        sap_payload["tax_items"] = [{"item": 1, "tax_code": None, "amount": round(tax_total, 2)}]
        # SAP rejects a tax line with a null TaxCode; force review so the user
        # supplies it before the controller posts.
        pending_actions.append({"type": "tax_code", "reason": "missing", "amount": round(tax_total, 2)})

    # _meta carries display names; the controller strips it before POSTing the doc
    # to SAP and uses it to record vendor->account / item->account history.
    meta: Dict[str, Any] = {"line_items": meta_lines}

    if vendor_match.get("list_id"):
        sap_payload["invoicing_party"] = vendor_match["list_id"]
        meta["vendor_id"] = vendor_match["list_id"]
        meta["vendor_name"] = vendor_match.get("name")
    elif extracted.get("vendor_or_customer"):
        # SAP requires an invoicing party (BP id); UI flags this and offers
        # create-vendor (sap_connector.create_vendor).
        meta["vendor_name"] = extracted["vendor_or_customer"]
        pending_actions.append({
            "type": "vendor",
            "name": extracted["vendor_or_customer"],
            "reason": "no_match",
        })

    sap_payload["_meta"] = meta

    duplicate_check = await check_bill(
        user_id=user_id,
        company_code=company_code,
        doc_date=extracted.get("doc_date"),
        total_amount=extracted_total,
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
        "sap_payload": sap_payload,
        "extracted_total": extracted_total,
        "needs_review": needs_review,
        "pending_actions": pending_actions,
        "duplicate_check": duplicate_check,
    }
