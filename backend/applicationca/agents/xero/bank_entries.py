"""
Xero bank-entries pipeline.

Bank-entries is the write-side analog of bank reconciliation: parse a statement
and create one Xero BankTransaction per row (a ``RECEIVE`` for inflows, a
``SPEND`` for outflows). Use this when the statement's transactions are NOT
already in Xero and you want to bootstrap them.

Mirrors agents/zoho/bank_entries.py. Differences vs Zoho:
  * Company key is ``tenant_id`` (the active Xero organisation, sent as the
    Xero-tenant-id header — Xero is cloud-OAuth only).
  * xero_connector list_* methods already return normalized cards
    ({list_id, code, name, account_type, ...}), so we consume them directly.
  * Xero BankTransactions are PascalCase: a ``Type`` (SPEND/RECEIVE), a
    ``BankAccount.AccountID`` (the bank account), a ``Contact.ContactID``
    (the counterparty), and one or more ``LineItems`` whose counter-account is
    referenced by ``AccountCode`` (the string Code, NOT the AccountID). After
    matching we look the matched account card up by list_id to read its `code`,
    and carry the AccountID + name inline under `_account_id` / `_account_name`
    for history. Top-level vendor_name/customer_name + the raw bank_description
    also ride inline for the UI + history; the connector strips the
    underscore-prefixed keys (plus vendor_name/customer_name/bank_description)
    before POSTing.

Each row produces a draft ``xero_payload``. The frontend lets the user edit
counterparty / account before pushing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from applicationca.agents.document_specific_functions.bank_reconciliation import (
    parse_bank_statement_for_recon,
)
from applicationca.agents.xero.bank_payload import (
    build_xero_bank_payload,
    BankVoucherError,
)
from applicationca.services.xero_aliases import get_aliases
from applicationca.services.xero_connector import xero_connector
from applicationca.services.xero_suggestion_service import (
    record_from_bank_entry_payload,
    suggest_for_bank_entry,
)
from applicationca.services.xero_duplicate_check import (
    check_bank_entries_batch,
    record_pushed as record_bank_pushed,
    DOC_BANK,
)

from ._llm import make_llm_async

logger = logging.getLogger(__name__)


# Xero chart-of-accounts Types (uppercase) that are valid targets for each side
# of a bank entry. Credits (money in) book to income; debits (money out) book to
# an expense account.
# VERIFY: Xero account Type enum values for income vs expense.
_INCOME_TYPES = ("REVENUE", "SALES", "OTHERINCOME")
_EXPENSE_TYPES = ("EXPENSE", "DIRECTCOSTS", "OVERHEADS", "CURRLIAB")
_DEFAULT_INCOME_HINTS = ("sales", "services", "other income", "uncategor")
_DEFAULT_EXPENSE_HINTS = ("office supplies", "miscellaneous", "other expense", "uncategor")

# Xero BankTransaction Type enum. RECEIVE = money in (credits/deposits),
# SPEND = money out (debits/expenses).
# VERIFY BankTransaction field names + Type enum values.
_TXN_TYPE_RECEIVE = "RECEIVE"
_TXN_TYPE_SPEND = "SPEND"


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


def _normalized_voucher(
    *,
    voucher_type: str,
    date_iso: Optional[str],
    description: str,
    amount: float,
    account: Optional[Dict[str, Any]],
    counterparty_match: Dict[str, Any],
    counterparty_type: str,
    bank_account_id: str,
    bank_account_name: Optional[str],
    bank_account_code: Optional[str] = None,
    reference: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the ERP-agnostic normalized bank voucher (the same contract Tally
    and QBO use) from a matched statement row. ``party_ledger`` is the Xero
    income/expense account; ``party_ledger_code`` is its Xero AccountCode (Xero
    line items reference accounts by Code, not GUID). ``counterparty`` is the
    silently-matched Customer/Vendor (or None) — it is never required and only
    attaches when the matcher was confident."""
    cp = None
    if counterparty_match.get("matched") and counterparty_match.get("list_id"):
        cp = {
            "id": str(counterparty_match["list_id"]),
            "name": counterparty_match.get("name"),
            "type": counterparty_type,
        }
    return {
        "date": date_iso,
        "narration": description or "",
        "amount": amount,
        "voucher_type": voucher_type,
        "party_ledger": account.get("name") if account else None,
        "party_ledger_id": str(account.get("list_id")) if account and account.get("list_id") else None,
        "party_ledger_code": account.get("code") if account else None,
        "bank_ledger": bank_account_name,
        "bank_ledger_id": str(bank_account_id) if bank_account_id else None,
        "bank_ledger_code": bank_account_code,
        "reference": reference,
        "counterparty": cp,
    }


async def process_bank_statement_for_entries(
    file_content: bytes,
    file_name: str,
    *,
    user_id: str,
    tenant_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse statement, classify each row as RECEIVE/SPEND, match counterparty
    + counter-account, and build draft Xero BankTransaction payloads."""
    parsed = parse_bank_statement_for_recon(file_content, file_name)
    rows = parsed.get("transactions") or []
    if not rows:
        return {"ok": False, "stage": "extract", "error": "Could not extract any transactions"}

    # xero_connector returns normalized cards directly — no re-normalization.
    customer_cards = await xero_connector.list_customers(user_id, tenant_id)
    vendor_cards = await xero_connector.list_vendors(user_id, tenant_id)
    all_accounts = await xero_connector.list_accounts(user_id, tenant_id)

    income_accts = [
        a for a in all_accounts if (a.get("account_type") or "").upper() in _INCOME_TYPES
    ] or all_accounts
    expense_accts = [
        a for a in all_accounts if (a.get("account_type") or "").upper() in _EXPENSE_TYPES
    ] or all_accounts

    # Look matched account cards up by list_id to read their `code` — Xero line
    # items reference accounts by AccountCode, not AccountID.
    income_by_id = {a["list_id"]: a for a in income_accts if a.get("list_id")}
    expense_by_id = {a["list_id"]: a for a in expense_accts if a.get("list_id")}

    # The chosen bank account's Code — Journal/Contra vouchers reference it.
    bank_card = next((a for a in all_accounts if str(a.get("list_id")) == str(bank_account_id)), None)
    bank_account_code = bank_card.get("code") if bank_card else None

    fallback_income = _pick_default(income_accts, _DEFAULT_INCOME_HINTS)
    fallback_expense = _pick_default(expense_accts, _DEFAULT_EXPENSE_HINTS)

    llm_async = make_llm_async()

    # Tier-4 alias maps. Bank entries touch all four entity types — load each once
    # and thread them through every per-row suggestion call so alias hits
    # short-circuit fuzzy / history lookups.
    customer_aliases = get_aliases(user_id=user_id, tenant_id=tenant_id or "", entity_type="customer")
    vendor_aliases = get_aliases(user_id=user_id, tenant_id=tenant_id or "", entity_type="vendor")
    account_aliases = get_aliases(user_id=user_id, tenant_id=tenant_id or "", entity_type="account")

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
                    tenant_id=tenant_id,
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
                tenant_id=tenant_id,
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

        if row.get("credit_amount"):
            suggestion = suggestions[(True, desc)]
            cust_m = suggestion["counterparty_match"]
            acct_m = suggestion["account_match"]
            amount = float(row.get("credit_amount") or 0)
            matched_acct = income_by_id.get(acct_m.get("list_id")) if acct_m.get("matched") else None
            account = matched_acct or (fallback_income or {})

            line: Dict[str, Any] = {
                "Description": desc or None,
                "UnitAmount": amount,
                "Quantity": 1,
            }
            if account.get("code"):
                line["AccountCode"] = account.get("code")  # Xero refs accounts by Code
                line["_account_id"] = account.get("list_id")  # AccountID; display-only, for history
                line["_account_name"] = account.get("name")  # display-only; stripped before POST

            payload: Dict[str, Any] = {
                "Type": _TXN_TYPE_RECEIVE,
                "BankAccount": {"AccountID": bank_account_id},  # bank account money lands in
                "bank_description": desc,  # raw narration; display-only, kept for history
                "LineItems": [line],
            }
            if amt_iso_date:
                payload["Date"] = amt_iso_date
            if cust_m.get("matched"):
                # Contact ref; carry display name inline for UI + history.
                payload["Contact"] = {"ContactID": cust_m.get("list_id")}
                payload["customer_name"] = cust_m.get("name")

            entries.append({
                "type": "deposit",
                "row": _serialize_row(row),
                "amount": amount,
                "counterparty_match": cust_m,
                "account_match": acct_m,
                "xero_payload": payload,
                "voucher": _normalized_voucher(
                    voucher_type="Receipt",
                    date_iso=amt_iso_date,
                    description=desc,
                    amount=amount,
                    account=account,
                    counterparty_match=cust_m,
                    counterparty_type="Customer",
                    bank_account_id=bank_account_id,
                    bank_account_name=bank_account_name,
                    bank_account_code=bank_account_code,
                    reference=row.get("reference"),
                ),
                "duplicate_check": None,
                "_dup_input": {
                    "date_iso": amt_iso_date, "amount": amount,
                    "is_credit": True, "description": desc,
                },
            })
        elif row.get("debit_amount"):
            suggestion = suggestions[(False, desc)]
            vend_m = suggestion["counterparty_match"]
            acct_m = suggestion["account_match"]
            amount = float(row.get("debit_amount") or 0)
            matched_acct = expense_by_id.get(acct_m.get("list_id")) if acct_m.get("matched") else None
            account = matched_acct or (fallback_expense or {})

            line = {
                "Description": desc or None,
                "UnitAmount": amount,
                "Quantity": 1,
            }
            if account.get("code"):
                line["AccountCode"] = account.get("code")  # Xero refs accounts by Code
                line["_account_id"] = account.get("list_id")  # AccountID; display-only, for history
                line["_account_name"] = account.get("name")  # display-only; stripped before POST

            payload = {
                "Type": _TXN_TYPE_SPEND,
                "BankAccount": {"AccountID": bank_account_id},  # bank account money leaves from
                "bank_description": desc,  # raw narration; display-only, kept for history
                "LineItems": [line],
            }
            if amt_iso_date:
                payload["Date"] = amt_iso_date
            if vend_m.get("matched"):
                # Contact ref; carry display name inline for UI + history.
                payload["Contact"] = {"ContactID": vend_m.get("list_id")}
                payload["vendor_name"] = vend_m.get("name")

            entries.append({
                "type": "expense",
                "row": _serialize_row(row),
                "amount": amount,
                "counterparty_match": vend_m,
                "account_match": acct_m,
                "xero_payload": payload,
                "voucher": _normalized_voucher(
                    voucher_type="Payment",
                    date_iso=amt_iso_date,
                    description=desc,
                    amount=amount,
                    account=account,
                    counterparty_match=vend_m,
                    counterparty_type="Vendor",
                    bank_account_id=bank_account_id,
                    bank_account_name=bank_account_name,
                    bank_account_code=bank_account_code,
                    reference=row.get("reference"),
                ),
                "duplicate_check": None,
                "_dup_input": {
                    "date_iso": amt_iso_date, "amount": amount,
                    "is_credit": False, "description": desc,
                },
            })

    # Batch dedupe the whole statement (one Xero range query + one local query).
    dup_inputs = [e.pop("_dup_input") for e in entries]
    dup_results = await check_bank_entries_batch(
        user_id=user_id, tenant_id=tenant_id, rows=dup_inputs,
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
        "bank_account_name": bank_account_name,
        "entries": entries,
        "needs_review": needs_review,
        "row_count": len(rows),
    }


async def push_one_entry(
    *,
    user_id: str,
    tenant_id: Optional[str],
    entry_type: str,
    payload: Dict[str, Any],
    extracted_total: Optional[float] = None,
) -> Dict[str, Any]:
    """Push one entry. `entry_type` controls which Xero endpoint is hit.

    deposit / expense -> POST /BankTransactions (RECEIVE / SPEND)
    payment           -> PUT /Payments. The frontend constructs the Payment
                         shape when the user picks an open Invoice/Bill.

    After a successful create we record vendor/account + narration history via
    xero_suggestion_service, and record the push for next-time dedupe.
    """
    if entry_type in ("deposit", "expense"):
        try:
            created = await xero_connector.create_bank_transaction(user_id, payload, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_bank_transaction failed: {exc}")
            return {"type": entry_type, "ok": False, "error": str(exc)}

        # Learn entity/account + narration mappings from the (un-stripped) payload.
        record_from_bank_entry_payload(
            user_id=user_id, tenant_id=tenant_id,
            entry_type=entry_type, payload=payload,
        )

        line = ((payload or {}).get("LineItems") or [{}])[0]
        party_name = (
            (payload or {}).get("vendor_name")
            or (payload or {}).get("customer_name")
            or (payload or {}).get("bank_description")
            or line.get("Description")
        )
        entity_id = (created or {}).get("BankTransactionID")
        record_bank_pushed(
            user_id=user_id, tenant_id=tenant_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("Date") or (created or {}).get("Date"),
            amount=extracted_total if extracted_total is not None
            else (line.get("UnitAmount") or (created or {}).get("Total")),
            doc_number=None,
            party_name=party_name,
            xero_entity_id=str(entity_id) if entity_id is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    if entry_type == "payment":
        if not (payload or {}).get("Invoice") and not (payload or {}).get("Account"):
            # Xero Payments require an Invoice (the bill/invoice being paid) and
            # a bank Account; the frontend builds this shape.
            return {"type": entry_type, "ok": False, "error": "Payment requires an Invoice + Account"}
        try:
            created = await xero_connector.create_payment(user_id, payload, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_payment failed: {exc}")
            return {"type": entry_type, "ok": False, "error": str(exc)}

        party_name = (payload or {}).get("vendor_name") or (payload or {}).get("customer_name")
        entity_id = (created or {}).get("PaymentID")
        record_bank_pushed(
            user_id=user_id, tenant_id=tenant_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("Date") or (created or {}).get("Date"),
            amount=extracted_total if extracted_total is not None
            else ((payload or {}).get("Amount") or (created or {}).get("Amount")),
            doc_number=None,
            party_name=party_name,
            xero_entity_id=str(entity_id) if entity_id is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    if entry_type == "banktransfer":
        # Contra: money moved between two bank accounts (Xero BankTransfer).
        try:
            created = await xero_connector.create_bank_transfer(user_id, payload, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_bank_transfer failed: {exc}")
            return {"type": entry_type, "ok": False, "error": str(exc)}
        entity_id = (created or {}).get("BankTransferID")
        record_bank_pushed(
            user_id=user_id, tenant_id=tenant_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("Date") or (created or {}).get("Date"),
            amount=extracted_total if extracted_total is not None
            else ((payload or {}).get("Amount") or (created or {}).get("Amount")),
            doc_number=None,
            party_name=(payload or {}).get("bank_description"),
            xero_entity_id=str(entity_id) if entity_id is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    if entry_type == "manualjournal":
        try:
            created = await xero_connector.create_manual_journal(user_id, payload, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_manual_journal failed: {exc}")
            return {"type": entry_type, "ok": False, "error": str(exc)}
        line = ((payload or {}).get("JournalLines") or [{}])[0]
        entity_id = (created or {}).get("ManualJournalID")
        record_bank_pushed(
            user_id=user_id, tenant_id=tenant_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("Date") or (created or {}).get("Date"),
            amount=extracted_total if extracted_total is not None
            else abs(float(line.get("LineAmount") or 0)),
            doc_number=(payload or {}).get("Reference"),
            party_name=(payload or {}).get("bank_description") or line.get("Description"),
            xero_entity_id=str(entity_id) if entity_id is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    return {"type": entry_type, "ok": False, "error": f"Unknown entry type {entry_type!r}"}


# Result-id key per Xero entry kind (for back-reporting the created id).
_ENTITY_ID_KEY = {
    "deposit": "BankTransactionID",
    "expense": "BankTransactionID",
    "banktransfer": "BankTransferID",
    "manualjournal": "ManualJournalID",
    "payment": "PaymentID",
}


async def _ensure_accounts_exist(
    user_id: str,
    tenant_id: Optional[str],
    required: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Create any chart-of-accounts entries that don't exist yet — the Xero
    analog of Tally's ensure_ledgers_exist / QBO's _ensure_accounts_exist.
    ``required`` is a list of ``{"name", "account_type"}``. Returns created /
    failed lists and a name(lower) -> {"id", "code"} map covering existing +
    newly-created accounts (Xero line items reference accounts by Code)."""
    name_to_ref: Dict[str, Dict[str, Optional[str]]] = {}
    created: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    if not required:
        return {"created": created, "failed": failed, "name_to_ref": name_to_ref}

    try:
        existing = await xero_connector.list_accounts(user_id, tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_accounts_exist: cannot list accounts: %s", exc)
        existing = []
    for a in existing:
        nm = (a.get("name") or "").strip().lower()
        if nm and a.get("list_id") is not None:
            name_to_ref[nm] = {"id": str(a.get("list_id")), "code": a.get("code")}

    # Pick the next free numeric Code for any account we have to create — Xero
    # requires a Code and line items reference it.
    used_codes = {str(a.get("code")) for a in existing if a.get("code")}
    next_code = 9000
    while str(next_code) in used_codes:
        next_code += 1

    seen = set()
    for req in required:
        name = (req.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in name_to_ref or key in seen:
            continue
        seen.add(key)
        acct_type = req.get("account_type") or "EXPENSE"
        code = str(next_code)
        next_code += 1
        try:
            res = await xero_connector.create_account(
                user_id, {"Name": name, "Type": acct_type, "Code": code}, tenant_id,
            )
            new_id = res.get("AccountID") if isinstance(res, dict) else None
            new_code = (res.get("Code") if isinstance(res, dict) else None) or code
            if new_id:
                name_to_ref[key] = {"id": str(new_id), "code": new_code}
                created.append({"name": name, "id": str(new_id), "code": new_code, "account_type": acct_type})
            else:
                failed.append({"name": name, "error": "Xero returned no account id"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("ensure_accounts_exist: create '%s' failed: %s", name, exc)
            failed.append({"name": name, "error": str(exc)})

    return {"created": created, "failed": failed, "name_to_ref": name_to_ref}


def _fill_account_refs(
    vouchers: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]],
    bank_account_id: Optional[str],
) -> None:
    """Backend-owns-translation: the shared (QB-cloned) frontend sends accounts
    by id/name, but Xero books line items by AccountCode. Fill any missing
    ``party_ledger_code`` / ``party_ledger_id`` / ``bank_ledger_code`` from the
    live account list so an id-only voucher still posts. Mutates in place.

    This keeps the Xero bank-entries view byte-identical to the QuickBooks one
    (which carries no account Code) — the Code requirement is satisfied here,
    on the backend, exactly like every other ERP-native translation."""
    by_id: Dict[str, Dict[str, Any]] = {}
    by_code: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    for a in accounts:
        aid = str(a.get("list_id")) if a.get("list_id") is not None else None
        code = str(a.get("code")) if a.get("code") is not None else None
        nm = (a.get("name") or "").strip().lower()
        ref = {"id": aid, "code": code}
        if aid:
            by_id[aid] = ref
        if code:
            by_code[code] = ref
        if nm:
            by_name[nm] = ref

    def _lookup(v_id, v_code, v_name):
        if v_id and str(v_id) in by_id:
            return by_id[str(v_id)]
        if v_code and str(v_code) in by_code:
            return by_code[str(v_code)]
        if v_name and v_name.strip().lower() in by_name:
            return by_name[v_name.strip().lower()]
        return None

    for v in vouchers:
        ref = _lookup(v.get("party_ledger_id"), v.get("party_ledger_code"), v.get("party_ledger"))
        if ref:
            if not v.get("party_ledger_id"):
                v["party_ledger_id"] = ref["id"]
            if not v.get("party_ledger_code"):
                v["party_ledger_code"] = ref["code"]
        if not v.get("bank_ledger_code"):
            bref = _lookup(v.get("bank_ledger_id") or bank_account_id, v.get("bank_ledger_code"), v.get("bank_ledger"))
            if bref:
                v["bank_ledger_code"] = bref["code"]


async def push_bank_vouchers_batch(
    *,
    user_id: str,
    tenant_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
    vouchers: List[Dict[str, Any]],
    auto_create_masters: bool = False,
) -> Dict[str, Any]:
    """Build each normalized voucher into a Xero payload and push it, recording
    history for next-time dedupe. The Xero analog of Tally's /push-vouchers-batch
    and QBO's push_bank_vouchers_batch.

    The frontend sends the normalized contract; this is the ONLY place the
    payload is built on the write path — never the frontend.

    ``auto_create_masters`` mirrors Tally's auto_create_ledgers: any party ledger
    that names an account with no id/code is created first (REVENUE for Receipts,
    EXPENSE for Payments), then its id + code are back-filled before the push.
    """
    created_masters: List[Dict[str, Any]] = []

    # Resolve account Codes from id/name first (the QB-cloned FE sends id-only
    # vouchers). Existing accounts get their Code filled here; only genuinely
    # new accounts then fall through to auto-create below.
    try:
        _accounts = await xero_connector.list_accounts(user_id, tenant_id)
        _fill_account_refs(vouchers, _accounts, bank_account_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("push_bank_vouchers_batch: account-ref resolution skipped: %s", exc)

    if auto_create_masters:
        required: List[Dict[str, str]] = []
        for v in vouchers:
            name = (v.get("party_ledger") or "").strip()
            if name and not (v.get("party_ledger_id") and v.get("party_ledger_code")):
                vt = (v.get("voucher_type") or "").strip().title()
                # Only auto-create where the type unambiguously implies an account
                # type. Contra/Journal targets are arbitrary accounts — leave those
                # for explicit creation rather than guess a type.
                if vt in ("Receipt", "Payment"):
                    required.append({
                        "name": name,
                        "account_type": "REVENUE" if vt == "Receipt" else "EXPENSE",
                    })
        ensure = await _ensure_accounts_exist(user_id, tenant_id, required)
        created_masters = ensure["created"]
        name_to_ref = ensure["name_to_ref"]
        for v in vouchers:
            ref = name_to_ref.get((v.get("party_ledger") or "").strip().lower())
            if ref:
                if not v.get("party_ledger_id"):
                    v["party_ledger_id"] = ref.get("id")
                if not v.get("party_ledger_code"):
                    v["party_ledger_code"] = ref.get("code")

    results: List[Dict[str, Any]] = []
    pushed = failed = skipped = 0

    for i, voucher in enumerate(vouchers):
        # Stage 1: build (pure). A BankVoucherError means the row is malformed —
        # skip it and report, exactly like build_bank_vouchers_xml skips bad rows.
        try:
            entry_type, payload = build_xero_bank_payload(
                voucher,
                bank_account_id=bank_account_id,
                bank_account_name=bank_account_name,
            )
        except BankVoucherError as exc:
            skipped += 1
            results.append({"index": i, "ok": False, "skipped": True, "error": str(exc)})
            continue

        # Stage 2: push.
        try:
            res = await push_one_entry(
                user_id=user_id,
                tenant_id=tenant_id,
                entry_type=entry_type,
                payload=payload,
            )
        except Exception as exc:  # network / Xero API error for this row only
            failed += 1
            logger.exception("push_bank_vouchers_batch: row %d failed", i)
            results.append({"index": i, "ok": False, "type": entry_type, "error": str(exc)})
            continue

        if res.get("ok"):
            pushed += 1
            created = res.get("result") or {}
            id_key = _ENTITY_ID_KEY.get(entry_type)
            ent_id = created.get(id_key) if id_key else None
            results.append({
                "index": i, "ok": True, "type": entry_type,
                "id": str(ent_id) if ent_id is not None else None,
            })
        else:
            failed += 1
            results.append({"index": i, "ok": False, "type": entry_type, "error": res.get("error")})

    return {
        "ok": failed == 0,
        "pushed": pushed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "created_masters": created_masters,
    }


async def validate_bank_vouchers(
    *,
    user_id: str,
    tenant_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
    vouchers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pre-push validation — the Xero analog of Tally's /validate-pre-push and
    QBO's validate_bank_vouchers.

    Reports rows that can't be built (errors), party accounts the user typed
    that don't yet exist in Xero (missing_masters, for the create-account flow),
    and likely duplicates.
    """
    accounts = await xero_connector.list_accounts(user_id, tenant_id)
    valid_ids = {str(a.get("list_id")) for a in accounts if a.get("list_id")}
    valid_codes = {str(a.get("code")) for a in accounts if a.get("code")}

    # Fill missing account Codes from id/name (QB-cloned FE sends id-only) so an
    # existing account isn't mis-flagged as a missing master below.
    _fill_account_refs(vouchers, accounts, bank_account_id)

    errors: List[Dict[str, Any]] = []
    missing_masters: List[Dict[str, Any]] = []
    seen_missing = set()
    dup_inputs: List[Dict[str, Any]] = []

    for i, v in enumerate(vouchers):
        party_id = v.get("party_ledger_id")
        party_code = v.get("party_ledger_code")
        party_name = (v.get("party_ledger") or "").strip()
        # An account name with no resolvable id/code (Xero refs accounts by Code)
        # means the user picked/typed a master that must be created before push.
        party_resolved = (
            party_id and str(party_id) in valid_ids
            and party_code and str(party_code) in valid_codes
        )
        if party_name and not party_resolved:
            key = party_name.lower()
            if key not in seen_missing:
                seen_missing.add(key)
                missing_masters.append({"name": party_name, "slot": "account", "index": i})

        bank_id = v.get("bank_ledger_id") or bank_account_id
        if not bank_id or str(bank_id) not in valid_ids:
            errors.append({"index": i, "error": "bank account does not exist in Xero"})

        # Dry-run the builder to surface any other structural problem.
        try:
            build_xero_bank_payload(
                v, bank_account_id=bank_account_id, bank_account_name=bank_account_name,
            )
        except BankVoucherError as exc:
            # A missing party account is already reported as a missing master;
            # don't double-report it as a hard error.
            if not (party_name and not party_resolved):
                errors.append({"index": i, "error": str(exc)})

        dup_inputs.append({
            "date_iso": v.get("date"),
            "amount": v.get("amount"),
            "is_credit": (v.get("voucher_type") or "").strip().title() == "Receipt",
            "description": v.get("narration") or "",
        })

    duplicates = await check_bank_entries_batch(
        user_id=user_id, tenant_id=tenant_id, rows=dup_inputs,
    )

    return {
        "ok": not errors and not missing_masters,
        "errors": errors,
        "missing_masters": missing_masters,
        "duplicates": duplicates,
    }
