"""
QBO bank-entries pipeline.

Bank-entries is the write-side analog of bank reconciliation: parse a
statement and create one QBO transaction per row (Deposit for inflows,
Purchase / cash-expense for outflows). Use this when the statement's
transactions are NOT already in QBO and you want to bootstrap them.

Each row produces a draft QBO payload. The frontend lets the user edit
counterparty / account / payment type before pushing.
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
from applicationca.agents.qbo._llm import make_llm_async
from applicationca.agents.qbo.bank_payload import (
    build_qbo_bank_payload,
    BankVoucherError,
)
from applicationca.services.qbo_aliases import get_aliases
from applicationca.services.qbo_connector import qbo_connector
from applicationca.services.qbo_suggestion_service import (
    record_from_bank_entry_payload,
    suggest_for_bank_entry,
)
from applicationca.services.qbo_duplicate_check import (
    check_bank_entries_batch,
    record_pushed as record_bank_pushed,
    DOC_BANK,
)

logger = logging.getLogger(__name__)


_INCOME_TYPES = ("Income", "OtherIncome")
_EXPENSE_TYPES = ("Expense", "CostOfGoodsSold", "OtherExpense", "OtherCurrentLiability")
_DEFAULT_INCOME_HINTS = ("Sales", "Services", "Other Income", "Uncategorized Income")
_DEFAULT_EXPENSE_HINTS = ("Office Supplies", "Miscellaneous", "Other Expense", "Uncategorized Expense")


def _pick_default(accounts: List[Dict[str, Any]], hints: tuple) -> Optional[Dict[str, Any]]:
    for hint in hints:
        for a in accounts:
            if hint.lower() in (a.get("name") or "").lower():
                return a
    return accounts[0] if accounts else None


def _row_date_iso(d: Any) -> Optional[str]:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, str) and d:
        s = d.strip()
        # The AI extractor emits DD/MM/YYYY (or DD-MM-YYYY). QBO TxnDate needs
        # ISO YYYY-MM-DD, so convert rather than pass slashes through (which QBO
        # rejects). Already-ISO strings fall through unchanged.
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", s)
        if m:
            dd, mm, yy = m.group(1), m.group(2), m.group(3)
            if len(yy) == 2:
                yy = "20" + yy
            return f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
        return s[:10]
    return None


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
    """Build the ERP-agnostic normalized bank voucher (the same contract Tally
    uses) from a matched statement row. ``party_ledger`` is the QB Income/Expense
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
    """Extract bank rows using the SAME AI extractor Tally uses
    (``extract_bank_transactions``), so a given statement classifies into
    receipts vs payments identically across ERPs.

    Why: the old path used ``parse_bank_statement_for_recon`` — a heuristic
    Debit/Credit-column table parser built for reconciliation. On layouts it
    couldn't read (e.g. HDFC) it dropped every amount into ``debit_amount``, so
    every row became a Payment. Tally's extractor is AI-based and direction-aware
    ("BY TRANSFER/Credit = receipt, TO TRANSFER/Debit = payment"), so it splits
    correctly. Reusing it kills the two-extractor drift.

    ``extract_bank_transactions`` returns ``[{date, description, type, amount}]``;
    we map ``type`` -> ``credit_amount``/``debit_amount`` which the rest of this
    pipeline expects.
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

        # PDF text is extracted exactly like Tally (PyPDF2) so identical text
        # reaches the identical extractor. Excel/CSV/images are handled inside
        # extract_bank_transactions via the file path.
        extracted_text = ""
        if suffix.lower() == ".pdf":
            def _read_pdf_text(path: str) -> str:
                import PyPDF2
                with open(path, "rb") as fh:
                    reader = PyPDF2.PdfReader(fh)
                    return "\n".join((page.extract_text() or "") for page in reader.pages)

            try:
                # Off the event loop: page-by-page extraction on a 40-50 page
                # PDF otherwise stalls every other request on this worker.
                extracted_text = await asyncio.to_thread(_read_pdf_text, tmp_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("QB bank extract: PDF text extraction failed: %s", exc)

        # Use the SAME model Tally uses for bank extraction (gpt-4.1, chosen for
        # its large output budget), not the general OPENAI_MODEL (gpt-4.1-mini),
        # so classification/extraction quality matches Tally on the same file.
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
        logger.warning("QB bank extract: AI extractor failed, will fall back: %s", exc)
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

    # Fallback: if the AI extractor produced nothing, degrade to the old table
    # parser rather than returning an empty result.
    if not rows:
        try:
            parsed = parse_bank_statement_for_recon(file_content, file_name)
            rows = parsed.get("transactions") or []
        except Exception:  # noqa: BLE001
            pass
    return rows


async def process_bank_statement_for_entries(
    *,
    file_content: bytes,
    file_name: str,
    user_id: str,
    realm_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse statement, classify each row as deposit/purchase, match
    counterparty + counter-account, build draft QBO payloads."""
    rows = await _extract_statement_rows(file_content, file_name)
    if not rows:
        return {"ok": False, "stage": "extract", "error": "Could not extract any transactions"}

    raw_customers = await qbo_connector.list_customers(user_id, realm_id)
    raw_vendors = await qbo_connector.list_vendors(user_id, realm_id)
    raw_accounts = await qbo_connector.list_accounts(user_id, realm_id)

    customer_cards = [
        {"list_id": c.get("Id"), "name": c.get("DisplayName") or c.get("FullyQualifiedName")}
        for c in raw_customers
    ]
    vendor_cards = [
        {"list_id": v.get("Id"), "name": v.get("DisplayName") or v.get("FullyQualifiedName")}
        for v in raw_vendors
    ]
    income_accts = [
        {"list_id": a.get("Id"), "name": a.get("Name"), "account_type": a.get("AccountType")}
        for a in raw_accounts if a.get("AccountType") in _INCOME_TYPES
    ]
    expense_accts = [
        {"list_id": a.get("Id"), "name": a.get("Name"), "account_type": a.get("AccountType")}
        for a in raw_accounts if a.get("AccountType") in _EXPENSE_TYPES
    ]

    fallback_income = _pick_default(income_accts, _DEFAULT_INCOME_HINTS)
    fallback_expense = _pick_default(expense_accts, _DEFAULT_EXPENSE_HINTS)

    llm_async = make_llm_async()

    # Tier-4 alias maps. Bank entries touch all four entity types — load each
    # once and thread them through every per-row suggestion call so alias hits
    # short-circuit fuzzy / history lookups.
    customer_aliases = get_aliases(user_id=user_id, realm_id=realm_id or "", entity_type="customer")
    vendor_aliases = get_aliases(user_id=user_id, realm_id=realm_id or "", entity_type="vendor")
    account_aliases = get_aliases(user_id=user_id, realm_id=realm_id or "", entity_type="account")

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
                    realm_id=realm_id,
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
                realm_id=realm_id,
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
            line = {
                "Amount": amount,
                "DetailType": "DepositLineDetail",
                "Description": desc or None,
                "DepositLineDetail": {
                    "AccountRef": {"value": account.get("list_id"), "name": account.get("name")} if account.get("list_id") else {"name": account.get("name") or ""},
                },
            }
            if cust_m.get("matched"):
                line["DepositLineDetail"]["Entity"] = {
                    "type": "Customer",
                    "value": cust_m.get("list_id"),
                    "name": cust_m.get("name"),
                }
            payload: Dict[str, Any] = {
                "DepositToAccountRef": {"value": bank_account_id, "name": bank_account_name or ""},
                "Line": [line],
            }
            if amt_iso_date:
                payload["TxnDate"] = amt_iso_date
            entries.append({
                "type": "deposit",
                "row": _serialize_row(row),
                "amount": amount,
                "counterparty_match": cust_m,
                "account_match": acct_m,
                "qbo_payload": payload,
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
            line = {
                "Amount": amount,
                "DetailType": "AccountBasedExpenseLineDetail",
                "Description": desc or None,
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": account.get("list_id"), "name": account.get("name")} if account.get("list_id") else {"name": account.get("name") or ""},
                },
            }
            payload = {
                "AccountRef": {"value": bank_account_id, "name": bank_account_name or ""},
                "PaymentType": "Cash",
                "Line": [line],
            }
            if vend_m.get("matched"):
                payload["EntityRef"] = {
                    "type": "Vendor",
                    "value": vend_m.get("list_id"),
                    "name": vend_m.get("name"),
                }
            if amt_iso_date:
                payload["TxnDate"] = amt_iso_date
            entries.append({
                "type": "purchase",
                "row": _serialize_row(row),
                "amount": amount,
                "counterparty_match": vend_m,
                "account_match": acct_m,
                "qbo_payload": payload,
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

    dup_inputs = [e.pop("_dup_input") for e in entries]
    dup_results = await check_bank_entries_batch(
        user_id=user_id, realm_id=realm_id, rows=dup_inputs,
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


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": _row_date_iso(row.get("date")),
        "description": row.get("description"),
        "reference": row.get("reference"),
        "debit_amount": float(row["debit_amount"]) if row.get("debit_amount") else None,
        "credit_amount": float(row["credit_amount"]) if row.get("credit_amount") else None,
    }


async def push_one_entry(
    *,
    user_id: str,
    realm_id: Optional[str],
    entry_type: str,
    payload: Dict[str, Any],
    extracted_total: Optional[float] = None,
) -> Dict[str, Any]:
    """Push one entry. `entry_type` controls which QBO endpoint is hit."""
    if entry_type == "deposit":
        res = await qbo_connector.create_deposit(user_id, payload, realm_id)
        created = res.get("Deposit", res) if isinstance(res, dict) else res
        # Record for next-time dedupe.
        line = ((payload or {}).get("Line") or [{}])[0]
        record_bank_pushed(
            user_id=user_id, realm_id=realm_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("TxnDate") or (created or {}).get("TxnDate"),
            amount=extracted_total if extracted_total is not None else (line.get("Amount") or (created or {}).get("TotalAmt")),
            doc_number=None,
            party_name=(line.get("DepositLineDetail") or {}).get("Entity", {}).get("name") or line.get("Description"),
            qbo_entity_id=str((created or {}).get("Id")) if (created or {}).get("Id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}
    if entry_type == "purchase":
        res = await qbo_connector.create_purchase(user_id, payload, realm_id)
        record_from_bank_entry_payload(
            user_id=user_id, realm_id=realm_id, entry_type=entry_type, payload=payload,
        )
        created = res.get("Purchase", res) if isinstance(res, dict) else res
        line = ((payload or {}).get("Line") or [{}])[0]
        record_bank_pushed(
            user_id=user_id, realm_id=realm_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("TxnDate") or (created or {}).get("TxnDate"),
            amount=extracted_total if extracted_total is not None else (line.get("Amount") or (created or {}).get("TotalAmt")),
            doc_number=None,
            party_name=((payload or {}).get("EntityRef") or {}).get("name") or line.get("Description"),
            qbo_entity_id=str((created or {}).get("Id")) if (created or {}).get("Id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}
    if entry_type == "payment":
        # Payload shape decides which entity we hit: BillPayment if VendorRef
        # is set (vendor payment from bank), Payment if CustomerRef is set
        # (customer receipt). The frontend constructs the appropriate shape
        # when the user picks an open Bill or Invoice.
        if (payload or {}).get("VendorRef"):
            res = await qbo_connector.create_billpayment(user_id, payload, realm_id)
            created = res.get("BillPayment", res) if isinstance(res, dict) else res
            party_name = ((payload or {}).get("VendorRef") or {}).get("name")
        elif (payload or {}).get("CustomerRef"):
            res = await qbo_connector.create_payment(user_id, payload, realm_id)
            created = res.get("Payment", res) if isinstance(res, dict) else res
            party_name = ((payload or {}).get("CustomerRef") or {}).get("name")
        else:
            return {"type": entry_type, "ok": False, "error": "Payment requires VendorRef or CustomerRef"}
        record_bank_pushed(
            user_id=user_id, realm_id=realm_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("TxnDate") or (created or {}).get("TxnDate"),
            amount=extracted_total if extracted_total is not None else (payload or {}).get("TotalAmt") or (created or {}).get("TotalAmt"),
            doc_number=None,
            party_name=party_name,
            qbo_entity_id=str((created or {}).get("Id")) if (created or {}).get("Id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}
    if entry_type == "transfer":
        res = await qbo_connector.create_transfer(user_id, payload, realm_id)
        created = res.get("Transfer", res) if isinstance(res, dict) else res
        record_bank_pushed(
            user_id=user_id, realm_id=realm_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("TxnDate") or (created or {}).get("TxnDate"),
            amount=extracted_total if extracted_total is not None else (payload or {}).get("Amount") or (created or {}).get("Amount"),
            doc_number=None,
            party_name=((payload or {}).get("ToAccountRef") or {}).get("name"),
            qbo_entity_id=str((created or {}).get("Id")) if (created or {}).get("Id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}
    if entry_type == "journalentry":
        res = await qbo_connector.create_journalentry(user_id, payload, realm_id)
        created = res.get("JournalEntry", res) if isinstance(res, dict) else res
        line = ((payload or {}).get("Line") or [{}])[0]
        record_bank_pushed(
            user_id=user_id, realm_id=realm_id, doc_type=DOC_BANK,
            date_iso=(payload or {}).get("TxnDate") or (created or {}).get("TxnDate"),
            amount=extracted_total if extracted_total is not None else line.get("Amount"),
            doc_number=(payload or {}).get("DocNumber"),
            party_name=line.get("Description"),
            qbo_entity_id=str((created or {}).get("Id")) if (created or {}).get("Id") is not None else None,
        )
        return {"type": entry_type, "ok": True, "result": created}
    return {"type": entry_type, "ok": False, "error": f"Unknown entry type {entry_type!r}"}


async def _ensure_accounts_exist(
    user_id: str,
    realm_id: Optional[str],
    required: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Create any chart-of-accounts entries that don't exist yet — the QBO analog
    of Tally's ensure_ledgers_exist. ``required`` is a list of
    ``{"name", "account_type"}``. Returns created/failed lists and a
    name(lower) -> id map covering existing + newly-created accounts."""
    name_to_id: Dict[str, str] = {}
    created: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    if not required:
        return {"created": created, "failed": failed, "name_to_id": name_to_id}

    try:
        existing = await qbo_connector.list_accounts(user_id, realm_id)
    except Exception as exc:
        logger.warning("ensure_accounts_exist: cannot list accounts: %s", exc)
        existing = []
    for a in existing:
        nm = (a.get("Name") or "").strip().lower()
        if nm and a.get("Id") is not None:
            name_to_id[nm] = str(a.get("Id"))

    seen = set()
    for req in required:
        name = (req.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in name_to_id or key in seen:
            continue
        seen.add(key)
        acct_type = req.get("account_type") or "Expense"
        try:
            res = await qbo_connector.create_account(
                user_id, {"Name": name, "AccountType": acct_type}, realm_id,
            )
            acc = res.get("Account", res) if isinstance(res, dict) else res
            new_id = str(acc.get("Id")) if acc and acc.get("Id") is not None else None
            if new_id:
                name_to_id[key] = new_id
                created.append({"name": name, "id": new_id, "account_type": acct_type})
            else:
                failed.append({"name": name, "error": "QBO returned no account id"})
        except Exception as exc:
            logger.warning("ensure_accounts_exist: create '%s' failed: %s", name, exc)
            failed.append({"name": name, "error": str(exc)})

    return {"created": created, "failed": failed, "name_to_id": name_to_id}


async def push_bank_vouchers_batch(
    *,
    user_id: str,
    realm_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
    vouchers: List[Dict[str, Any]],
    auto_create_masters: bool = False,
) -> Dict[str, Any]:
    """Build each normalized voucher into a QBO payload and push it, recording
    history for next-time dedupe. The QBO analog of Tally's /push-vouchers-batch.

    The frontend sends the normalized contract; this is the ONLY place the
    payload is built on the write path — never the frontend.

    ``auto_create_masters`` mirrors Tally's auto_create_ledgers: any party ledger
    that names an account with no id is created first (Income for Receipts,
    Expense for Payments), then its id is back-filled before the push.
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
                        "account_type": "Income" if vt == "Receipt" else "Expense",
                    })
        ensure = await _ensure_accounts_exist(user_id, realm_id, required)
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
        # skip it and report, exactly like build_bank_vouchers_xml skips bad rows.
        try:
            entry_type, payload = build_qbo_bank_payload(
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
                realm_id=realm_id,
                entry_type=entry_type,
                payload=payload,
            )
        except Exception as exc:  # network / QBO API error for this row only
            failed += 1
            logger.exception("push_bank_vouchers_batch: row %d failed", i)
            results.append({"index": i, "ok": False, "type": entry_type, "error": str(exc)})
            continue

        if res.get("ok"):
            pushed += 1
            created = res.get("result") or {}
            results.append({
                "index": i, "ok": True, "type": entry_type,
                "id": str(created.get("Id")) if created.get("Id") is not None else None,
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
    realm_id: Optional[str],
    bank_account_id: str,
    bank_account_name: Optional[str] = None,
    vouchers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pre-push validation — the QBO analog of Tally's /validate-pre-push.

    Reports rows that can't be built (errors), party accounts the user typed
    that don't yet exist in QBO (missing_masters, for the create-account flow),
    and likely duplicates.
    """
    raw_accounts = await qbo_connector.list_accounts(user_id, realm_id)
    valid_ids = {str(a.get("Id")) for a in raw_accounts}

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
            errors.append({"index": i, "error": "bank account does not exist in QBO"})

        # Dry-run the builder to surface any other structural problem.
        try:
            build_qbo_bank_payload(
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
        user_id=user_id, realm_id=realm_id, rows=dup_inputs,
    )

    return {
        "ok": not errors and not missing_masters,
        "errors": errors,
        "missing_masters": missing_masters,
        "duplicates": duplicates,
    }
