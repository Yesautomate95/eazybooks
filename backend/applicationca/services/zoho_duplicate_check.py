"""
Duplicate-transaction detection for the three Zoho write flows (sales invoices,
purchase bills, bank entries). Mirrors qbo_duplicate_check.

Strategy is identical to QBO:
  1. Local check against our own `transaction_imports` table (date + amount).
  2. Authoritative check against Zoho for anything not flagged locally.

The Zoho check uses the list endpoints (no SQL dialect). We pass a server-side
date filter AND re-filter client-side by date + amount, so the result is correct
even if the exact Zoho filter param name needs tweaking (defensive — see VERIFY).

Match key: date + amount flags a possible duplicate; doc-number + party boosts
confidence. Confidence tiers + scoring are reused verbatim from the QBO logic.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.zoho_connector import zoho_connector

logger = logging.getLogger(__name__)


CONF_DEFINITE = 1.0
CONF_STRONG = 0.7
CONF_POSSIBLE = 0.4

DOC_SALES = "sales_invoice"
DOC_PURCHASE = "purchase_invoice"
DOC_BANK = "bank_statement"


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None


def _normalize_date(value: Any) -> Optional[str]:
    if not value:
        return None
    s = str(value)[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None


def _compose_hash(*, organization_id: str, doc_type: str, date_iso: str, amount: Decimal,
                  doc_number: Optional[str], party_name: Optional[str]) -> str:
    raw = "|".join([
        organization_id or "",
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
# Local check — query transaction_imports (company_name holds organization_id)
# ---------------------------------------------------------------------------

def _local_matches(
    *, user_id: str, organization_id: str, doc_type: str, date_iso: str, amount: Decimal,
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
            (user_id, organization_id, doc_type, date_iso, amount),
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
# Zoho check — query the org via list endpoints + client-side filter
# ---------------------------------------------------------------------------

# VERIFY: confirm Zoho list date-filter param names. We filter client-side
# regardless, so a wrong server param only affects performance, not correctness.
def _date_range_params(date_start: str, date_end: str) -> Dict[str, str]:
    return {"date_start": date_start, "date_end": date_end}


async def _zoho_invoices_in_range(user_id, organization_id, min_date, max_date) -> List[Dict[str, Any]]:
    rows = await zoho_connector.list_invoices(
        user_id, organization_id, params=_date_range_params(min_date, max_date)
    )
    return [{
        "entity_type": "Invoice",
        "entity_id": str(r.get("invoice_id")) if r.get("invoice_id") is not None else None,
        "doc_number": r.get("invoice_number"),
        "date": _normalize_date(r.get("date")),
        "amount": float(r.get("total") or 0),
        "party_name": r.get("customer_name"),
    } for r in rows]


async def _zoho_bills_in_range(user_id, organization_id, min_date, max_date) -> List[Dict[str, Any]]:
    rows = await zoho_connector.list_bills(
        user_id, organization_id, params=_date_range_params(min_date, max_date)
    )
    return [{
        "entity_type": "Bill",
        "entity_id": str(r.get("bill_id")) if r.get("bill_id") is not None else None,
        "doc_number": r.get("bill_number"),
        "date": _normalize_date(r.get("date")),
        "amount": float(r.get("total") or 0),
        "party_name": r.get("vendor_name"),
    } for r in rows]


async def _zoho_bank_txns_in_range(user_id, organization_id, min_date, max_date) -> List[Dict[str, Any]]:
    rows = await zoho_connector._paged_list(
        user_id, "/banktransactions", "banktransactions",
        organization_id=organization_id, params=_date_range_params(min_date, max_date),
        max_pages=10,
    )
    out = []
    for r in rows:
        out.append({
            "entity_type": (r.get("transaction_type") or "BankTransaction"),
            "entity_id": str(r.get("transaction_id")) if r.get("transaction_id") is not None else None,
            "doc_number": r.get("reference_number"),
            "date": _normalize_date(r.get("date")),
            "amount": float(r.get("amount") or 0),
            "party_name": r.get("payee") or r.get("vendor_name") or r.get("customer_name"),
            "is_credit": (r.get("transaction_type") or "").lower() in ("deposit", "customer_payment", "sales_without_invoices"),
        })
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
    *, user_id: str, organization_id: Optional[str], doc_date: Optional[str],
    total_amount: Any, doc_number: Optional[str] = None, customer_name: Optional[str] = None,
) -> Dict[str, Any]:
    return await _check_invoice_or_bill(
        user_id=user_id, organization_id=organization_id, doc_type=DOC_SALES,
        date_iso=_normalize_date(doc_date), amount=_to_decimal(total_amount),
        doc_number=doc_number, party_name=customer_name,
    )


async def check_bill(
    *, user_id: str, organization_id: Optional[str], doc_date: Optional[str],
    total_amount: Any, doc_number: Optional[str] = None, vendor_name: Optional[str] = None,
) -> Dict[str, Any]:
    return await _check_invoice_or_bill(
        user_id=user_id, organization_id=organization_id, doc_type=DOC_PURCHASE,
        date_iso=_normalize_date(doc_date), amount=_to_decimal(total_amount),
        doc_number=doc_number, party_name=vendor_name,
    )


async def _check_invoice_or_bill(
    *, user_id: str, organization_id: Optional[str], doc_type: str,
    date_iso: Optional[str], amount: Optional[Decimal],
    doc_number: Optional[str], party_name: Optional[str],
) -> Dict[str, Any]:
    if not date_iso or amount is None or amount == 0:
        return _no_check_result("Missing date or amount — cannot dedupe")

    org = organization_id or ""

    local_normalized = _local_normalize(_local_matches(
        user_id=user_id, organization_id=org, doc_type=doc_type, date_iso=date_iso, amount=amount,
    ))

    try:
        if doc_type == DOC_SALES:
            zoho_rows = await _zoho_invoices_in_range(user_id, organization_id, date_iso, date_iso)
        else:
            zoho_rows = await _zoho_bills_in_range(user_id, organization_id, date_iso, date_iso)
        zoho_rows = _filter_by_key(zoho_rows, date_iso, amount)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"zoho duplicate query failed: {exc}")
        zoho_rows = []
    for r in zoho_rows:
        r["source"] = "zoho"

    result = _score(matches=local_normalized + zoho_rows,
                    candidate_doc_number=doc_number, candidate_party_name=party_name)
    if result["is_duplicate"]:
        result["source"] = "local" if local_normalized else "zoho"
    else:
        result["source"] = "none"
    return result


async def check_bank_entry(
    *, user_id: str, organization_id: Optional[str], txn_date: Optional[str],
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
    org = organization_id or ""

    local_normalized = _local_normalize(_local_matches(
        user_id=user_id, organization_id=org, doc_type=DOC_BANK, date_iso=date_iso, amount=amount,
    ))

    try:
        zoho_all = await _zoho_bank_txns_in_range(user_id, organization_id, date_iso, date_iso)
        zoho_rows = [m for m in _filter_by_key(zoho_all, date_iso, amount) if bool(m.get("is_credit")) == is_credit]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"zoho bank duplicate query failed: {exc}")
        zoho_rows = []
    for r in zoho_rows:
        r["source"] = "zoho"

    result = _score(matches=local_normalized + zoho_rows,
                    candidate_doc_number=None, candidate_party_name=description)
    result["source"] = "local" if local_normalized else ("zoho" if zoho_rows else "none")
    return result


async def check_bank_entries_batch(
    *, user_id: str, organization_id: Optional[str], rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Batch dedupe for a whole bank statement — two Zoho range queries + one
    local query, then index by (date, amount) and look up per row in memory.

    Each ``rows`` entry needs: date_iso, amount, is_credit, description?.
    """
    if not rows:
        return []

    valid_dates = [r.get("date_iso") for r in rows if r.get("date_iso")]
    if not valid_dates:
        return [_no_check_result("Missing date — cannot dedupe") for _ in rows]

    min_d, max_d = min(valid_dates), max(valid_dates)
    org = organization_id or ""

    try:
        zoho_all = await _zoho_bank_txns_in_range(user_id, organization_id, min_d, max_d)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"batch zoho bank query failed: {exc}")
        zoho_all = []
    locals_ = _local_bank_entries_in_range(user_id=user_id, organization_id=org, min_date=min_d, max_date=max_d)

    zoho_idx: Dict[Tuple[Optional[str], Optional[str], bool], List[Dict[str, Any]]] = {}
    for m in zoho_all:
        zoho_idx.setdefault((m.get("date"), _amount_key(m.get("amount")), bool(m.get("is_credit"))), []).append(m)

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
        zoho_hits = [dict(m, source="zoho") for m in zoho_idx.get((date_iso, akey, is_credit), [])]

        result = _score(matches=local_hits + zoho_hits,
                        candidate_doc_number=None, candidate_party_name=row.get("description"))
        result["source"] = "local" if local_hits else ("zoho" if zoho_hits else "none")
        results.append(result)

    return results


def _local_bank_entries_in_range(*, user_id: str, organization_id: str, min_date: str, max_date: str) -> List[Dict[str, Any]]:
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
            (user_id, organization_id, DOC_BANK, min_date, max_date),
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
    *, user_id: str, organization_id: Optional[str], doc_type: str, date_iso: str,
    amount: Any, doc_number: Optional[str] = None, party_name: Optional[str] = None,
    zoho_entity_id: Optional[str] = None, extracted_data: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a just-pushed transaction so future dedupe checks find it. The
    `transaction_imports.tally_voucher_number` column stores the Zoho entity id
    (the column is platform-neutral despite its name)."""
    amount_dec = _to_decimal(amount)
    if not date_iso or amount_dec is None:
        return
    org = organization_id or ""
    txn_hash = _compose_hash(
        organization_id=org, doc_type=doc_type, date_iso=date_iso,
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
                user_id, org, doc_type, "(zoho push)",
                date_iso, doc_number, amount_dec, party_name,
                txn_hash, zoho_entity_id, Json(extracted_data or {}),
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
