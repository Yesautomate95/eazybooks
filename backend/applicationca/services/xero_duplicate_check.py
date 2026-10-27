"""
Duplicate-transaction detection for the three Xero write flows (sales invoices,
purchase bills, bank entries). Mirrors zoho_duplicate_check / qbo_duplicate_check.

Strategy is identical to QBO/Zoho:
  1. Local check against our own `transaction_imports` table (date + amount).
  2. Authoritative check against Xero for anything not flagged locally.

The Xero check uses the list endpoints (no SQL dialect). We pass a server-side
date filter via the Xero `where` query param AND re-filter client-side by date +
amount, so the result is correct even if the exact Xero filter syntax needs
tweaking (defensive — see VERIFY).

Match key: date + amount flags a possible duplicate; doc-number + party boosts
confidence. Confidence tiers + scoring are reused verbatim from the QBO logic.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.xero_connector import xero_connector

logger = logging.getLogger(__name__)


CONF_DEFINITE = 1.0
CONF_STRONG = 0.7
CONF_POSSIBLE = 0.4

DOC_SALES = "sales_invoice"
DOC_PURCHASE = "purchase_invoice"
DOC_BANK = "bank_statement"

# Xero serializes dates as "/Date(1599609600000+0000)/" (epoch ms, optional tz).
_XERO_DATE_RE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


def _parse_xero_date(value: Any) -> Optional[str]:
    """Parse a Xero date into YYYY-MM-DD.

    Xero RESPONSE dates look like "/Date(1599609600000+0000)/" (epoch ms). We
    also accept plain ISO strings (some endpoints already return YYYY-MM-DD...).
    """
    if not value:
        return None
    s = str(value)
    m = _XERO_DATE_RE.search(s)
    if m:
        try:
            dt = datetime.fromtimestamp(int(m.group(1)) / 1000.0, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            return None
    # Fall back to a plain ISO-ish prefix.
    s = s[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None


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


def _compose_hash(*, tenant_id: str, doc_type: str, date_iso: str, amount: Decimal,
                  doc_number: Optional[str], party_name: Optional[str]) -> str:
    raw = "|".join([
        tenant_id or "",
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
# Local check — query transaction_imports (company_name holds tenant_id)
# ---------------------------------------------------------------------------

def _local_matches(
    *, user_id: str, tenant_id: str, doc_type: str, date_iso: str, amount: Decimal,
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
            (user_id, tenant_id, doc_type, date_iso, amount),
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
# Xero check — query the tenant via list endpoints + client-side filter
# ---------------------------------------------------------------------------

# VERIFY: Xero `where` date-filter syntax — Xero expects
# DateTime(yyyy,mm,dd) literals, e.g. Date>=DateTime(2024,01,01). We filter
# client-side regardless, so a wrong server filter only affects performance,
# not correctness.
def _xero_date_clause(date_start: str, date_end: str) -> str:
    def _lit(d: str) -> str:
        y, m, day = d.split("-")
        return f"DateTime({int(y)},{int(m)},{int(day)})"
    return f"Date>={_lit(date_start)} && Date<=DateTime({int(date_end.split('-')[0])},{int(date_end.split('-')[1])},{int(date_end.split('-')[2])})"


def _invoice_where(type_clause: str, date_start: str, date_end: str) -> Dict[str, str]:
    # VERIFY Xero where date filter syntax.
    return {"where": f'{type_clause} && {_xero_date_clause(date_start, date_end)}'}


async def _xero_invoices_in_range(user_id, tenant_id, min_date, max_date) -> List[Dict[str, Any]]:
    rows = await xero_connector.list_invoices(
        user_id, tenant_id, params=_invoice_where('Type=="ACCREC"', min_date, max_date)
    )
    return [{
        "entity_type": "Invoice",
        "entity_id": str(r.get("InvoiceID")) if r.get("InvoiceID") is not None else None,
        "doc_number": r.get("InvoiceNumber"),
        "date": _parse_xero_date(r.get("Date")),
        "amount": float(r.get("Total") or 0),
        "party_name": (r.get("Contact") or {}).get("Name"),
    } for r in rows]


async def _xero_bills_in_range(user_id, tenant_id, min_date, max_date) -> List[Dict[str, Any]]:
    rows = await xero_connector.list_invoices(
        user_id, tenant_id, params=_invoice_where('Type=="ACCPAY"', min_date, max_date)
    )
    return [{
        "entity_type": "Bill",
        "entity_id": str(r.get("InvoiceID")) if r.get("InvoiceID") is not None else None,
        "doc_number": r.get("InvoiceNumber"),
        "date": _parse_xero_date(r.get("Date")),
        "amount": float(r.get("Total") or 0),
        "party_name": (r.get("Contact") or {}).get("Name"),
    } for r in rows]


async def _xero_bank_txns_in_range(user_id, tenant_id, min_date, max_date) -> List[Dict[str, Any]]:
    # VERIFY Xero where date filter syntax.
    rows = await xero_connector.list_bank_transactions(
        user_id, tenant_id, params={"where": _xero_date_clause(min_date, max_date)}
    )
    out = []
    for r in rows:
        txn_type = (r.get("Type") or "BankTransaction")
        out.append({
            "entity_type": txn_type,
            "entity_id": str(r.get("BankTransactionID")) if r.get("BankTransactionID") is not None else None,
            "doc_number": r.get("Reference"),
            "date": _parse_xero_date(r.get("Date")),
            "amount": float(r.get("Total") or 0),
            "party_name": (r.get("Contact") or {}).get("Name"),
            # RECEIVE = money in (credit/deposit); SPEND = money out (debit).
            "is_credit": str(txn_type).upper() == "RECEIVE",
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
    *, user_id: str, tenant_id: Optional[str], doc_date: Optional[str],
    total_amount: Any, doc_number: Optional[str] = None, customer_name: Optional[str] = None,
) -> Dict[str, Any]:
    return await _check_invoice_or_bill(
        user_id=user_id, tenant_id=tenant_id, doc_type=DOC_SALES,
        date_iso=_normalize_date(doc_date), amount=_to_decimal(total_amount),
        doc_number=doc_number, party_name=customer_name,
    )


async def check_bill(
    *, user_id: str, tenant_id: Optional[str], doc_date: Optional[str],
    total_amount: Any, doc_number: Optional[str] = None, vendor_name: Optional[str] = None,
) -> Dict[str, Any]:
    return await _check_invoice_or_bill(
        user_id=user_id, tenant_id=tenant_id, doc_type=DOC_PURCHASE,
        date_iso=_normalize_date(doc_date), amount=_to_decimal(total_amount),
        doc_number=doc_number, party_name=vendor_name,
    )


async def _check_invoice_or_bill(
    *, user_id: str, tenant_id: Optional[str], doc_type: str,
    date_iso: Optional[str], amount: Optional[Decimal],
    doc_number: Optional[str], party_name: Optional[str],
) -> Dict[str, Any]:
    if not date_iso or amount is None or amount == 0:
        return _no_check_result("Missing date or amount — cannot dedupe")

    tid = tenant_id or ""

    local_normalized = _local_normalize(_local_matches(
        user_id=user_id, tenant_id=tid, doc_type=doc_type, date_iso=date_iso, amount=amount,
    ))

    try:
        if doc_type == DOC_SALES:
            xero_rows = await _xero_invoices_in_range(user_id, tenant_id, date_iso, date_iso)
        else:
            xero_rows = await _xero_bills_in_range(user_id, tenant_id, date_iso, date_iso)
        xero_rows = _filter_by_key(xero_rows, date_iso, amount)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"xero duplicate query failed: {exc}")
        xero_rows = []
    for r in xero_rows:
        r["source"] = "xero"

    result = _score(matches=local_normalized + xero_rows,
                    candidate_doc_number=doc_number, candidate_party_name=party_name)
    if result["is_duplicate"]:
        result["source"] = "local" if local_normalized else "xero"
    else:
        result["source"] = "none"
    return result


async def check_bank_entry(
    *, user_id: str, tenant_id: Optional[str], txn_date: Optional[str],
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
    tid = tenant_id or ""

    local_normalized = _local_normalize(_local_matches(
        user_id=user_id, tenant_id=tid, doc_type=DOC_BANK, date_iso=date_iso, amount=amount,
    ))

    try:
        xero_all = await _xero_bank_txns_in_range(user_id, tenant_id, date_iso, date_iso)
        xero_rows = [m for m in _filter_by_key(xero_all, date_iso, amount) if bool(m.get("is_credit")) == is_credit]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"xero bank duplicate query failed: {exc}")
        xero_rows = []
    for r in xero_rows:
        r["source"] = "xero"

    result = _score(matches=local_normalized + xero_rows,
                    candidate_doc_number=None, candidate_party_name=description)
    result["source"] = "local" if local_normalized else ("xero" if xero_rows else "none")
    return result


async def check_bank_entries_batch(
    *, user_id: str, tenant_id: Optional[str], rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Batch dedupe for a whole bank statement — one Xero range query + one
    local query, then index by (date, amount) and look up per row in memory.

    Each ``rows`` entry needs: date_iso, amount, is_credit, description?.
    """
    if not rows:
        return []

    valid_dates = [r.get("date_iso") for r in rows if r.get("date_iso")]
    if not valid_dates:
        return [_no_check_result("Missing date — cannot dedupe") for _ in rows]

    min_d, max_d = min(valid_dates), max(valid_dates)
    tid = tenant_id or ""

    try:
        xero_all = await _xero_bank_txns_in_range(user_id, tenant_id, min_d, max_d)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"batch xero bank query failed: {exc}")
        xero_all = []
    locals_ = _local_bank_entries_in_range(user_id=user_id, tenant_id=tid, min_date=min_d, max_date=max_d)

    xero_idx: Dict[Tuple[Optional[str], Optional[str], bool], List[Dict[str, Any]]] = {}
    for m in xero_all:
        xero_idx.setdefault((m.get("date"), _amount_key(m.get("amount")), bool(m.get("is_credit"))), []).append(m)

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
        xero_hits = [dict(m, source="xero") for m in xero_idx.get((date_iso, akey, is_credit), [])]

        result = _score(matches=local_hits + xero_hits,
                        candidate_doc_number=None, candidate_party_name=row.get("description"))
        result["source"] = "local" if local_hits else ("xero" if xero_hits else "none")
        results.append(result)

    return results


def _local_bank_entries_in_range(*, user_id: str, tenant_id: str, min_date: str, max_date: str) -> List[Dict[str, Any]]:
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
            (user_id, tenant_id, DOC_BANK, min_date, max_date),
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
    *, user_id: str, tenant_id: Optional[str], doc_type: str, date_iso: str,
    amount: Any, doc_number: Optional[str] = None, party_name: Optional[str] = None,
    xero_entity_id: Optional[str] = None, extracted_data: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a just-pushed transaction so future dedupe checks find it. The
    `transaction_imports.tally_voucher_number` column stores the Xero entity id
    (the column is platform-neutral despite its name)."""
    amount_dec = _to_decimal(amount)
    if not date_iso or amount_dec is None:
        return
    tid = tenant_id or ""
    txn_hash = _compose_hash(
        tenant_id=tid, doc_type=doc_type, date_iso=date_iso,
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
                user_id, tid, doc_type, "(xero push)",
                date_iso, doc_number, amount_dec, party_name,
                txn_hash, xero_entity_id, Json(extracted_data or {}),
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
