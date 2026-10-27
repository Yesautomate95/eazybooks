"""
Duplicate-transaction detection for the three SAP write flows (sales invoices,
purchase bills, bank entries). Mirrors zoho_duplicate_check / xero_duplicate_check.

Strategy is identical to QBO/Zoho/Xero:
  1. Local check against our own `transaction_imports` table (date + amount).
  2. Authoritative check against SAP for anything not flagged locally.

The SAP check uses the open-item reads (no SQL dialect). SAP S/4HANA Cloud Public
Edition has no OData service that lists *posted* journal-entry headers cheaply, so
we query the open-item cube (open A/P supplier items for bills, open A/R customer
items for invoices) via sap_connector.list_bills / list_invoices / list_open_items
and re-filter client-side by date + amount. A row only carries into a match when it
shares the posting date AND the document amount — party / reference boosts
confidence. Confidence tiers + scoring are reused verbatim from the QBO logic.

NOTE vs Zoho/Xero (deviation, see report):
  * The company key is `company_code` (SAP Company Code), not organization_id /
    tenant_id. `transaction_imports.company_name` stores it (platform-neutral).
  * `transaction_imports.tally_voucher_number` stores the SAP AccountingDocument
    number (the column is platform-neutral despite its name).
  * SAP reads only surface OPEN items — a fully cleared (paid) duplicate will not
    appear in the SAP check. The local `transaction_imports` check still catches
    anything we pushed ourselves, which is the primary dedupe guarantee.

VERIFY flags below mark open-item-cube field names that research could only
confirm to medium confidence — we read several aliases defensively, and the
client-side date+amount re-filter keeps the result correct even if one field name
is off (it only affects which rows survive, not the scoring).
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.sap_connector import sap_connector

logger = logging.getLogger(__name__)


CONF_DEFINITE = 1.0
CONF_STRONG = 0.7
CONF_POSSIBLE = 0.4

DOC_SALES = "sales_invoice"
DOC_PURCHASE = "purchase_invoice"
DOC_BANK = "bank_statement"

# SAP open-item cube line-item codes for the customer (A/R) / supplier (A/P) views.
FIN_ACCT_TYPE_CUSTOMER = "D"   # A/R
FIN_ACCT_TYPE_SUPPLIER = "K"   # A/P

# Candidate field names on A_OperationalAcctgDocItemCube (VERIFY against $metadata).
# We read the first present value from each tuple so a single field-name miss does
# not break extraction.
_DATE_FIELDS = ("PostingDate", "DocumentDate", "NetDueDate", "ClearingDate")
_AMOUNT_FIELDS = (
    "AmountInCompanyCodeCurrency",
    "AmountInTransactionCurrency",
    "AbsoluteAmountInCoCodeCrcy",
    "AmountInBalanceTransacCrcy",
)
_DOC_NUMBER_FIELDS = ("DocumentReferenceID", "AccountingDocument", "OriginalReferenceDocument")
_PARTY_NAME_FIELDS = ("BusinessPartnerName", "SupplierName", "CustomerName")
_PARTY_ID_FIELDS = ("Supplier", "Customer", "BusinessPartner")
_ENTITY_ID_FIELDS = ("AccountingDocument", "DocumentReferenceID")


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None


def _normalize_date(value: Any) -> Optional[str]:
    """Normalize a date to YYYY-MM-DD.

    Accepts plain ISO strings and the OData V2 `/Date(epochMillis)/` form that
    SAP returns from some collections.
    """
    if not value:
        return None
    s = str(value)
    if s.startswith("/Date("):
        try:
            ms = int(s[len("/Date("):].split(")")[0].split("+")[0].split("-")[0] or "0")
            from datetime import datetime, timezone
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            return None
    s = s[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None


def _first(row: Dict[str, Any], fields: Tuple[str, ...]) -> Any:
    for f in fields:
        v = row.get(f)
        if v not in (None, ""):
            return v
    return None


def _compose_hash(*, company_code: str, doc_type: str, date_iso: str, amount: Decimal,
                  doc_number: Optional[str], party_name: Optional[str]) -> str:
    raw = "|".join([
        company_code or "",
        doc_type,
        date_iso,
        str(amount),
        (doc_number or "").strip().lower(),
        (party_name or "").strip().lower(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _amount_key(amount: Any) -> Optional[str]:
    d = _to_decimal(amount)
    return f"{d:.2f}" if d is not None else None


# ---------------------------------------------------------------------------
# Local check — query transaction_imports (company_name holds company_code)
# ---------------------------------------------------------------------------

def _local_matches(
    *, user_id: str, company_code: str, doc_type: str, date_iso: str, amount: Decimal,
) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT import_id, transaction_date, amount, reference_number,
                   party_name, debit_ledger, credit_ledger, status,
                   tally_voucher_number, created_at
              FROM transaction_imports
             WHERE user_id = %s
               AND company_name = %s
               AND document_type = %s
               AND transaction_date = %s::date
               AND ABS(amount - %s) < 0.01
            """,
            (user_id, company_code, doc_type, date_iso, amount),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"local duplicate query failed: {exc}")
        return []
    finally:
        return_connection(conn)


def _local_normalize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        "entity_type": "TransactionImport",
        "entity_id": str(r.get("import_id")),
        "doc_number": r.get("reference_number"),
        "date": str(r.get("transaction_date")) if r.get("transaction_date") else None,
        "amount": float(r.get("amount") or 0),
        "party_name": r.get("party_name"),
        "source": "local",
    } for r in rows]


# ---------------------------------------------------------------------------
# SAP check — query open items via the connector + client-side filter
# ---------------------------------------------------------------------------

def _normalize_open_item(r: Dict[str, Any], entity_type: str) -> Dict[str, Any]:
    party_name = _first(r, _PARTY_NAME_FIELDS)
    party_id = _first(r, _PARTY_ID_FIELDS)
    return {
        "entity_type": entity_type,
        "entity_id": str(_first(r, _ENTITY_ID_FIELDS)) if _first(r, _ENTITY_ID_FIELDS) is not None else None,
        "doc_number": _first(r, _DOC_NUMBER_FIELDS),
        "date": _normalize_date(_first(r, _DATE_FIELDS)),
        # Open-item amounts can be negative (credit). We compare absolute value.
        "amount": abs(float(_to_decimal(_first(r, _AMOUNT_FIELDS)) or 0)),
        # Prefer the human name; fall back to the BP id so party scoring can still fire.
        "party_name": party_name or (str(party_id) if party_id is not None else None),
        "party_id": str(party_id) if party_id is not None else None,
    }


async def _sap_invoices_in_range(user_id, company_code, min_date, max_date) -> List[Dict[str, Any]]:
    """Open A/R (customer) items — analogue of zoho/xero list_invoices.

    SAP open-item reads ignore a server-side date filter (the cube filter is built
    by the connector); we re-filter client-side in _filter_by_key, so min_date /
    max_date are advisory only.
    """
    rows = await sap_connector.list_invoices(user_id, company_code)
    return [_normalize_open_item(r, "Invoice") for r in rows]


async def _sap_bills_in_range(user_id, company_code, min_date, max_date) -> List[Dict[str, Any]]:
    """Open A/P (supplier) items — analogue of zoho/xero list_bills."""
    rows = await sap_connector.list_bills(user_id, company_code)
    return [_normalize_open_item(r, "Bill") for r in rows]


async def _sap_bank_txns_in_range(user_id, company_code, min_date, max_date) -> List[Dict[str, Any]]:
    """Open items on both ledgers, used for bank-entry dedupe. SAP FI has no
    separate "bank transaction" feed analogous to Zoho/Xero — a bank entry posts a
    journal entry that lands as open A/R or A/P items, so we union both views and
    derive `is_credit` from the financial-account type (D=customer receipt=credit,
    K=supplier payment=debit). VERIFY the directionality against your posting flow.
    """
    out: List[Dict[str, Any]] = []
    try:
        ar = await sap_connector.list_open_items(
            user_id, company_code, financial_account_type=FIN_ACCT_TYPE_CUSTOMER,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"sap open A/R query failed: {exc}")
        ar = []
    try:
        ap = await sap_connector.list_open_items(
            user_id, company_code, financial_account_type=FIN_ACCT_TYPE_SUPPLIER,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"sap open A/P query failed: {exc}")
        ap = []
    for r in ar:
        item = _normalize_open_item(r, "CustomerOpenItem")
        item["is_credit"] = True   # money in
        out.append(item)
    for r in ap:
        item = _normalize_open_item(r, "SupplierOpenItem")
        item["is_credit"] = False  # money out
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Confidence scoring (verbatim from the QBO logic)
# ---------------------------------------------------------------------------

def _score(*, matches: List[Dict[str, Any]], candidate_doc_number: Optional[str],
           candidate_party_name: Optional[str]) -> Dict[str, Any]:
    if not matches:
        return {"is_duplicate": False, "confidence": 0.0,
                "reason": "No matching transaction found", "matches": []}

    cand_doc = (candidate_doc_number or "").strip().lower() or None
    cand_party = (candidate_party_name or "").strip().lower() or None

    best = None
    best_conf = 0.0
    best_reason = ""
    for m in matches:
        m_doc = (m.get("doc_number") or "").strip().lower() or None
        m_party = (m.get("party_name") or "").strip().lower() or None
        if cand_doc and m_doc and cand_doc == m_doc and cand_party and m_party and cand_party == m_party:
            conf = CONF_DEFINITE
            reason = "Exact match: same date, amount, doc # and party"
        elif (cand_doc and m_doc and cand_doc == m_doc) or (cand_party and m_party and cand_party == m_party):
            conf = CONF_STRONG
            reason = "Strong match: same date, amount and " + ("doc #" if cand_doc and m_doc and cand_doc == m_doc else "party")
        else:
            conf = CONF_POSSIBLE
            reason = "Possible match: same date and amount only"
        if conf > best_conf:
            best_conf = conf
            best = m
            best_reason = reason

    return {"is_duplicate": True, "confidence": best_conf, "reason": best_reason,
            "matches": matches[:5], "best_match": best}


def _filter_by_key(rows: List[Dict[str, Any]], date_iso: str, amount: Decimal) -> List[Dict[str, Any]]:
    akey = f"{amount:.2f}"
    return [r for r in rows if r.get("date") == date_iso and _amount_key(r.get("amount")) == akey]


def _no_check_result(reason: str) -> Dict[str, Any]:
    return {"is_duplicate": False, "confidence": 0.0, "source": "skipped", "reason": reason, "matches": []}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def check_invoice(
    *, user_id: str, company_code: Optional[str], doc_date: Optional[str],
    total_amount: Any, doc_number: Optional[str] = None, customer_name: Optional[str] = None,
) -> Dict[str, Any]:
    return await _check_invoice_or_bill(
        user_id=user_id, company_code=company_code, doc_type=DOC_SALES,
        date_iso=_normalize_date(doc_date), amount=_to_decimal(total_amount),
        doc_number=doc_number, party_name=customer_name,
    )


async def check_bill(
    *, user_id: str, company_code: Optional[str], doc_date: Optional[str],
    total_amount: Any, doc_number: Optional[str] = None, vendor_name: Optional[str] = None,
) -> Dict[str, Any]:
    return await _check_invoice_or_bill(
        user_id=user_id, company_code=company_code, doc_type=DOC_PURCHASE,
        date_iso=_normalize_date(doc_date), amount=_to_decimal(total_amount),
        doc_number=doc_number, party_name=vendor_name,
    )


# Alias kept for the original SAP stub contract (check_supplier_invoice). Maps the
# A/P supplier-invoice dedupe onto check_bill so either name works downstream.
async def check_supplier_invoice(
    *, user_id: str, company_code: Optional[str], doc_date: Optional[str],
    total_amount: Any, doc_number: Optional[str] = None, vendor_name: Optional[str] = None,
) -> Dict[str, Any]:
    return await check_bill(
        user_id=user_id, company_code=company_code, doc_date=doc_date,
        total_amount=total_amount, doc_number=doc_number, vendor_name=vendor_name,
    )


async def _check_invoice_or_bill(
    *, user_id: str, company_code: Optional[str], doc_type: str,
    date_iso: Optional[str], amount: Optional[Decimal],
    doc_number: Optional[str], party_name: Optional[str],
) -> Dict[str, Any]:
    if not date_iso or amount is None or amount == 0:
        return _no_check_result("Missing date or amount — cannot dedupe")

    cc = company_code or ""

    local_normalized = _local_normalize(_local_matches(
        user_id=user_id, company_code=cc, doc_type=doc_type, date_iso=date_iso, amount=amount,
    ))

    sap_rows: List[Dict[str, Any]] = []
    if company_code:
        try:
            if doc_type == DOC_SALES:
                sap_rows = await _sap_invoices_in_range(user_id, company_code, date_iso, date_iso)
            else:
                sap_rows = await _sap_bills_in_range(user_id, company_code, date_iso, date_iso)
            sap_rows = _filter_by_key(sap_rows, date_iso, amount)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"sap duplicate query failed: {exc}")
            sap_rows = []
    for r in sap_rows:
        r["source"] = "sap"

    result = _score(matches=local_normalized + sap_rows,
                    candidate_doc_number=doc_number, candidate_party_name=party_name)
    if result["is_duplicate"]:
        result["source"] = "local" if local_normalized else "sap"
    else:
        result["source"] = "none"
    return result


async def check_bank_entry(
    *, user_id: str, company_code: Optional[str], txn_date: Optional[str],
    debit_amount: Any = None, credit_amount: Any = None,
    description: Optional[str] = None, bank_account_id: Optional[str] = None,
) -> Dict[str, Any]:
    date_iso = _normalize_date(txn_date)
    debit = _to_decimal(debit_amount)
    credit = _to_decimal(credit_amount)
    if not date_iso or ((debit is None or debit == 0) and (credit is None or credit == 0)):
        return _no_check_result("Missing date or amount — cannot dedupe")

    is_credit = bool(credit) and (not debit)
    amount = credit if is_credit else debit
    cc = company_code or ""

    local_normalized = _local_normalize(_local_matches(
        user_id=user_id, company_code=cc, doc_type=DOC_BANK, date_iso=date_iso, amount=amount,
    ))

    sap_rows: List[Dict[str, Any]] = []
    if company_code:
        try:
            sap_all = await _sap_bank_txns_in_range(user_id, company_code, date_iso, date_iso)
            sap_rows = [m for m in _filter_by_key(sap_all, date_iso, amount) if bool(m.get("is_credit")) == is_credit]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"sap bank duplicate query failed: {exc}")
            sap_rows = []
    for r in sap_rows:
        r["source"] = "sap"

    result = _score(matches=local_normalized + sap_rows,
                    candidate_doc_number=None, candidate_party_name=description)
    result["source"] = "local" if local_normalized else ("sap" if sap_rows else "none")
    return result


async def check_bank_entries_batch(
    *, user_id: str, company_code: Optional[str], rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Batch dedupe for a whole bank statement — one SAP open-item pull (A/R + A/P)
    + one local query, then index by (date, amount) and look up per row in memory.

    Each ``rows`` entry needs: date_iso, amount, is_credit, description?.
    """
    if not rows:
        return []

    valid_dates = [r.get("date_iso") for r in rows if r.get("date_iso")]
    if not valid_dates:
        return [_no_check_result("Missing date — cannot dedupe") for _ in rows]

    min_d, max_d = min(valid_dates), max(valid_dates)
    cc = company_code or ""

    sap_all: List[Dict[str, Any]] = []
    if company_code:
        try:
            sap_all = await _sap_bank_txns_in_range(user_id, company_code, min_d, max_d)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"batch sap bank query failed: {exc}")
            sap_all = []
    locals_ = _local_bank_entries_in_range(user_id=user_id, company_code=cc, min_date=min_d, max_date=max_d)

    sap_idx: Dict[Tuple[Optional[str], Optional[str], bool], List[Dict[str, Any]]] = {}
    for m in sap_all:
        sap_idx.setdefault((m.get("date"), _amount_key(m.get("amount")), bool(m.get("is_credit"))), []).append(m)

    local_idx: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]] = {}
    for r in locals_:
        d = str(r.get("transaction_date")) if r.get("transaction_date") else None
        local_idx.setdefault((d, _amount_key(r.get("amount"))), []).append(r)

    results: List[Dict[str, Any]] = []
    for row in rows:
        date_iso = row.get("date_iso")
        amt = _to_decimal(row.get("amount"))
        if not date_iso or amt is None or amt == 0:
            results.append(_no_check_result("Missing date or amount — cannot dedupe"))
            continue
        is_credit = bool(row.get("is_credit"))
        akey = f"{amt:.2f}"

        local_hits = _local_normalize(local_idx.get((date_iso, akey), []))
        sap_hits = [dict(m, source="sap") for m in sap_idx.get((date_iso, akey, is_credit), [])]

        result = _score(matches=local_hits + sap_hits,
                        candidate_doc_number=None, candidate_party_name=row.get("description"))
        result["source"] = "local" if local_hits else ("sap" if sap_hits else "none")
        results.append(result)

    return results


def _local_bank_entries_in_range(*, user_id: str, company_code: str, min_date: str, max_date: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT import_id, transaction_date, amount, reference_number,
                   party_name, status, created_at
              FROM transaction_imports
             WHERE user_id = %s
               AND company_name = %s
               AND document_type = %s
               AND transaction_date BETWEEN %s::date AND %s::date
            """,
            (user_id, company_code, DOC_BANK, min_date, max_date),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"batch local duplicate query failed: {exc}")
        return []
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------
# After a successful push — record so we catch it locally next time.
# ---------------------------------------------------------------------------

def record_pushed(
    *, user_id: str, company_code: Optional[str], doc_type: str, date_iso: str,
    amount: Any, doc_number: Optional[str] = None, party_name: Optional[str] = None,
    sap_entity_id: Optional[str] = None, extracted_data: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a just-pushed transaction so future dedupe checks find it. The
    `transaction_imports.tally_voucher_number` column stores the SAP entity id
    (the AccountingDocument number — the column is platform-neutral despite its
    name)."""
    amount_dec = _to_decimal(amount)
    if not date_iso or amount_dec is None:
        return
    cc = company_code or ""
    txn_hash = _compose_hash(
        company_code=cc, doc_type=doc_type, date_iso=date_iso,
        amount=amount_dec, doc_number=doc_number, party_name=party_name,
    )

    conn = get_connection()
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO transaction_imports
                (user_id, company_name, document_type, original_filename,
                 transaction_date, reference_number, amount, party_name,
                 transaction_hash, status, tally_voucher_number, extracted_data)
            VALUES (%s, %s, %s, %s, %s::date, %s, %s, %s, %s, 'pushed', %s, %s)
            ON CONFLICT (user_id, company_name, transaction_hash) DO UPDATE
                SET status = 'pushed',
                    tally_voucher_number = COALESCE(EXCLUDED.tally_voucher_number, transaction_imports.tally_voucher_number),
                    pushed_at = CURRENT_TIMESTAMP
            """,
            (
                user_id, cc, doc_type, "(sap push)",
                date_iso, doc_number, amount_dec, party_name,
                txn_hash, sap_entity_id, Json(extracted_data or {}),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"record_pushed failed: {exc}")
    finally:
        return_connection(conn)
