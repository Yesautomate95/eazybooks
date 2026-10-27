"""
Duplicate-transaction detection for the three QB write flows:
  - Sales invoices
  - Purchase bills
  - Bank entries (deposits / cash purchases)

Strategy (per the product decision):
  1. **Local check first** — query our own `transaction_imports` table for
     anything matching by date + amount in the same realm. Cheap, no QBO
     round-trip. Only catches transactions imported through this product.
  2. **QBO check** for anything not flagged locally — run a SELECT against
     QBO for the relevant entity (Invoice / Bill / Purchase / Deposit) at the
     same TxnDate + TotalAmt. Authoritative, catches transactions entered
     directly in QBO too.

Match key (looser per product decision): date + amount alone is enough to
flag a duplicate. Doc-number + party match boosts confidence to "definite"
when present, otherwise it stays a "possible" hit.

Returned shape per input row:
    {
        "is_duplicate": bool,
        "confidence": float,           # 1.0 = definite, 0.7 = strong, 0.4 = possible
        "source": "local" | "qbo" | "none",
        "reason": str,                 # human-readable
        "matches": [                   # 0-N existing matches
            {
                "qbo_entity_type": "Invoice" | "Bill" | "Purchase" | "Deposit",
                "qbo_id": str | None,
                "doc_number": str | None,
                "date": str | None,
                "amount": float | None,
                "party_name": str | None,
            }
        ],
    }

The caller (extractors) attaches this dict under `duplicate_check` on each
extracted row, so the frontend can render a badge and gate the push.

After a successful push, the controllers also call `record_pushed_*` so the
same row never re-appears as new on the next extract.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from applicationca.services.qbo_connector import qbo_connector

logger = logging.getLogger(__name__)


# Confidence tiers. The frontend uses these to colour the badge.
CONF_DEFINITE = 1.0   # date + amount + doc number + party
CONF_STRONG = 0.7     # date + amount + party (or + doc number)
CONF_POSSIBLE = 0.4   # date + amount only

# Document_type values in `transaction_imports`.
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
    """Return YYYY-MM-DD or None. QBO and our extractors both produce iso
    strings, dates, or sometimes None."""
    if not value:
        return None
    s = str(value)[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None


def _compose_hash(*, realm_id: str, doc_type: str, date_iso: str, amount: Decimal,
                  doc_number: Optional[str], party_name: Optional[str]) -> str:
    """Deterministic hash used both to record a pushed transaction and to
    look it up later. Includes realm so two realms don't collide."""
    raw = "|".join([
        realm_id or "",
        doc_type,
        date_iso,
        str(amount),
        (doc_number or "").strip().lower(),
        (party_name or "").strip().lower(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Local check — query transaction_imports
# ---------------------------------------------------------------------------

def _local_matches(
    *,
    user_id: str,
    realm_id: str,
    doc_type: str,
    date_iso: str,
    amount: Decimal,
) -> List[Dict[str, Any]]:
    """Return rows from transaction_imports matching the looser key (date +
    amount). Caller can boost confidence by checking doc_number / party_name
    on the returned rows."""
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
            (user_id, realm_id, doc_type, date_iso, amount),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"local duplicate query failed: {exc}")
        return []
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------
# QBO check — query the realm
# ---------------------------------------------------------------------------

_QBO_ENTITY_BY_DOC = {
    DOC_SALES: "Invoice",
    DOC_PURCHASE: "Bill",
}


async def _qbo_matches_invoice_or_bill(
    *,
    user_id: str,
    realm_id: Optional[str],
    doc_type: str,
    date_iso: str,
    amount: Decimal,
) -> List[Dict[str, Any]]:
    entity = _QBO_ENTITY_BY_DOC.get(doc_type)
    if not entity:
        return []
    # QBO SELECT supports TxnDate = 'YYYY-MM-DD' and TotalAmt = N.NN
    sql = (
        f"SELECT Id, DocNumber, TxnDate, TotalAmt, "
        f"{'CustomerRef' if entity == 'Invoice' else 'VendorRef'} "
        f"FROM {entity} WHERE TxnDate = '{date_iso}' AND TotalAmt = '{amount}' "
        f"MAXRESULTS 20"
    )
    try:
        data = await qbo_connector.query(user_id, sql, realm_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"qbo duplicate query failed ({entity}): {exc}")
        return []
    rows = ((data or {}).get("QueryResponse") or {}).get(entity) or []
    return [_normalize_qbo_invoice_row(r, entity) for r in rows]


def _normalize_qbo_invoice_row(row: Dict[str, Any], entity: str) -> Dict[str, Any]:
    ref = row.get("CustomerRef") if entity == "Invoice" else row.get("VendorRef")
    return {
        "qbo_entity_type": entity,
        "qbo_id": str(row.get("Id")) if row.get("Id") is not None else None,
        "doc_number": row.get("DocNumber"),
        "date": row.get("TxnDate"),
        "amount": float(row.get("TotalAmt") or 0),
        "party_name": (ref or {}).get("name") if isinstance(ref, dict) else None,
    }


async def _qbo_matches_bank_entry(
    *,
    user_id: str,
    realm_id: Optional[str],
    date_iso: str,
    amount: Decimal,
    bank_account_id: Optional[str],
    is_credit: bool,
) -> List[Dict[str, Any]]:
    """Bank-entry duplicates: credits == Deposit, debits == Purchase. QBO
    stores both as TxnDate + TotalAmt searchable rows."""
    entity = "Deposit" if is_credit else "Purchase"
    sql = (
        f"SELECT Id, TxnDate, TotalAmt, PrivateNote "
        f"FROM {entity} WHERE TxnDate = '{date_iso}' AND TotalAmt = '{amount}' "
        f"MAXRESULTS 20"
    )
    try:
        data = await qbo_connector.query(user_id, sql, realm_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"qbo duplicate query failed ({entity}): {exc}")
        return []
    rows = ((data or {}).get("QueryResponse") or {}).get(entity) or []
    out = []
    for r in rows:
        out.append({
            "qbo_entity_type": entity,
            "qbo_id": str(r.get("Id")) if r.get("Id") is not None else None,
            "doc_number": None,
            "date": r.get("TxnDate"),
            "amount": float(r.get("TotalAmt") or 0),
            "party_name": r.get("PrivateNote") or None,
        })
    return out


# ---------------------------------------------------------------------------
# Confidence scoring (combines local + qbo matches into one verdict)
# ---------------------------------------------------------------------------

def _score(
    *,
    matches: List[Dict[str, Any]],
    candidate_doc_number: Optional[str],
    candidate_party_name: Optional[str],
) -> Dict[str, Any]:
    """Pick the best match and assign a confidence tier."""
    if not matches:
        return {
            "is_duplicate": False,
            "confidence": 0.0,
            "reason": "No matching transaction found",
            "matches": [],
        }

    cand_doc = (candidate_doc_number or "").strip().lower() or None
    cand_party = (candidate_party_name or "").strip().lower() or None

    best = None
    best_conf = 0.0
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

    return {
        "is_duplicate": True,
        "confidence": best_conf,
        "reason": best_reason,
        "matches": matches[:5],
        "best_match": best,
    }


# ---------------------------------------------------------------------------
# Batch bank-entries dup check
#
# A statement can carry hundreds of rows. Calling check_bank_entry once per
# row (each fires one QBO query) makes the extract step pathologically slow
# — sandbox latency × N rows easily blows past the frontend's 180s axios
# timeout. Instead, pull every Deposit + Purchase in the row date-range with
# two QBO queries total, plus one local DB query, then index by
# (date, amount) and look up per row in memory.
# ---------------------------------------------------------------------------

def _amount_key(amount: Any) -> Optional[str]:
    d = _to_decimal(amount)
    return f"{d:.2f}" if d is not None else None


async def _qbo_bank_entries_in_range(
    *,
    user_id: str,
    realm_id: Optional[str],
    min_date_iso: str,
    max_date_iso: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """One QBO call each for Deposit + Purchase across the full date range.
    Fired concurrently. Returns (deposit_rows, purchase_rows) already
    normalized into the match-dict shape used elsewhere."""

    async def _q(entity: str) -> List[Dict[str, Any]]:
        sql = (
            f"SELECT Id, TxnDate, TotalAmt, PrivateNote FROM {entity} "
            f"WHERE TxnDate >= '{min_date_iso}' AND TxnDate <= '{max_date_iso}' "
            f"MAXRESULTS 1000"
        )
        try:
            data = await qbo_connector.query(user_id, sql, realm_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"batch qbo duplicate query failed ({entity}): {exc}")
            return []
        rows = ((data or {}).get("QueryResponse") or {}).get(entity) or []
        if len(rows) >= 1000:
            logger.warning(
                f"batch qbo duplicate query hit MAXRESULTS=1000 for {entity} "
                f"between {min_date_iso}..{max_date_iso} — duplicate detection "
                "may miss matches beyond the first page"
            )
        return [{
            "qbo_entity_type": entity,
            "qbo_id": str(r.get("Id")) if r.get("Id") is not None else None,
            "doc_number": None,
            "date": r.get("TxnDate"),
            "amount": float(r.get("TotalAmt") or 0),
            "party_name": r.get("PrivateNote") or None,
        } for r in rows]

    deposits, purchases = await asyncio.gather(_q("Deposit"), _q("Purchase"))
    return deposits, purchases


def _local_bank_entries_in_range(
    *,
    user_id: str,
    realm_id: str,
    min_date_iso: str,
    max_date_iso: str,
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
               AND transaction_date BETWEEN %s::date AND %s::date
            """,
            (user_id, realm_id, DOC_BANK, min_date_iso, max_date_iso),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"batch local duplicate query failed: {exc}")
        return []
    finally:
        return_connection(conn)


async def check_bank_entries_batch(
    *,
    user_id: str,
    realm_id: Optional[str],
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Batch duplicate check for a whole bank statement.

    Each ``rows`` entry must contain: ``date_iso`` (YYYY-MM-DD or None),
    ``amount`` (positive number), ``is_credit`` (bool — credit=Deposit,
    debit=Purchase), ``description`` (optional).

    Returns one dup-check dict per input row, in input order, with the
    same shape that ``check_bank_entry`` produces.
    """
    if not rows:
        return []

    results: List[Optional[Dict[str, Any]]] = [None] * len(rows)
    valid_dates = [r.get("date_iso") for r in rows if r.get("date_iso")]
    if not valid_dates:
        return [_no_check_result("Missing date — cannot dedupe") for _ in rows]

    min_d, max_d = min(valid_dates), max(valid_dates)
    realm = realm_id or ""

    deposits, purchases = await _qbo_bank_entries_in_range(
        user_id=user_id, realm_id=realm_id,
        min_date_iso=min_d, max_date_iso=max_d,
    )
    locals_ = _local_bank_entries_in_range(
        user_id=user_id, realm_id=realm,
        min_date_iso=min_d, max_date_iso=max_d,
    )

    deposit_idx: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]] = {}
    for m in deposits:
        deposit_idx.setdefault((m.get("date"), _amount_key(m.get("amount"))), []).append(m)
    purchase_idx: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]] = {}
    for m in purchases:
        purchase_idx.setdefault((m.get("date"), _amount_key(m.get("amount"))), []).append(m)

    local_idx: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]] = {}
    for r in locals_:
        d = str(r.get("transaction_date")) if r.get("transaction_date") else None
        local_idx.setdefault((d, _amount_key(r.get("amount"))), []).append(r)

    for i, row in enumerate(rows):
        date_iso = row.get("date_iso")
        amt = _to_decimal(row.get("amount"))
        if not date_iso or amt is None or amt == 0:
            results[i] = _no_check_result("Missing date or amount — cannot dedupe")
            continue

        key = (date_iso, f"{amt:.2f}")
        local_hits = local_idx.get(key, [])
        local_normalized = [{
            "qbo_entity_type": "TransactionImport",
            "qbo_id": str(r.get("import_id")),
            "doc_number": r.get("reference_number"),
            "date": str(r.get("transaction_date")) if r.get("transaction_date") else None,
            "amount": float(r.get("amount") or 0),
            "party_name": r.get("party_name"),
            "source": "local",
        } for r in local_hits]

        idx = deposit_idx if row.get("is_credit") else purchase_idx
        qbo_hits_raw = idx.get(key, [])
        qbo_hits = [dict(m, source="qbo") for m in qbo_hits_raw]

        result = _score(
            matches=local_normalized + qbo_hits,
            candidate_doc_number=None,
            candidate_party_name=row.get("description"),
        )
        result["source"] = "local" if local_normalized else ("qbo" if qbo_hits else "none")
        results[i] = result

    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def check_invoice(
    *,
    user_id: str,
    realm_id: Optional[str],
    doc_date: Optional[str],
    total_amount: Any,
    doc_number: Optional[str] = None,
    customer_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Duplicate check for one extracted sales invoice."""
    return await _check_invoice_or_bill(
        user_id=user_id, realm_id=realm_id, doc_type=DOC_SALES,
        date_iso=_normalize_date(doc_date),
        amount=_to_decimal(total_amount),
        doc_number=doc_number,
        party_name=customer_name,
    )


async def check_bill(
    *,
    user_id: str,
    realm_id: Optional[str],
    doc_date: Optional[str],
    total_amount: Any,
    doc_number: Optional[str] = None,
    vendor_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Duplicate check for one extracted purchase bill."""
    return await _check_invoice_or_bill(
        user_id=user_id, realm_id=realm_id, doc_type=DOC_PURCHASE,
        date_iso=_normalize_date(doc_date),
        amount=_to_decimal(total_amount),
        doc_number=doc_number,
        party_name=vendor_name,
    )


async def _check_invoice_or_bill(
    *,
    user_id: str, realm_id: Optional[str], doc_type: str,
    date_iso: Optional[str], amount: Optional[Decimal],
    doc_number: Optional[str], party_name: Optional[str],
) -> Dict[str, Any]:
    if not date_iso or amount is None or amount == 0:
        return _no_check_result("Missing date or amount — cannot dedupe")

    realm = realm_id or ""

    # Local first.
    locals_ = _local_matches(
        user_id=user_id, realm_id=realm, doc_type=doc_type,
        date_iso=date_iso, amount=amount,
    )
    local_normalized = [{
        "qbo_entity_type": "TransactionImport",
        "qbo_id": str(r.get("import_id")),
        "doc_number": r.get("reference_number"),
        "date": str(r.get("transaction_date")) if r.get("transaction_date") else None,
        "amount": float(r.get("amount") or 0),
        "party_name": r.get("party_name"),
        "source": "local",
    } for r in locals_]

    # QBO second.
    qbo_rows = await _qbo_matches_invoice_or_bill(
        user_id=user_id, realm_id=realm_id, doc_type=doc_type,
        date_iso=date_iso, amount=amount,
    )
    for r in qbo_rows:
        r["source"] = "qbo"

    result = _score(
        matches=local_normalized + qbo_rows,
        candidate_doc_number=doc_number,
        candidate_party_name=party_name,
    )
    if result["is_duplicate"]:
        # Prefer local source if both fired (cheaper to render link, also more
        # specific — "you already imported this through us").
        result["source"] = "local" if local_normalized else "qbo"
    else:
        result["source"] = "none"
    return result


async def check_bank_entry(
    *,
    user_id: str,
    realm_id: Optional[str],
    txn_date: Optional[str],
    debit_amount: Any = None,
    credit_amount: Any = None,
    description: Optional[str] = None,
    bank_account_id: Optional[str] = None,
) -> Dict[str, Any]:
    date_iso = _normalize_date(txn_date)
    debit = _to_decimal(debit_amount)
    credit = _to_decimal(credit_amount)
    if not date_iso or ((debit is None or debit == 0) and (credit is None or credit == 0)):
        return _no_check_result("Missing date or amount — cannot dedupe")

    is_credit = bool(credit) and (not debit)
    amount = credit if is_credit else debit
    realm = realm_id or ""

    locals_ = _local_matches(
        user_id=user_id, realm_id=realm, doc_type=DOC_BANK,
        date_iso=date_iso, amount=amount,
    )
    local_normalized = [{
        "qbo_entity_type": "TransactionImport",
        "qbo_id": str(r.get("import_id")),
        "doc_number": r.get("reference_number"),
        "date": str(r.get("transaction_date")) if r.get("transaction_date") else None,
        "amount": float(r.get("amount") or 0),
        "party_name": r.get("party_name"),
        "source": "local",
    } for r in locals_]

    qbo_rows = await _qbo_matches_bank_entry(
        user_id=user_id, realm_id=realm_id,
        date_iso=date_iso, amount=amount,
        bank_account_id=bank_account_id, is_credit=is_credit,
    )
    for r in qbo_rows:
        r["source"] = "qbo"

    result = _score(
        matches=local_normalized + qbo_rows,
        candidate_doc_number=None,
        candidate_party_name=description,
    )
    result["source"] = "local" if local_normalized else ("qbo" if qbo_rows else "none")
    return result


def _no_check_result(reason: str) -> Dict[str, Any]:
    return {
        "is_duplicate": False,
        "confidence": 0.0,
        "source": "skipped",
        "reason": reason,
        "matches": [],
    }


# ---------------------------------------------------------------------------
# After a successful push — record so we catch it locally next time.
# ---------------------------------------------------------------------------

def record_pushed(
    *,
    user_id: str,
    realm_id: Optional[str],
    doc_type: str,
    date_iso: str,
    amount: Any,
    doc_number: Optional[str] = None,
    party_name: Optional[str] = None,
    qbo_entity_id: Optional[str] = None,
    extracted_data: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a just-pushed transaction so future dedupe checks find it.
    Silently no-ops on DB failures — never block the push path."""
    amount_dec = _to_decimal(amount)
    if not date_iso or amount_dec is None:
        return
    realm = realm_id or ""
    txn_hash = _compose_hash(
        realm_id=realm, doc_type=doc_type, date_iso=date_iso,
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
                user_id, realm, doc_type, "(qb push)",
                date_iso, doc_number, amount_dec, party_name,
                txn_hash, qbo_entity_id, Json(extracted_data or {}),
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
