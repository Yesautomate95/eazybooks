"""
SAP S/4HANA Cloud bank-entries pipeline.

Bank-entries is the write-side analog of bank reconciliation: parse a statement
and create one SAP journal entry per row. Use this when the statement's
transactions are NOT already in SAP and you want to bootstrap them.

Template: applicationca/agents/zoho/bank_entries.py. Differences vs Zoho:
  * Company key is ``company_code`` (SAP Company Code), not organization_id.
  * SAP FI has no flat "bank transaction" object (no Zoho transaction_type /
    account_id deposit/expense). Every bank line posts a GL **journal entry**
    via ``sap_connector.post_journal_entry`` (doc_type 'SA', a generic GL
    posting). The double-entry is built explicitly:
        money in  (statement credit) -> debit bank GL (S) / credit income GL (H)
                                          (or credit the customer's A/R via a
                                          debtor_item when only a customer matched)
        money out (statement debit)  -> debit expense GL (S) / credit bank GL (H)
    Debits (S) must equal credits (H); amounts are positive; DebitCreditCode
    carries the sign.
  * GL accounts ARE the line ledgers (there is no item master). Where the Zoho
    pipeline matched counter-accounts, we match GL accounts (account_cards from
    list_accounts).
  * Display names (customer_name / vendor_name / account_name) plus the raw
    ``bank_description`` ride along on a ``_meta`` block AND inline so
    history / dedupe can record names; the controller strips ``_meta`` before
    posting and the connector only reads the JE ``doc``.

Each row produces a draft ``sap_payload`` (a post_journal_entry doc). The
frontend lets the user edit counterparty / account before pushing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from applicationca.agents.document_specific_functions.bank_reconciliation import (
    parse_bank_statement_for_recon,
)
from applicationca.services import sap_bank
from applicationca.services.sap_aliases import get_aliases
from applicationca.services.sap_connector import sap_connector
from applicationca.services.sap_suggestion_service import (
    record_from_bank_entry_payload,
    suggest_for_bank_entry,
)
from applicationca.services.sap_duplicate_check import (
    check_bank_entries_batch,
    record_pushed as record_bank_pushed,
    DOC_BANK,
)

from applicationca.agents.qbo._llm import make_llm_async

logger = logging.getLogger(__name__)


# SAP chart-of-accounts GLAccountType codes that are valid targets for each side
# of a bank entry. A statement credit (money in) books to a revenue account; a
# statement debit (money out) books to an expense account. The connector card's
# ``account_type`` carries the GLAccountType code (e.g. P = P&L, X = balance
# sheet) — there is no income/expense enum, so we fall back to name hints and,
# failing that, to the whole list (the CA edits before pushing).
_INCOME_TYPES = ("p", "pl", "revenue", "income")
_EXPENSE_TYPES = ("p", "pl", "expense", "cost")
_DEFAULT_INCOME_HINTS = ("sales", "revenue", "service", "other income", "income")
_DEFAULT_EXPENSE_HINTS = ("misc", "office", "other expense", "expense", "purchase")

# SAP journal-entry accounting document type for a generic GL bank posting.
_JE_DOC_TYPE = "SA"
# DebitCreditCode: S = debit, H = credit (positive amounts; the code carries sign).
_DC_DEBIT = "S"
_DC_CREDIT = "H"


def _pick_default(accounts: List[Dict[str, Any]], hints: tuple) -> Optional[Dict[str, Any]]:
    for hint in hints:
        for a in accounts:
            if hint in (a.get("name") or "").lower():
                return a
    return accounts[0] if accounts else None


def _row_date_iso(d: Any) -> Optional[str]:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, str) and d:
        return d[:10]
    return None


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": _row_date_iso(row.get("date")),
        "description": row.get("description"),
        "reference": row.get("reference"),
        "debit_amount": float(row["debit_amount"]) if row.get("debit_amount") else None,
        "credit_amount": float(row["credit_amount"]) if row.get("credit_amount") else None,
    }


async def process_bank_statement_for_entries(
    file_content: bytes,
    file_name: str,
    *,
    user_id: str,
    company_code: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse statement, classify each row as money-in/money-out, match
    counterparty + counter GL account, and build draft SAP journal-entry
    payloads (one ``post_journal_entry`` doc per row)."""
    parsed = parse_bank_statement_for_recon(file_content, file_name)
    rows = parsed.get("transactions") or []
    if not rows:
        return {"ok": False, "stage": "extract", "error": "Could not extract any transactions"}

    # sap_connector returns normalized cards directly — no re-normalization.
    customer_cards = await sap_connector.list_customers(user_id, company_code)
    vendor_cards = await sap_connector.list_vendors(user_id, company_code)
    all_accounts = await sap_connector.list_accounts(user_id, company_code)

    income_accts = [
        a for a in all_accounts if (a.get("account_type") or "").lower() in _INCOME_TYPES
    ] or all_accounts
    expense_accts = [
        a for a in all_accounts if (a.get("account_type") or "").lower() in _EXPENSE_TYPES
    ] or all_accounts

    fallback_income = _pick_default(income_accts, _DEFAULT_INCOME_HINTS)
    fallback_expense = _pick_default(expense_accts, _DEFAULT_EXPENSE_HINTS)

    # Resolve the bank GL account name for display (the JE counter-leg).
    bank_name = bank_account_name
    if not bank_name:
        try:
            bank_accts = await sap_bank.list_bank_accounts(user_id, company_code)
            for b in bank_accts:
                if str(b.get("id")) == str(bank_account_id):
                    bank_name = b.get("name")
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"bank account name lookup failed: {exc}")

    llm_async = make_llm_async()

    # Tier-4 alias maps. Bank entries touch all four entity types — load each once
    # and thread them through every per-row suggestion call so alias hits
    # short-circuit fuzzy / history lookups.
    customer_aliases = get_aliases(user_id=user_id, company_code=company_code or "", entity_type="customer")
    vendor_aliases = get_aliases(user_id=user_id, company_code=company_code or "", entity_type="vendor")
    account_aliases = get_aliases(user_id=user_id, company_code=company_code or "", entity_type="account")

    # Resolve suggestions for all rows up front: dedupe identical
    # (direction, description) pairs and run the unique ones concurrently.
    # Each suggestion can cost one or more LLM round-trips, so awaiting them
    # one row at a time dominated processing time on long statements.
    _suggest_sem = asyncio.Semaphore(10)

    async def _suggest(desc: str, is_credit: bool) -> Dict[str, Any]:
        async with _suggest_sem:
            if is_credit:
                return await suggest_for_bank_entry(
                    user_id=user_id,
                    company_code=company_code,
                    description=desc,
                    counterparty_cards=customer_cards,
                    account_cards=income_accts,
                    counterparty_label="customer",
                    account_label="income account",
                    llm_async=llm_async,
                    counterparty_aliases=customer_aliases,
                    account_aliases=account_aliases,
                )
            return await suggest_for_bank_entry(
                user_id=user_id,
                company_code=company_code,
                description=desc,
                counterparty_cards=vendor_cards,
                account_cards=expense_accts,
                counterparty_label="vendor",
                account_label="expense account",
                llm_async=llm_async,
                counterparty_aliases=vendor_aliases,
                account_aliases=account_aliases,
            )

    suggestion_keys = []
    for row in rows:
        desc = row.get("description") or ""
        if row.get("credit_amount"):
            key = (True, desc)
        elif row.get("debit_amount"):
            key = (False, desc)
        else:
            continue
        if key not in suggestion_keys:
            suggestion_keys.append(key)
    suggestion_results = await asyncio.gather(
        *(_suggest(d, c) for c, d in suggestion_keys)
    )
    suggestions = dict(zip(suggestion_keys, suggestion_results))

    entries: List[Dict[str, Any]] = []
    for row in rows:
        amt_iso_date = _row_date_iso(row.get("date"))
        desc = row.get("description") or ""
        reference = row.get("reference") or None

        if row.get("credit_amount"):
            # Money IN: debit the bank GL, credit revenue (or the customer A/R).
            suggestion = suggestions[(True, desc)]
            cust_m = suggestion["counterparty_match"]
            acct_m = suggestion["account_match"]
            amount = float(row.get("credit_amount") or 0)
            account = acct_m if acct_m.get("matched") else (fallback_income or {})

            doc: Dict[str, Any] = {
                "document_date": amt_iso_date,
                "posting_date": amt_iso_date,
                "doc_type": _JE_DOC_TYPE,
                "currency": "INR",
                "header_text": (desc or "")[:50] or None,
                "reference": reference,
                # Debit the bank GL (money in).
                "gl_items": [{
                    "gl_account": bank_account_id,
                    "amount": amount,
                    "dc": _DC_DEBIT,
                    "text": (desc or None),
                }],
                "debtor_items": [],
                "creditor_items": [],
            }
            # Credit leg: prefer a revenue GL; if none but a customer matched,
            # credit the customer's A/R via a debtor item (dc=H).
            if account.get("list_id"):
                doc["gl_items"].append({
                    "gl_account": account.get("list_id"),
                    "amount": amount,
                    "dc": _DC_CREDIT,
                    "text": (desc or None),
                })
            elif cust_m.get("matched"):
                doc["debtor_items"].append({
                    "customer": cust_m.get("list_id"),
                    "amount": amount,
                    "dc": _DC_CREDIT,
                    "text": (desc or None),
                })

            # Display-only context for history + UI (controller strips _meta;
            # the connector only reads the JE doc fields above).
            meta: Dict[str, Any] = {
                "bank_account_id": bank_account_id,
                "bank_account_name": bank_name,
                "account_name": account.get("name"),
                "bank_description": desc,
            }
            if cust_m.get("matched"):
                meta["customer_id"] = cust_m.get("list_id")
                meta["customer_name"] = cust_m.get("name")
            doc["_meta"] = meta

            entries.append({
                "type": "deposit",
                "row": _serialize_row(row),
                "amount": amount,
                "counterparty_match": cust_m,
                "account_match": acct_m,
                "sap_payload": doc,
                "duplicate_check": None,
                "_dup_input": {
                    "date_iso": amt_iso_date, "amount": amount,
                    "is_credit": True, "description": desc,
                },
            })
        elif row.get("debit_amount"):
            # Money OUT: debit the expense GL, credit the bank GL.
            suggestion = suggestions[(False, desc)]
            vend_m = suggestion["counterparty_match"]
            acct_m = suggestion["account_match"]
            amount = float(row.get("debit_amount") or 0)
            account = acct_m if acct_m.get("matched") else (fallback_expense or {})

            gl_items: List[Dict[str, Any]] = []
            if account.get("list_id"):
                gl_items.append({
                    "gl_account": account.get("list_id"),
                    "amount": amount,
                    "dc": _DC_DEBIT,
                    "text": (desc or None),
                })
            # Credit the bank GL (money out).
            gl_items.append({
                "gl_account": bank_account_id,
                "amount": amount,
                "dc": _DC_CREDIT,
                "text": (desc or None),
            })

            doc = {
                "document_date": amt_iso_date,
                "posting_date": amt_iso_date,
                "doc_type": _JE_DOC_TYPE,
                "currency": "INR",
                "header_text": (desc or "")[:50] or None,
                "reference": reference,
                "gl_items": gl_items,
                "debtor_items": [],
                "creditor_items": [],
            }

            meta = {
                "bank_account_id": bank_account_id,
                "bank_account_name": bank_name,
                "account_name": account.get("name"),
                "bank_description": desc,
            }
            if vend_m.get("matched"):
                meta["vendor_id"] = vend_m.get("list_id")
                meta["vendor_name"] = vend_m.get("name")
            doc["_meta"] = meta

            entries.append({
                "type": "expense",
                "row": _serialize_row(row),
                "amount": amount,
                "counterparty_match": vend_m,
                "account_match": acct_m,
                "sap_payload": doc,
                "duplicate_check": None,
                "_dup_input": {
                    "date_iso": amt_iso_date, "amount": amount,
                    "is_credit": False, "description": desc,
                },
            })

    # Batch dedupe the whole statement (one SAP open-item pull + one local query).
    dup_inputs = [e.pop("_dup_input") for e in entries]
    dup_results = await check_bank_entries_batch(
        user_id=user_id, company_code=company_code, rows=dup_inputs,
    )
    for e, dup in zip(entries, dup_results):
        e["duplicate_check"] = dup

    needs_review = any(
        not (e["counterparty_match"].get("matched") or False)
        or not (e["account_match"].get("matched") or False)
        for e in entries
    )

    return {
        "ok": True,
        "bank_account_id": bank_account_id,
        "bank_account_name": bank_name,
        "entries": entries,
        "needs_review": needs_review,
        "row_count": len(rows),
    }


def _history_payload_from_meta(payload: Dict[str, Any], entry_type: str) -> Dict[str, Any]:
    """Build the inline-keyed payload shape that sap_suggestion_service.
    record_from_bank_entry_payload reads (vendor_name / customer_name top-level,
    line_items[].account_name, bank_description) out of the JE doc's _meta block
    and gl_items. The connector never sees this — it's only fed to history."""
    meta = (payload or {}).get("_meta") or {}
    # The counter (non-bank) GL leg carries the matched account id/name.
    bank_id = str(meta.get("bank_account_id")) if meta.get("bank_account_id") is not None else None
    account_id = None
    for it in (payload or {}).get("gl_items") or []:
        if str(it.get("gl_account")) != bank_id:
            account_id = it.get("gl_account")
            break
    out: Dict[str, Any] = {
        "bank_description": meta.get("bank_description"),
        "line_items": [{
            "account_id": account_id,
            "account_name": meta.get("account_name"),
            "description": meta.get("bank_description"),
        }],
    }
    if entry_type == "expense":
        out["vendor_id"] = meta.get("vendor_id")
        out["vendor_name"] = meta.get("vendor_name")
    else:
        out["contact_id"] = meta.get("customer_id")
        out["contact_name"] = meta.get("customer_name")
    return out


async def push_one_entry(
    *,
    user_id: str,
    company_code: Optional[str],
    entry_type: str,
    payload: Dict[str, Any],
    extracted_total: Optional[float] = None,
) -> Dict[str, Any]:
    """Push one bank entry as a SAP journal entry via post_journal_entry.

    `entry_type` is 'deposit' (money in) or 'expense' (money out); both post a
    generic GL journal entry (doc_type 'SA') — SAP FI has no separate bank-txn
    object. `payload` is the JE doc built by process_bank_statement_for_entries,
    carrying a display-only ``_meta`` block which we strip before posting.

    After a successful post we record entity/account + narration history via
    sap_suggestion_service and record the push for next-time dedupe.
    """
    if not company_code:
        return {"type": entry_type, "ok": False, "error": "company_code is required to post a journal entry"}
    if entry_type not in ("deposit", "expense"):
        return {"type": entry_type, "ok": False, "error": f"Unknown entry type {entry_type!r}"}

    meta = (payload or {}).get("_meta") or {}
    # Strip display-only _meta — the connector reads only the JE doc fields.
    doc = {k: v for k, v in (payload or {}).items() if k != "_meta"}

    try:
        created = await sap_connector.post_journal_entry(user_id, doc, company_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"post_journal_entry failed: {exc}")
        return {"type": entry_type, "ok": False, "error": str(exc)}

    # Learn entity/account + narration mappings from the (un-stripped) payload.
    record_from_bank_entry_payload(
        user_id=user_id, company_code=company_code,
        entry_type=entry_type, payload=_history_payload_from_meta(payload, entry_type),
    )

    party_name = (
        meta.get("vendor_name")
        or meta.get("customer_name")
        or meta.get("bank_description")
    )
    line_amount = None
    for it in doc.get("gl_items") or []:
        if it.get("amount"):
            line_amount = it.get("amount")
            break
    entity_id = (created or {}).get("accounting_document") if isinstance(created, dict) else None
    record_bank_pushed(
        user_id=user_id, company_code=company_code, doc_type=DOC_BANK,
        date_iso=doc.get("posting_date") or doc.get("document_date"),
        amount=extracted_total if extracted_total is not None else line_amount,
        doc_number=doc.get("reference"),
        party_name=party_name,
        sap_entity_id=str(entity_id) if entity_id is not None else None,
    )
    return {"type": entry_type, "ok": True, "result": created}
