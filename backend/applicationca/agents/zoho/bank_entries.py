"""
Zoho Books bank-entries pipeline.

Bank-entries is the write-side analog of bank reconciliation: parse a statement
and create one Zoho entry per row (a bank ``deposit`` for inflows, an
``expense`` paid through the selected bank account for outflows). Use this when
the statement's transactions are NOT already in Zoho and you want to bootstrap
them.

Mirrors agents/qbo/bank_entries.py. Differences vs QBO:
  * Company key is ``organization_id`` (no realm_id, no desktop/QBXML path —
    Zoho is cloud-OAuth only).
  * zoho_connector list_* methods already return normalized cards
    ({list_id, name, account_type, ...}), so we consume them directly instead of
    re-normalizing raw QBO objects.
  * Zoho bank credits are /banktransactions deposits (`from_account_id` ->
    `to_account_id`), while debits are /expenses (`account_id` paid through the
    bank account). Display names and the raw ``bank_description`` ride inline
    for the UI + history; the connector strips display-only keys before POSTing.

Each row produces a draft ``zoho_payload``. The frontend lets the user edit
counterparty / account before pushing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from applicationca.agents.document_specific_functions.bank_reconciliation import (
    parse_bank_statement_for_recon,
)
from applicationca.agents.zoho.bank_payload import (
    build_zoho_bank_payload,
    BankVoucherError,
)
from applicationca.services.zoho_aliases import get_aliases
from applicationca.services.zoho_connector import zoho_connector
from applicationca.services.zoho_suggestion_service import (
    record_from_bank_entry_payload,
    suggest_for_bank_entry,
)
from applicationca.services.zoho_duplicate_check import (
    check_bank_entries_batch,
    record_pushed as record_bank_pushed,
    DOC_BANK,
)

from ._llm import make_llm_async

logger = logging.getLogger(__name__)


# Zoho chart-of-accounts types (lowercased) that are valid targets for each side
# of a bank entry. Credits (money in) book to income; debits (money out) book to
# an expense account.
_INCOME_TYPES = ("income", "other_income")
_EXPENSE_TYPES = ("expense", "cost_of_goods_sold", "other_expense", "other_current_liability")
_DEFAULT_INCOME_HINTS = ("sales", "services", "other income", "uncategorized")
_DEFAULT_EXPENSE_HINTS = ("office supplies", "miscellaneous", "other expense", "uncategorized")

_TXN_TYPE_DEPOSIT = "deposit"
_ENTRY_TYPE_EXPENSE = "expense"


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
        s = d.strip()
        # Match QBO: bank-entry dates posted to cloud ERPs must be ISO. The AI
        # extractor commonly emits DD/MM/YYYY for Indian statements.
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", s)
        if m:
            dd, mm, yy = m.group(1), m.group(2), m.group(3)
            if len(yy) == 2:
                yy = "20" + yy
            return f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
        return s[:10]
    return None


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": _row_date_iso(row.get("date")),
        "description": row.get("description"),
        "reference": row.get("reference"),
        "debit_amount": float(row["debit_amount"]) if row.get("debit_amount") else None,
        "credit_amount": float(row["credit_amount"]) if row.get("credit_amount") else None,
    }


def _coerce_legacy_push_payload(entry_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Convert pre-parity bank-entry push payloads to current Zoho schemas.

    Older drafts stored /banktransactions-style payloads with the selected bank
    in top-level account_id and the counter account in line_items[0]. Current
    Zoho posting needs deposits as from_account_id -> to_account_id and expenses
    as /expenses with account_id + paid_through_account_id.
    """
    if not isinstance(payload, dict):
        return payload
    lines = payload.get("line_items") or []
    line = lines[0] if lines and isinstance(lines[0], dict) else {}

    if entry_type == "deposit" and not payload.get("to_account_id") and payload.get("account_id"):
        if not line.get("account_id"):
            return payload
        out = {
            k: v for k, v in payload.items()
            if k not in ("account_id", "account_name", "line_items")
        }
        out["to_account_id"] = payload.get("account_id")
        if payload.get("account_name"):
            out["to_account_name"] = payload.get("account_name")
        out["from_account_id"] = line.get("account_id")
        if line.get("account_name"):
            out["from_account_name"] = line.get("account_name")
        out.setdefault("description", payload.get("bank_description") or line.get("description"))
        return out

    if entry_type == "expense" and not payload.get("paid_through_account_id") and payload.get("account_id"):
        if not line.get("account_id"):
            return payload
        out = {
            k: v for k, v in payload.items()
            if k not in ("transaction_type", "line_items")
        }
        out["paid_through_account_id"] = payload.get("account_id")
        out["account_id"] = line.get("account_id")
        if line.get("account_name"):
            out["account_name"] = line.get("account_name")
        out.setdefault("description", payload.get("bank_description") or line.get("description"))
        return out

    return payload


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
    reference: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the ERP-agnostic normalized bank voucher (the same contract QBO/Tally
    use) from a matched statement row. ``party_ledger`` is the Zoho income/expense
    account; ``counterparty`` is the silently-matched Customer/Vendor (or None) —
    it is never required and only attaches when the matcher was confident."""
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
        "bank_ledger": bank_account_name,
        "bank_ledger_id": str(bank_account_id) if bank_account_id else None,
        "reference": reference,
        "counterparty": cp,
    }


async def _extract_statement_rows(file_content: bytes, file_name: str) -> List[Dict[str, Any]]:
    """Extract rows with the same AI bank-entry extractor QBO/Tally use.

    The reconciliation parser is kept as a fallback only. It is table-oriented
    and can misread statement direction on some layouts; the AI extractor is the
    canonical bank-entry path for write-side statement imports.
    """
    import os
    import tempfile
    from config.settings import settings
    from applicationca.agents.document_specific_functions.bank_entries import (
        extract_bank_transactions,
    )

    suffix = os.path.splitext(file_name)[1] or ".pdf"
    tmp_path = None
    txns: List[Dict[str, Any]] = []
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        extracted_text = ""
        if suffix.lower() == ".pdf":
            def _read_pdf_text(path: str) -> str:
                import PyPDF2
                with open(path, "rb") as fh:
                    reader = PyPDF2.PdfReader(fh)
                    return "\n".join((page.extract_text() or "") for page in reader.pages)

            try:
                extracted_text = await asyncio.to_thread(_read_pdf_text, tmp_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Zoho bank extract: PDF text extraction failed: %s", exc)

        bank_model = os.getenv("BANK_EXTRACTION_MODEL", "gpt-4.1")
        txns = await extract_bank_transactions(
            extracted_text=extracted_text,
            file_path=tmp_path,
            openai_model=bank_model,
            openai_api_key=settings.OPENAI_API_KEY,
            anthropic_api_key=getattr(settings, "ANTHROPIC_API_KEY", None),
            extraction_provider=getattr(settings, "EXTRACTION_PROVIDER", "openai"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Zoho bank extract: AI extractor failed, will fall back: %s", exc)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    rows: List[Dict[str, Any]] = []
    for t in txns or []:
        amount = float(t.get("amount") or 0)
        if not amount:
            continue
        is_credit = str(t.get("type", "")).strip().lower() in ("receipt", "credit", "deposit")
        rows.append({
            "date": t.get("date"),
            "description": t.get("description") or "",
            "reference": t.get("reference") or "",
            "credit_amount": amount if is_credit else None,
            "debit_amount": None if is_credit else amount,
        })

    if not rows:
        try:
            parsed = parse_bank_statement_for_recon(file_content, file_name)
            rows = parsed.get("transactions") or []
        except Exception:  # noqa: BLE001
            pass
    return rows


async def process_bank_statement_for_entries(
    file_content: bytes,
    file_name: str,
    *,
    user_id: str,
    organization_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse statement, classify each row as deposit/expense, match counterparty
    + counter-account, and build draft Zoho bank-transaction payloads."""
    rows = await _extract_statement_rows(file_content, file_name)
    if not rows:
        return {"ok": False, "stage": "extract", "error": "Could not extract any transactions"}

    # zoho_connector returns normalized cards directly — no re-normalization.
    customer_cards = await zoho_connector.list_customers(user_id, organization_id)
    vendor_cards = await zoho_connector.list_vendors(user_id, organization_id)
    all_accounts = await zoho_connector.list_accounts(user_id, organization_id)

    income_accts = [
        a for a in all_accounts if (a.get("account_type") or "").lower() in _INCOME_TYPES
    ] or all_accounts
    expense_accts = [
        a for a in all_accounts if (a.get("account_type") or "").lower() in _EXPENSE_TYPES
    ] or all_accounts

    fallback_income = _pick_default(income_accts, _DEFAULT_INCOME_HINTS)
    fallback_expense = _pick_default(expense_accts, _DEFAULT_EXPENSE_HINTS)

    llm_async = make_llm_async()

    # Tier-4 alias maps. Bank entries touch all four entity types — load each once
    # and thread them through every per-row suggestion call so alias hits
    # short-circuit fuzzy / history lookups.
    customer_aliases = get_aliases(user_id=user_id, organization_id=organization_id or "", entity_type="customer")
    vendor_aliases = get_aliases(user_id=user_id, organization_id=organization_id or "", entity_type="vendor")
    account_aliases = get_aliases(user_id=user_id, organization_id=organization_id or "", entity_type="account")

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
                    organization_id=organization_id,
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
                organization_id=organization_id,
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
            account = acct_m if acct_m.get("matched") else (fallback_income or {})

            payload: Dict[str, Any] = {
                "transaction_type": _TXN_TYPE_DEPOSIT,
                "to_account_id": bank_account_id,
                "amount": amount,
                "description": desc,
                "bank_description": desc,  # raw narration; display-only, kept for history
            }
            if bank_account_name:
                payload["to_account_name"] = bank_account_name
            if account.get("list_id"):
                payload["from_account_id"] = account.get("list_id")
                payload["from_account_name"] = account.get("name")  # display-only; stripped before POST
            if amt_iso_date:
                payload["date"] = amt_iso_date
            if cust_m.get("matched"):
                # Flat customer ref; carry display name inline for UI + history.
                payload["customer_id"] = cust_m.get("list_id")
                payload["customer_name"] = cust_m.get("name")

            entries.append({
                "type": "deposit",
                "row": _serialize_row(row),
                "amount": amount,
                "counterparty_match": cust_m,
                "account_match": acct_m,
                "zoho_payload": payload,
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
            account = acct_m if acct_m.get("matched") else (fallback_expense or {})

            payload = {
                "account_id": account.get("list_id"),
                "account_name": account.get("name"),  # display-only; stripped before POST
                "paid_through_account_id": bank_account_id,
                "amount": amount,
                "description": desc,
                "bank_description": desc,  # raw narration; display-only, kept for history
            }
            if bank_account_name:
                payload["paid_through_account_name"] = bank_account_name
            if amt_iso_date:
                payload["date"] = amt_iso_date
            if vend_m.get("matched"):
                # Flat vendor ref; carry display name inline for UI + history.
                payload["vendor_id"] = vend_m.get("list_id")
                payload["vendor_name"] = vend_m.get("name")

            entries.append({
                "type": _ENTRY_TYPE_EXPENSE,
                "row": _serialize_row(row),
                "amount": amount,
                "counterparty_match": vend_m,
                "account_match": acct_m,
                "zoho_payload": payload,
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
                    reference=row.get("reference"),
                ),
                "duplicate_check": None,
                "_dup_input": {
                    "date_iso": amt_iso_date, "amount": amount,
                    "is_credit": False, "description": desc,
                },
            })

    # Batch dedupe the whole statement (two Zoho range queries + one local query).
    dup_inputs = [e.pop("_dup_input") for e in entries]
    dup_results = await check_bank_entries_batch(
        user_id=user_id, organization_id=organization_id, rows=dup_inputs,
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
    organization_id: Optional[str],
    entry_type: str,
    payload: Dict[str, Any],
    extracted_total: Optional[float] = None,
) -> Dict[str, Any]:
    """Push one entry. `entry_type` controls which Zoho endpoint is hit.

    deposit          -> /banktransactions
    expense          -> /expenses
    payment           -> /vendorpayments or /customerpayments (the payload shape
                         decides: vendor_id -> vendor payment, customer_id ->
                         customer receipt). The frontend constructs the payment
                         shape when the user picks an open Bill or Invoice.

    After a successful create we record vendor/account + narration history via
    zoho_suggestion_service, and record the push for next-time dedupe.
    """
    payload = _coerce_legacy_push_payload(entry_type, payload)

    if entry_type == "deposit":
        try:
            created = await zoho_connector.create_bank_transaction(user_id, payload, organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_bank_transaction failed: {exc}")
            return {"type": entry_type, "ok": False, "error": str(exc)}

        # Learn entity/account + narration mappings from the (un-stripped) payload.
        record_from_bank_entry_payload(
            user_id=user_id, organization_id=organization_id,
            entry_type=entry_type, payload=payload,
        )

        line = ((payload or {}).get("line_items") or [{}])[0]
        party_name = (
            (payload or {}).get("vendor_name")
            or (payload or {}).get("customer_name")
            or (payload or {}).get("bank_description")
            or line.get("description")
        )
        record_bank_pushed(
            user_id=user_id, organization_id=organization_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("date") or (created or {}).get("date"),
            amount=extracted_total if extracted_total is not None
            else ((payload or {}).get("amount") or line.get("amount") or (created or {}).get("amount")),
            doc_number=None,
            party_name=party_name,
            zoho_entity_id=str((created or {}).get("transaction_id")) if (created or {}).get("transaction_id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    if entry_type == "expense":
        try:
            created = await zoho_connector.create_expense(user_id, payload, organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_expense failed: {exc}")
            return {"type": entry_type, "ok": False, "error": str(exc)}

        record_from_bank_entry_payload(
            user_id=user_id, organization_id=organization_id,
            entry_type=entry_type, payload=payload,
        )

        party_name = (
            (payload or {}).get("vendor_name")
            or (payload or {}).get("bank_description")
            or (payload or {}).get("description")
        )
        record_bank_pushed(
            user_id=user_id, organization_id=organization_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("date") or (created or {}).get("date"),
            amount=extracted_total if extracted_total is not None
            else ((payload or {}).get("amount") or (created or {}).get("amount")),
            doc_number=None,
            party_name=party_name,
            zoho_entity_id=str((created or {}).get("expense_id")) if (created or {}).get("expense_id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    if entry_type == "payment":
        if (payload or {}).get("vendor_id"):
            try:
                created = await zoho_connector.create_vendor_payment(user_id, payload, organization_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"create_vendor_payment failed: {exc}")
                return {"type": entry_type, "ok": False, "error": str(exc)}
            party_name = (payload or {}).get("vendor_name")
            entity_id = (created or {}).get("payment_id") or (created or {}).get("vendorpayment_id")
        elif (payload or {}).get("customer_id"):
            try:
                created = await zoho_connector.create_customer_payment(user_id, payload, organization_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"create_customer_payment failed: {exc}")
                return {"type": entry_type, "ok": False, "error": str(exc)}
            party_name = (payload or {}).get("customer_name")
            entity_id = (created or {}).get("payment_id")
        else:
            return {"type": entry_type, "ok": False, "error": "Payment requires vendor_id or customer_id"}

        record_bank_pushed(
            user_id=user_id, organization_id=organization_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("date") or (created or {}).get("date"),
            amount=extracted_total if extracted_total is not None
            else ((payload or {}).get("amount") or (created or {}).get("amount")),
            doc_number=None,
            party_name=party_name,
            zoho_entity_id=str(entity_id) if entity_id is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    if entry_type == "transfer":
        try:
            created = await zoho_connector.create_transfer(user_id, payload, organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_transfer failed: {exc}")
            return {"type": entry_type, "ok": False, "error": str(exc)}
        record_bank_pushed(
            user_id=user_id, organization_id=organization_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("date") or (created or {}).get("date"),
            amount=extracted_total if extracted_total is not None
            else ((payload or {}).get("amount") or (created or {}).get("amount")),
            doc_number=None,
            party_name=(payload or {}).get("to_account_name"),
            zoho_entity_id=str((created or {}).get("transaction_id")) if (created or {}).get("transaction_id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    if entry_type == "journal":
        try:
            created = await zoho_connector.create_journal(user_id, payload, organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_journal failed: {exc}")
            return {"type": entry_type, "ok": False, "error": str(exc)}
        line = ((payload or {}).get("line_items") or [{}])[0]
        record_bank_pushed(
            user_id=user_id, organization_id=organization_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("journal_date") or (created or {}).get("journal_date"),
            amount=extracted_total if extracted_total is not None else line.get("amount"),
            doc_number=(payload or {}).get("reference_number"),
            party_name=line.get("description"),
            zoho_entity_id=str((created or {}).get("journal_id")) if (created or {}).get("journal_id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}

    return {"type": entry_type, "ok": False, "error": f"Unknown entry type {entry_type!r}"}


async def _ensure_accounts_exist(
    user_id: str,
    organization_id: Optional[str],
    required: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Create any chart-of-accounts entries that don't exist yet — the Zoho analog
    of QBO's _ensure_accounts_exist / Tally's ensure_ledgers_exist. ``required`` is
    a list of ``{"name", "account_type"}``. Returns created/failed lists and a
    name(lower) -> id map covering existing + newly-created accounts."""
    name_to_id: Dict[str, str] = {}
    created: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    if not required:
        return {"created": created, "failed": failed, "name_to_id": name_to_id}

    try:
        existing = await zoho_connector.list_accounts(user_id, organization_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_accounts_exist: cannot list accounts: %s", exc)
        existing = []
    for a in existing:
        nm = (a.get("name") or "").strip().lower()
        if nm and a.get("list_id") is not None:
            name_to_id[nm] = str(a.get("list_id"))

    seen = set()
    for req in required:
        name = (req.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in name_to_id or key in seen:
            continue
        seen.add(key)
        # Zoho expects an account_type slug; income/expense for the two unambiguous
        # bank-entry slots. (qbo used Income/Expense in TitleCase; zoho uses slugs.)
        acct_type = req.get("account_type") or "expense"
        try:
            acc = await zoho_connector.create_account(
                user_id, {"account_name": name, "account_type": acct_type}, organization_id,
            )
            new_id = str(acc.get("account_id")) if acc and acc.get("account_id") is not None else None
            if new_id:
                name_to_id[key] = new_id
                created.append({"name": name, "id": new_id, "account_type": acct_type})
            else:
                failed.append({"name": name, "error": "Zoho returned no account id"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("ensure_accounts_exist: create '%s' failed: %s", name, exc)
            failed.append({"name": name, "error": str(exc)})

    return {"created": created, "failed": failed, "name_to_id": name_to_id}


async def push_bank_vouchers_batch(
    *,
    user_id: str,
    organization_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
    vouchers: List[Dict[str, Any]],
    auto_create_masters: bool = False,
) -> Dict[str, Any]:
    """Build each normalized voucher into a Zoho payload and push it, recording
    history for next-time dedupe. The Zoho analog of QBO's push_bank_vouchers_batch
    / Tally's /push-vouchers-batch.

    The frontend sends the normalized contract; this is the ONLY place the payload
    is built on the write path — never the frontend.

    ``auto_create_masters`` mirrors QBO/Tally's behavior: any party ledger that
    names an account with no id is created first (income for Receipts, expense for
    Payments), then its id is back-filled before the push.
    """
    created_masters: List[Dict[str, Any]] = []
    if auto_create_masters:
        required: List[Dict[str, str]] = []
        for v in vouchers:
            name = (v.get("party_ledger") or "").strip()
            if name and not v.get("party_ledger_id"):
                vt = (v.get("voucher_type") or "").strip().title()
                # Only auto-create where the type unambiguously implies an account
                # type. Contra/Journal targets are arbitrary accounts — leave those
                # for explicit creation rather than guess a type.
                if vt in ("Receipt", "Payment"):
                    required.append({
                        "name": name,
                        "account_type": "income" if vt == "Receipt" else "expense",
                    })
        ensure = await _ensure_accounts_exist(user_id, organization_id, required)
        created_masters = ensure["created"]
        name_to_id = ensure["name_to_id"]
        for v in vouchers:
            if not v.get("party_ledger_id"):
                nid = name_to_id.get((v.get("party_ledger") or "").strip().lower())
                if nid:
                    v["party_ledger_id"] = nid

    results: List[Dict[str, Any]] = []
    pushed = failed = skipped = 0

    for i, voucher in enumerate(vouchers):
        # Stage 1: build (pure). A BankVoucherError means the row is malformed —
        # skip it and report, exactly like QBO/Tally skip bad rows.
        try:
            entry_type, payload = build_zoho_bank_payload(
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
                organization_id=organization_id,
                entry_type=entry_type,
                payload=payload,
            )
        except Exception as exc:  # network / Zoho API error for this row only
            failed += 1
            logger.exception("push_bank_vouchers_batch: row %d failed", i)
            results.append({"index": i, "ok": False, "type": entry_type, "error": str(exc)})
            continue

        if res.get("ok"):
            pushed += 1
            created = res.get("result") or {}
            entity_id = (
                created.get("transaction_id")
                or created.get("journal_id")
                or created.get("banktransaction_id")
            )
            results.append({
                "index": i, "ok": True, "type": entry_type,
                "id": str(entity_id) if entity_id is not None else None,
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
    organization_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
    vouchers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pre-push validation — the Zoho analog of QBO's validate_bank_vouchers /
    Tally's /validate-pre-push.

    Reports rows that can't be built (errors), party accounts the user typed that
    don't yet exist in Zoho (missing_masters, for the create-account flow), and
    likely duplicates.
    """
    all_accounts = await zoho_connector.list_accounts(user_id, organization_id)
    valid_ids = {str(a.get("list_id")) for a in all_accounts}

    errors: List[Dict[str, Any]] = []
    missing_masters: List[Dict[str, Any]] = []
    seen_missing = set()
    dup_inputs: List[Dict[str, Any]] = []

    for i, v in enumerate(vouchers):
        party_id = v.get("party_ledger_id")
        party_name = (v.get("party_ledger") or "").strip()
        # An account name with no id (or an id that no longer resolves) means the
        # user picked/typed a master that must be created before push.
        if party_name and (not party_id or str(party_id) not in valid_ids):
            key = party_name.lower()
            if key not in seen_missing:
                seen_missing.add(key)
                missing_masters.append({"name": party_name, "slot": "account", "index": i})

        bank_id = v.get("bank_ledger_id") or bank_account_id
        if not bank_id or str(bank_id) not in valid_ids:
            errors.append({"index": i, "error": "bank account does not exist in Zoho"})

        # Dry-run the builder to surface any other structural problem.
        try:
            build_zoho_bank_payload(
                v, bank_account_id=bank_account_id, bank_account_name=bank_account_name,
            )
        except BankVoucherError as exc:
            # A missing party id is already reported as a missing master; don't
            # double-report it as a hard error.
            if not (party_name and not party_id):
                errors.append({"index": i, "error": str(exc)})

        dup_inputs.append({
            "date_iso": v.get("date"),
            "amount": v.get("amount"),
            "is_credit": (v.get("voucher_type") or "").strip().title() == "Receipt",
            "description": v.get("narration") or "",
        })

    duplicates = await check_bank_entries_batch(
        user_id=user_id, organization_id=organization_id, rows=dup_inputs,
    )

    return {
        "ok": not errors and not missing_masters,
        "errors": errors,
        "missing_masters": missing_masters,
        "duplicates": duplicates,
    }
