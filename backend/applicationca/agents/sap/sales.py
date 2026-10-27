"""
SAP S/4HANA sales pipeline — A/R customer invoice.

Template: applicationca/agents/zoho/sales.py (and agents/xero/sales.py). The
extract -> match -> build -> review skeleton is identical; the BUILD step differs
because SAP FI has no invoice object reachable from this flow — a customer (A/R)
invoice is posted as a *journal entry* (doc_type 'DR') via the SOAP A2X service:

    Dr  Customer / A-R   (debtor_item,  dc='S', gross_total)
    Cr  Revenue G/L      (gl_item,      dc='H', net per revenue account)
    Cr  Output Tax G/L   (gl_item,      dc='H', tax_total)   -- only when a tax
                                                                G/L account resolves

The journal entry must balance (sum of S == sum of H). SAP also has no item /
product master in the FI posting flow, so where the Zoho/Xero pipelines matched
line items against an Item master, here we match them against revenue **GL
accounts** (account_cards from list_accounts) — the GL accounts ARE the line
ledgers.

This pipeline does NOT post. It returns a payload (sap_doc + _meta +
extracted_total) for the controller to review and then hand to
sap_connector.post_journal_entry. `_meta` carries display names (customer_name /
account_name) for history / dedup; the controller strips it before posting.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from applicationca.services.sap_aliases import get_aliases
from applicationca.services.sap_connector import sap_connector
from applicationca.services.qbo_doc_extractor import extract_invoice
from applicationca.services.qbo_matcher import match_entity, match_many
from applicationca.services.sap_duplicate_check import check_invoice

from applicationca.agents.qbo._llm import make_llm_async

logger = logging.getLogger(__name__)

# SAP GLAccountType / name hints that flag a P&L revenue account. GLAccountType
# "N" = non-operating expense/revenue, "P" = primary costs / revenue (P&L). We
# also fall back to name hints because tenants vary. (VERIFY GLAccountType codes
# against your tenant's value help.)
_REVENUE_GL_TYPES = ("N", "P", "X")  # X kept as a loose P&L-ish fallback
_REVENUE_NAME_HINTS = ("revenue", "sales", "income", "turnover")
_TAX_NAME_HINTS = ("output tax", "tax payable", "gst", "vat", "tax")


def _is_revenue_account(a: Dict[str, Any]) -> bool:
    """Heuristic: a P&L (non balance-sheet) account whose name/type smells like
    revenue. SAP has no clean "Revenue" account-type flag like Zoho/Xero, so we
    combine the IsBalanceSheetAccount flag with name hints."""
    name = (a.get("name") or "").lower()
    if any(h in name for h in _REVENUE_NAME_HINTS):
        return True
    is_bs = a.get("is_balance_sheet")
    if is_bs in (True, "true", "X", "x"):
        return False
    return (a.get("account_type") or "").upper() in _REVENUE_GL_TYPES


def _pick_revenue_account(accounts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fallback revenue GL for lines that don't match a specific account."""
    for a in accounts:
        if any(h in (a.get("name") or "").lower() for h in _REVENUE_NAME_HINTS):
            return a
    for a in accounts:
        if _is_revenue_account(a):
            return a
    return accounts[0] if accounts else None


def _pick_tax_account(accounts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Best-effort output-tax GL account by name hint. When none is found we fold
    the tax into the revenue side so the journal entry still balances."""
    for hint in _TAX_NAME_HINTS:
        for a in accounts:
            if hint in (a.get("name") or "").lower():
                return a
    return None


def _coerce_amount(li: Dict[str, Any]) -> float:
    """Net line amount (qty * rate fallback), mirroring the Zoho/Xero pipelines."""
    amount = li.get("amount")
    qty = li.get("qty") or 1
    rate = li.get("rate")
    if amount is None:
        try:
            amount = float(qty) * float(rate or 0)
        except Exception:
            amount = 0.0
    try:
        return float(amount or 0)
    except Exception:
        return 0.0


async def process_sales_pdf(
    pdf_bytes: bytes,
    *,
    user_id: str,
    company_code: str,
) -> Dict[str, Any]:
    extracted = await extract_invoice(pdf_bytes)
    if "error" in extracted:
        return {"ok": False, "stage": "extract", **extracted}

    customer_cards = await sap_connector.list_customers(user_id, company_code)
    all_accounts = await sap_connector.list_accounts(user_id, company_code)
    revenue_account_cards = [a for a in all_accounts if _is_revenue_account(a)] or all_accounts

    llm_async = make_llm_async()

    customer_aliases = get_aliases(user_id=user_id, company_code=company_code or "", entity_type="customer")
    account_aliases = get_aliases(user_id=user_id, company_code=company_code or "", entity_type="account")

    cust_match = await match_entity(
        extracted.get("vendor_or_customer", ""),
        extracted.get("vendor_or_customer_email"),
        customer_cards,
        entity_label="customer",
        llm_async=llm_async,
        aliases=customer_aliases,
    )

    # SAP has no item master — match each line against a REVENUE GL account.
    line_input = [
        {"name": li.get("description", ""), "email": None}
        for li in extracted.get("line_items", [])
    ]
    line_matches = await match_many(
        line_input, revenue_account_cards, entity_label="account",
        llm_async=llm_async, aliases=account_aliases,
    )

    fallback_revenue = _pick_revenue_account(revenue_account_cards)

    # Aggregate the credit (revenue) side per matched GL account.
    revenue_by_account: Dict[str, Dict[str, Any]] = {}
    review_rows: List[Dict[str, Any]] = []
    pending_actions: List[Dict[str, Any]] = []
    net_total = 0.0

    for li, lm in zip(extracted.get("line_items", []), line_matches):
        amount = _coerce_amount(li)
        net_total += amount

        gl_id = lm.get("list_id") or (fallback_revenue.get("list_id") if fallback_revenue else None)
        gl_name = lm.get("name") or (fallback_revenue.get("name") if fallback_revenue else None)
        if not lm.get("list_id"):
            pending_actions.append({
                "type": "account",
                "name": li.get("description") or lm.get("candidate_name") or (gl_name or "Revenue"),
                "reason": "no_match" if fallback_revenue else "no_match_no_fallback",
            })

        if gl_id:
            bucket = revenue_by_account.setdefault(gl_id, {"gl_account": gl_id, "name": gl_name, "amount": 0.0})
            bucket["amount"] += amount

        review_rows.append({
            "description": li.get("description"),
            "qty": li.get("qty"),
            "rate": li.get("rate"),
            "amount": amount,
            "tax_amount": li.get("tax_amount"),
            "match": lm,
            "gl_account": gl_id,
            "gl_account_name": gl_name,
        })

    # Tax: sum extracted per-line tax + document tax_total (prefer the explicit
    # document total when present, else the per-line sum).
    line_tax_sum = 0.0
    for li in extracted.get("line_items", []):
        try:
            line_tax_sum += float(li.get("tax_amount") or 0)
        except Exception:
            pass
    try:
        doc_tax = float(extracted.get("tax_total") or 0)
    except Exception:
        doc_tax = 0.0
    tax_total = doc_tax if doc_tax else line_tax_sum

    # Gross (debit) side: prefer the extractor's document total; else net + tax.
    try:
        extracted_total = float(extracted.get("total")) if extracted.get("total") is not None else None
    except Exception:
        extracted_total = None
    gross_total = extracted_total if extracted_total is not None else round(net_total + tax_total, 2)

    currency = extracted.get("currency") or "INR"
    doc_date = (extracted.get("doc_date") or "")[:10] or None
    reference = str(extracted.get("doc_number")) if extracted.get("doc_number") else None

    # ---- BUILD the journal entry (doc_type 'DR': customer invoice) ----
    tax_account = _pick_tax_account(all_accounts) if tax_total else None

    gl_items: List[Dict[str, Any]] = []
    account_names: Dict[str, str] = {}
    revenue_credit_total = 0.0

    # When there is tax but no tax GL account, fold the tax into the revenue
    # credit so the entry still balances (Cr revenue = net + tax).
    fold_tax_into_revenue = bool(tax_total) and tax_account is None
    revenue_scale = 1.0
    if fold_tax_into_revenue and net_total:
        revenue_scale = (net_total + tax_total) / net_total

    for gl_id, bucket in revenue_by_account.items():
        credit_amt = round(bucket["amount"] * revenue_scale, 2)
        if credit_amt == 0:
            continue
        revenue_credit_total += credit_amt
        if bucket.get("name"):
            account_names[gl_id] = bucket["name"]
        gl_items.append({
            "gl_account": gl_id,
            "amount": credit_amt,
            "dc": "H",  # credit revenue
            "text": (bucket.get("name") or "Revenue")[:50],
        })

    if tax_account is not None and tax_total:
        gl_items.append({
            "gl_account": tax_account["list_id"],
            "amount": round(tax_total, 2),
            "dc": "H",  # credit output tax
            "text": (tax_account.get("name") or "Output Tax")[:50],
        })
        account_names[tax_account["list_id"]] = tax_account.get("name") or "Output Tax"
        revenue_credit_total += round(tax_total, 2)

    # ---- Debtor (customer / A-R) debit side ----
    customer_id = cust_match.get("list_id")
    customer_name = cust_match.get("name") or extracted.get("vendor_or_customer")
    if not customer_id and extracted.get("vendor_or_customer"):
        pending_actions.append({
            "type": "customer",
            "name": extracted["vendor_or_customer"],
            "reason": "no_match",
        })

    # Balance guard: the debtor debit equals the sum of credits actually built,
    # so the entry always balances even if rounding or the tax-fold shifted cents.
    debtor_amount = round(revenue_credit_total, 2) if revenue_credit_total else round(gross_total, 2)

    debtor_items: List[Dict[str, Any]] = []
    if customer_id:
        debtor_items.append({
            "customer": customer_id,
            "amount": debtor_amount,
            "dc": "S",  # debit A/R
            "text": (customer_name or "Customer")[:50],
        })

    sap_doc: Dict[str, Any] = {
        "document_date": doc_date,
        "posting_date": doc_date,
        "doc_type": "DR",
        "currency": currency,
        "header_text": (customer_name or "Sales Invoice")[:25] if customer_name else "Sales Invoice",
        "gl_items": gl_items,
        "debtor_items": debtor_items,
        "creditor_items": [],
    }
    if reference:
        sap_doc["reference"] = reference

    # ---- duplicate check (local + SAP open A/R items) ----
    duplicate_check = await check_invoice(
        user_id=user_id,
        company_code=company_code,
        doc_date=extracted.get("doc_date"),
        total_amount=extracted.get("total") if extracted.get("total") is not None else gross_total,
        doc_number=extracted.get("doc_number"),
        customer_name=customer_name,
    )

    balanced = round(sum(i["amount"] for i in debtor_items), 2) == round(
        sum(i["amount"] for i in gl_items), 2
    )

    needs_review = (
        not cust_match.get("matched")
        or not customer_id
        or any(not m.get("matched") for m in line_matches)
        or bool(pending_actions)
        or not balanced
        or not gl_items
        or not doc_date
        or duplicate_check.get("is_duplicate", False)
    )

    # Nest _meta inside the payload so the push contract matches the purchase
    # pipeline and the controller (push_invoice reads body.sap_payload, pulls
    # _meta for history, then strips it before posting).
    sap_doc["_meta"] = {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "account_names": account_names,
        "tax_total": round(tax_total, 2),
        "net_total": round(net_total, 2),
        "balanced": balanced,
    }
    return {
        "ok": True,
        "extracted": extracted,
        "customer_match": cust_match,
        "line_review": review_rows,
        "sap_payload": sap_doc,
        "extracted_total": gross_total,
        "needs_review": needs_review,
        "pending_actions": pending_actions,
        "duplicate_check": duplicate_check,
    }
