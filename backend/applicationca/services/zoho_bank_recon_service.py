"""
Zoho Books Bank Reconciliation Service.

Clone of `bank_recon_service` (the Tally flow) adapted for Zoho so Zoho Books
walks the exact same stateful path:

    create report (persist statement)
      -> process (parse statement + fetch Zoho txns + derive closing balance
                  + run the shared auto-matcher + persist normalized rows)
      -> review matches (accept / reject / manual)
      -> summary -> save -> Excel

The only ERP-specific swaps vs Tally are the "book" side fetch (Zoho
TransactionList instead of the desktop bridge) and the book closing balance
(derived from Zoho's CurrentBalance). The statement parser, matcher and summary
math are the shared ones, so QB and Tally reconcile to the same numbers.
"""

import asyncio
import io
import json
import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3

from config.settings import settings
from applicationca.database.database_retrieval import (
    execute_query,
    get_connection,
    return_connection,
)
# Reuse the Tally service's statement-file storage helpers verbatim — there's
# nothing ERP-specific about staging an uploaded PDF to S3 / memory.
from applicationca.services.bank_recon_service import (
    _upload_to_s3,
    store_pending_file,
    pop_pending_file,
)
from applicationca.services.zoho_bank import (
    fetch_account_ledger,
    get_account_closing_balance,
)
from applicationca.agents.document_specific_functions.bank_reconciliation import (
    compute_reconciliation_summary,
    get_financial_year,
    parse_bank_statement_with_llm,
    run_group_matching,
    _to_decimal as _dec,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serial number generation
# ---------------------------------------------------------------------------

def get_next_report_serial(user_id: str, today: date) -> str:
    """Return next serial string like 'QBBankRecon_20260605_001'."""
    date_str = today.strftime("%Y%m%d")
    prefix = f"QBBankRecon_{date_str}_"
    rows = execute_query(
        """
        SELECT report_name FROM zoho_bank_recon_reports
        WHERE user_id = %s AND report_name LIKE %s
        ORDER BY report_name DESC LIMIT 1
        """,
        (user_id, f"{prefix}%"),
    )
    if rows:
        last_name = rows[0]["report_name"]
        try:
            last_num = int(last_name.split("_")[-1])
        except (ValueError, IndexError):
            last_num = 0
        return f"{prefix}{str(last_num + 1).zfill(3)}"
    return f"{prefix}001"


# ---------------------------------------------------------------------------
# Report CRUD
# ---------------------------------------------------------------------------

def create_report(
    user_id: str,
    organization_id: str,
    account_id: str,
    account_name: Optional[str],
    report_name: str,
    period_from: date,
    period_to: date,
    financial_year: str,
    file_url: str,
    file_name: str,
    zoho_closing_balance_override: Optional[float] = None,
) -> Optional[Dict]:
    conn = get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO zoho_bank_recon_reports
              (user_id, organization_id, account_id, account_name, report_name,
               period_from, period_to, financial_year,
               status, zoho_closing_balance_override,
               bank_statement_file_url, bank_statement_file_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s)
            RETURNING report_id, report_name, status, created_at
            """,
            (user_id, organization_id, account_id, account_name, report_name,
             period_from, period_to, financial_year,
             zoho_closing_balance_override, file_url, file_name),
        )
        row = cursor.fetchone()
        conn.commit()
        return {
            "report_id": row[0],
            "report_name": row[1],
            "status": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        }
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho create_report error: {e}")
        return None
    finally:
        return_connection(conn)


def get_reports(user_id: str, organization_id: str, account_id: str) -> List[Dict]:
    rows = execute_query(
        """
        SELECT report_id, report_name, organization_id, account_id, account_name,
               period_from, period_to, financial_year, status,
               book_opening_balance, bank_opening_balance,
               book_closing_balance, bank_closing_balance,
               reconciliation_difference, matched_count,
               unmatched_book_count, unmatched_bank_count,
               created_at, updated_at
        FROM zoho_bank_recon_reports
        WHERE user_id = %s AND organization_id = %s AND account_id = %s
        ORDER BY created_at DESC
        """,
        (user_id, organization_id, account_id),
    )
    return [dict(r) for r in rows] if rows else []


def get_report(report_id: int, user_id: str) -> Optional[Dict]:
    rows = execute_query(
        """
        SELECT * FROM zoho_bank_recon_reports
        WHERE report_id = %s AND user_id = %s
        """,
        (report_id, user_id),
    )
    return dict(rows[0]) if rows else None


def update_report(report_id: int, user_id: str, fields: Dict) -> bool:
    allowed = {
        "report_name", "status", "account_name",
        "book_opening_balance", "book_closing_balance",
        "bank_opening_balance", "bank_closing_balance",
        "zoho_closing_balance_override",
        "reconciliation_difference",
        "book_credits_not_in_bank", "book_debits_not_in_bank",
        "bank_credits_not_in_book", "bank_debits_not_in_book",
        "total_book_transactions", "total_bank_transactions",
        "matched_count", "unmatched_book_count", "unmatched_bank_count",
    }
    update_fields = {k: v for k, v in fields.items() if k in allowed}
    if not update_fields:
        return False
    set_clause = ", ".join(f"{k} = %s" for k in update_fields)
    values = list(update_fields.values()) + [datetime.now(), report_id, user_id]
    result = execute_query(
        f"""
        UPDATE zoho_bank_recon_reports
        SET {set_clause}, updated_at = %s
        WHERE report_id = %s AND user_id = %s
        """,
        values,
    )
    return result is not None


def delete_report(report_id: int, user_id: str) -> bool:
    result = execute_query(
        "DELETE FROM zoho_bank_recon_reports WHERE report_id = %s AND user_id = %s",
        (report_id, user_id),
    )
    return result is not None


# ---------------------------------------------------------------------------
# Transaction CRUD
# ---------------------------------------------------------------------------

def bulk_insert_transactions(report_id: int, transactions: List[Dict]) -> List[int]:
    """Insert many transactions in a single batch and return their txn_ids in order."""
    if not transactions:
        return []
    conn = get_connection()
    if not conn:
        return []
    try:
        from psycopg2.extras import execute_values
        cursor = conn.cursor()
        rows = [
            (
                report_id,
                t["source"],
                t.get("date"),
                t.get("description", ""),
                t.get("reference", ""),
                t.get("debit_amount"),
                t.get("credit_amount"),
                t.get("zoho_txn_id"),
                t.get("zoho_txn_type"),
                json.dumps(t.get("raw_data")) if t.get("raw_data") else None,
                t.get("is_opening_balance", False),
            )
            for t in transactions
        ]
        fetched = execute_values(
            cursor,
            """
            INSERT INTO zoho_bank_recon_transactions
              (report_id, source, transaction_date, description, reference,
               debit_amount, credit_amount, recon_status,
               zoho_txn_id, zoho_txn_type, raw_data, is_opening_balance)
            VALUES %s
            RETURNING txn_id
            """,
            rows,
            template="(%s,%s,%s,%s,%s,%s,%s,'unmatched',%s,%s,%s,%s)",
            fetch=True,
        )
        ids = [row[0] for row in fetched]
        conn.commit()
        return ids
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho bulk_insert_transactions error: {e}")
        return []
    finally:
        return_connection(conn)


def get_transactions(
    report_id: int,
    source: Optional[str] = None,
    recon_status: Optional[str] = None,
) -> List[Dict]:
    base_sql = """
        SELECT txn_id, report_id, source, transaction_date, description, reference,
               debit_amount, credit_amount, recon_status, match_id,
               zoho_txn_id, zoho_txn_type, is_opening_balance, created_at,
               COALESCE(raw_data->>'manual', '') = 'true' AS is_manual,
               COALESCE(raw_data->>'adjustment', '') = 'true' AS is_adjustment
        FROM zoho_bank_recon_transactions
        WHERE report_id = %s AND is_opening_balance = FALSE
    """
    params = [report_id]
    if source:
        base_sql += " AND source = %s"
        params.append(source)
    if recon_status:
        base_sql += " AND recon_status = %s"
        params.append(recon_status)
    base_sql += " ORDER BY transaction_date, txn_id"
    rows = execute_query(base_sql, params)
    return [dict(r) for r in rows] if rows else []


def mark_transactions_pending(report_id: int, txn_ids: List[int], pending: bool = True) -> int:
    """Move unmatched transactions to/from the 'pending' triage bucket.

    No Zoho writes — this is pure recon triage ("set this row aside for later").
    A pending row drops out of the unmatched lists and the reconciliation
    difference. Only `unmatched` <-> `pending` transitions are allowed, so
    matched rows are never disturbed. Returns the number of rows changed.
    """
    if not txn_ids:
        return 0
    target = "pending" if pending else "unmatched"
    source = "unmatched" if pending else "pending"
    conn = get_connection()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        placeholders = ",".join(["%s"] * len(txn_ids))
        cursor.execute(
            f"""
            UPDATE zoho_bank_recon_transactions
            SET recon_status = %s
            WHERE report_id = %s AND recon_status = %s AND txn_id IN ({placeholders})
            """,
            [target, report_id, source, *txn_ids],
        )
        n = cursor.rowcount
        conn.commit()
        return n
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho mark_transactions_pending error: {e}")
        return 0
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------
# Match CRUD
# ---------------------------------------------------------------------------

def bulk_create_match_groups(
    report_id: int,
    groups: List[Dict],
    book_ids: List[int],
    bank_ids: List[int],
) -> Dict[str, int]:
    """Persist the groups returned by run_group_matching.

    Each group is {book_idxs, bank_idxs, confidence, tier, reason}. The idxs point
    into the inserted-row order, so book_ids[idx] / bank_ids[idx] recover the
    persisted txn_ids. A group becomes one match header + N member rows. Tier
    drives the initial review state:
        tier 'auto'    -> status 'accepted'       (auto-reconciled, badge A)
        tier 'suggest' -> status 'pending_review' (needs review, badge S)
    All member txns are flipped to 'auto_matched' (tentative); rejecting a match
    returns them to 'unmatched'. Returns {total, auto, suggest}.
    """
    if not groups:
        return {"total": 0, "auto": 0, "suggest": 0}
    conn = get_connection()
    if not conn:
        return {"total": 0, "auto": 0, "suggest": 0}
    try:
        from psycopg2.extras import execute_values
        cursor = conn.cursor()
        auto_n = 0
        for g in groups:
            book_txn_ids = [book_ids[i] for i in g["book_idxs"]]
            bank_txn_ids = [bank_ids[j] for j in g["bank_idxs"]]
            tier = g.get("tier", "suggest")
            status = "accepted" if tier == "auto" else "pending_review"
            if tier == "auto":
                auto_n += 1
            # denormalized 1:1 columns are kept only for the trivial single+single case
            single_book = book_txn_ids[0] if len(book_txn_ids) == 1 else None
            single_bank = bank_txn_ids[0] if len(bank_txn_ids) == 1 else None
            cursor.execute(
                """
                INSERT INTO zoho_bank_recon_matches
                  (report_id, match_type, status, tier, book_txn_id, bank_txn_id,
                   confidence_score, match_reason, notes)
                VALUES (%s,'automatic',%s,%s,%s,%s,%s,%s,'')
                RETURNING match_id
                """,
                (report_id, status, tier, single_book, single_bank,
                 g.get("confidence"), g.get("reason", "")),
            )
            match_id = cursor.fetchone()[0]
            member_rows = (
                [(match_id, t, "book") for t in book_txn_ids]
                + [(match_id, t, "bank") for t in bank_txn_ids]
            )
            execute_values(
                cursor,
                "INSERT INTO zoho_bank_recon_match_members (match_id, txn_id, side) VALUES %s",
                member_rows,
            )
            cursor.executemany(
                "UPDATE zoho_bank_recon_transactions SET recon_status='auto_matched', match_id=%s WHERE txn_id=%s",
                [(match_id, t) for t in (book_txn_ids + bank_txn_ids)],
            )
        conn.commit()
        return {"total": len(groups), "auto": auto_n, "suggest": len(groups) - auto_n}
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho bulk_create_match_groups error: {e}")
        return {"total": 0, "auto": 0, "suggest": 0}
    finally:
        return_connection(conn)


def create_match(
    report_id: int,
    book_txn_id: int,
    bank_txn_id: int,
    match_type: str,
    confidence: float,
    reason: str,
    notes: str = "",
) -> Optional[int]:
    conn = get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO zoho_bank_recon_matches
              (report_id, match_type, status, book_txn_id, bank_txn_id,
               confidence_score, match_reason, notes)
            VALUES (%s,%s,'pending_review',%s,%s,%s,%s,%s)
            RETURNING match_id
            """,
            (report_id, match_type, book_txn_id, bank_txn_id, confidence, reason, notes),
        )
        match_id = cursor.fetchone()[0]
        conn.commit()
        return match_id
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho create_match error: {e}")
        return None
    finally:
        return_connection(conn)


def get_matches(report_id: int, status: Optional[str] = None) -> List[Dict]:
    """Return match groups for a report. Each match carries member arrays
    (`bank_transactions` / `book_transactions`) so 1-to-many and many-to-many
    matches render correctly. Singular `bank_transaction` / `book_transaction`
    (the first member) are also included for back-compat with 1:1 consumers.
    """
    sql = """
        SELECT match_id, match_type, tier, status, confidence_score,
               match_reason, notes, created_at, reviewed_at
        FROM zoho_bank_recon_matches
        WHERE report_id = %s
    """
    params = [report_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY created_at"
    headers = execute_query(sql, params)
    if not headers:
        return []

    match_ids = [h["match_id"] for h in headers]
    placeholders = ",".join(["%s"] * len(match_ids))
    members = execute_query(
        f"""
        SELECT mm.match_id, mm.side,
               t.txn_id, t.transaction_date, t.description, t.reference,
               t.debit_amount, t.credit_amount, t.zoho_txn_id, t.zoho_txn_type
        FROM zoho_bank_recon_match_members mm
        JOIN zoho_bank_recon_transactions t ON t.txn_id = mm.txn_id
        WHERE mm.match_id IN ({placeholders})
        ORDER BY t.transaction_date, t.txn_id
        """,
        match_ids,
    )

    grouped: Dict[int, Dict[str, List]] = defaultdict(lambda: {"bank": [], "book": []})
    for m in members or []:
        txn = {
            "txn_id": m["txn_id"],
            "transaction_date": m["transaction_date"].isoformat() if m["transaction_date"] else None,
            "description": m["description"],
            "reference": m["reference"],
            "debit_amount": float(m["debit_amount"]) if m["debit_amount"] else None,
            "credit_amount": float(m["credit_amount"]) if m["credit_amount"] else None,
        }
        if m["side"] == "book":
            txn["zoho_txn_id"] = m["zoho_txn_id"]
            txn["zoho_txn_type"] = m["zoho_txn_type"]
        grouped[m["match_id"]][m["side"]].append(txn)

    result = []
    for h in headers:
        g = grouped.get(h["match_id"], {"bank": [], "book": []})
        result.append({
            "match_id": h["match_id"],
            "match_type": h["match_type"],
            "tier": h.get("tier"),
            "status": h["status"],
            "confidence_score": float(h["confidence_score"]) if h["confidence_score"] else None,
            "match_reason": h["match_reason"],
            "notes": h["notes"],
            "created_at": h["created_at"].isoformat() if h["created_at"] else None,
            "reviewed_at": h["reviewed_at"].isoformat() if h["reviewed_at"] else None,
            "bank_transactions": g["bank"],
            "book_transactions": g["book"],
            "bank_transaction": g["bank"][0] if g["bank"] else None,
            "book_transaction": g["book"][0] if g["book"] else None,
        })
    return result


def update_match_status(match_id: int, status: str) -> Optional[Dict]:
    """Accept or reject a match group. Flips ALL member txns; on reject they
    return to unmatched."""
    conn = get_connection()
    if not conn:
        logger.error("zoho update_match_status: could not get DB connection")
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE zoho_bank_recon_matches
            SET status = %s, reviewed_at = NOW()
            WHERE match_id = %s
            RETURNING match_type
            """,
            (status, match_id),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            logger.warning(f"zoho update_match_status: match_id={match_id} not found")
            return None
        match_type = row[0]

        cursor.execute(
            "SELECT txn_id FROM zoho_bank_recon_match_members WHERE match_id = %s",
            (match_id,),
        )
        member_ids = [r[0] for r in cursor.fetchall()]

        if member_ids:
            if status == "accepted":
                new_status = "manual_matched" if match_type == "manual" else "auto_matched"
                cursor.executemany(
                    "UPDATE zoho_bank_recon_transactions SET recon_status=%s, match_id=%s WHERE txn_id=%s",
                    [(new_status, match_id, t) for t in member_ids],
                )
            elif status == "rejected":
                cursor.executemany(
                    "UPDATE zoho_bank_recon_transactions SET recon_status='unmatched', match_id=NULL WHERE txn_id=%s",
                    [(t,) for t in member_ids],
                )

        conn.commit()
        return {"match_id": match_id, "status": status}
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho update_match_status error (match_id={match_id}, status={status}): {e}")
        return None
    finally:
        return_connection(conn)


def bulk_update_match_status(match_ids: List[int], status: str) -> int:
    updated = 0
    for mid in match_ids:
        if update_match_status(mid, status):
            updated += 1
    return updated


def create_manual_match_group(
    report_id: int,
    book_txn_ids: List[int],
    bank_txn_ids: List[int],
    notes: str = "",
) -> Optional[Dict]:
    """User-driven many-to-many match. Creates an accepted manual match group and
    flips all member txns to manual_matched. Either side may be empty (e.g. an
    all-book contra), but not both."""
    book_txn_ids = list(book_txn_ids or [])
    bank_txn_ids = list(bank_txn_ids or [])
    if not book_txn_ids and not bank_txn_ids:
        return None
    conn = get_connection()
    if not conn:
        return None
    try:
        from psycopg2.extras import execute_values
        cursor = conn.cursor()
        single_book = book_txn_ids[0] if len(book_txn_ids) == 1 else None
        single_bank = bank_txn_ids[0] if len(bank_txn_ids) == 1 else None
        cursor.execute(
            """
            INSERT INTO zoho_bank_recon_matches
              (report_id, match_type, status, tier, book_txn_id, bank_txn_id,
               confidence_score, match_reason, notes, reviewed_at)
            VALUES (%s,'manual','accepted','manual',%s,%s,1.0,%s,%s,NOW())
            RETURNING match_id
            """,
            (report_id, single_book, single_bank, "Manual match by user", notes),
        )
        match_id = cursor.fetchone()[0]
        member_rows = (
            [(match_id, t, "book") for t in book_txn_ids]
            + [(match_id, t, "bank") for t in bank_txn_ids]
        )
        execute_values(
            cursor,
            "INSERT INTO zoho_bank_recon_match_members (match_id, txn_id, side) VALUES %s",
            member_rows,
        )
        cursor.executemany(
            "UPDATE zoho_bank_recon_transactions SET recon_status='manual_matched', match_id=%s WHERE txn_id=%s",
            [(match_id, t) for t in (book_txn_ids + bank_txn_ids)],
        )
        conn.commit()
        return {"match_id": match_id, "status": "accepted"}
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho create_manual_match_group error: {e}")
        return None
    finally:
        return_connection(conn)


def create_manual_match(
    report_id: int,
    book_txn_id: int,
    bank_txn_id: int,
    notes: str = "",
) -> Optional[Dict]:
    """Back-compat 1:1 wrapper over create_manual_match_group."""
    return create_manual_match_group(report_id, [book_txn_id], [bank_txn_id], notes)


def delete_match(match_id: int) -> bool:
    """Remove a match group and return all member transactions to unmatched."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT txn_id FROM zoho_bank_recon_match_members WHERE match_id = %s",
            (match_id,),
        )
        member_ids = [r[0] for r in cursor.fetchall()]
        cursor.execute("DELETE FROM zoho_bank_recon_matches WHERE match_id = %s", (match_id,))
        if cursor.rowcount == 0:
            conn.rollback()
            return False
        if member_ids:
            cursor.executemany(
                "UPDATE zoho_bank_recon_transactions SET recon_status='unmatched', match_id=NULL WHERE txn_id=%s",
                [(t,) for t in member_ids],
            )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho delete_match error: {e}")
        return False
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------
# Adjusting entries (create a missing book entry + post it to Zoho Books)
# ---------------------------------------------------------------------------

async def create_adjustment(
    report_id: int,
    user_id: str,
    organization_id: Optional[str],
    *,
    date_str: str,
    description: str,
    voucher_type: str,
    amount: float,
    party_ledger: str,
    party_ledger_id: Optional[str] = None,
    reference: str = "",
    bank_txn_ids: Optional[List[int]] = None,
    auto_create_masters: bool = True,
    auto_match: bool = False,
) -> Dict:
    """Create a missing book entry during manual reconciliation, POST it to
    Zoho Books, and record it as a book transaction in the recon.

    By default (`auto_match=False`) the new entry is left UNMATCHED so it shows up
    as a fresh row in the unmatched-book list and the user finishes the
    reconciliation by hand (e.g. select the bank line + the original book line +
    this adjustment, watch the difference hit zero, then Reconcile). Pass
    `auto_match=True` to immediately match it to `bank_txn_ids` instead.

    Accounting note: the effective book closing balance is shifted by the signed
    effect of the posted entry (Receipt=+, Payment=-). Posting an adjustment
    corrects the books; it does NOT move the true book-vs-bank gap, so this shift
    plus the new (matched or unmatched) row leaves the reconciliation difference
    invariant either way.
    """
    report = get_report(report_id, user_id)
    if not report:
        return {"error": "Report not found"}
    organization_id = organization_id or report.get("organization_id")

    try:
        amt = round(abs(float(amount)), 2)
    except (TypeError, ValueError):
        return {"error": "Invalid amount"}
    if amt == 0:
        return {"error": "Amount must be non-zero"}

    # Validate the date BEFORE pushing: a bad date would otherwise post the
    # entry to Zoho Books and then fail the local insert, leaving a real Zoho
    # entry with no recon row to reconcile against.
    adj_date = _coerce_date(date_str)
    if not adj_date:
        return {"error": "Invalid date"}

    voucher = {
        "date": date_str,
        "narration": description or "",
        "amount": amt,
        "voucher_type": voucher_type,
        "party_ledger": party_ledger,
        "party_ledger_id": party_ledger_id,
        "reference": reference or "",
    }

    # Reuse the single payload-builder + push path (FE never builds Zoho JSON).
    from applicationca.agents.zoho.bank_entries import push_bank_vouchers_batch
    push = await push_bank_vouchers_batch(
        user_id=user_id,
        organization_id=organization_id,
        bank_account_id=report["account_id"],
        bank_account_name=report.get("account_name"),
        vouchers=[voucher],
        auto_create_masters=auto_create_masters,
    )
    row0 = (push.get("results") or [{}])[0]
    if not row0.get("ok"):
        return {
            "error": row0.get("error") or "Failed to post adjustment to Zoho Books",
            "push": push,
        }
    zoho_id = row0.get("id")
    entry_type = row0.get("type")

    # Record it as a book transaction (Receipt = inflow = debit; else credit).
    is_inflow = (voucher_type or "").strip().title() == "Receipt"
    book_insert = {
        "source": "book",
        "date": adj_date,
        "description": description or "",
        "reference": reference or "",
        "debit_amount": amt if is_inflow else None,
        "credit_amount": None if is_inflow else amt,
        "zoho_txn_id": zoho_id,
        "zoho_txn_type": entry_type,
        "raw_data": {
            "adjustment": "true",
            "voucher_type": voucher_type,
            "party_ledger": party_ledger,
        },
    }
    new_ids = bulk_insert_transactions(report_id, [book_insert])
    new_book_id = new_ids[0] if new_ids else None

    # Keep the reconciliation difference invariant: shift effective book CB.
    delta = amt if is_inflow else -amt
    if report.get("zoho_closing_balance_override") is not None:
        update_report(report_id, user_id, {
            "zoho_closing_balance_override": float(report["zoho_closing_balance_override"]) + delta,
        })
    else:
        update_report(report_id, user_id, {
            "book_closing_balance": float(report.get("book_closing_balance") or 0) + delta,
        })

    # Only auto-match when explicitly asked; the default flow leaves the new row
    # unmatched so the user reconciles it manually.
    match = None
    if auto_match and new_book_id and bank_txn_ids:
        match = create_manual_match_group(
            report_id, [new_book_id], list(bank_txn_ids), notes="Adjustment"
        )

    return {
        "ok": True,
        "zoho_txn_id": zoho_id,
        "zoho_txn_type": entry_type,
        "book_txn_id": new_book_id,
        "match": match,
        "created_masters": push.get("created_masters") or [],
    }


# ---------------------------------------------------------------------------
# Manual bank-statement entries (statement rows the parser missed)
# ---------------------------------------------------------------------------

def _validate_bank_entry_amounts(debit_amount, credit_amount):
    """Exactly one of debit/credit must be a positive amount. Returns
    (debit, credit, error)."""
    try:
        debit = round(abs(float(debit_amount)), 2) if debit_amount is not None else None
        credit = round(abs(float(credit_amount)), 2) if credit_amount is not None else None
    except (TypeError, ValueError):
        return None, None, "Invalid amount"
    debit = debit or None
    credit = credit or None
    if bool(debit) == bool(credit):
        return None, None, "Provide exactly one of debit_amount or credit_amount (> 0)"
    return debit, credit, None


def create_manual_bank_entry(
    report_id: int,
    *,
    date_str: str,
    description: str,
    reference: str = "",
    debit_amount: Optional[float] = None,
    credit_amount: Optional[float] = None,
) -> Dict:
    """Add a bank-statement row the parser missed. Local-only — banks can't be
    written to, so the row just joins the statement side of the recon (flagged
    `manual` so the UI can badge and edit/delete it).

    No balance shift: the statement closing balance already reflects every real
    bank movement, including ones the parser dropped, so the new unmatched row
    slots into the bank-side summary categories and the difference self-corrects.
    """
    tdate = _coerce_date(date_str)
    if not tdate:
        return {"error": "Invalid date"}
    debit, credit, err = _validate_bank_entry_amounts(debit_amount, credit_amount)
    if err:
        return {"error": err}

    new_ids = bulk_insert_transactions(report_id, [{
        "source": "bank_statement",
        "date": tdate,
        "description": description or "",
        "reference": reference or "",
        "debit_amount": debit,
        "credit_amount": credit,
        "raw_data": {"manual": "true"},
    }])
    if not new_ids:
        return {"error": "Failed to add bank entry"}
    return {"ok": True, "bank_txn_id": new_ids[0]}


def _get_editable_manual_bank_entry(cursor, report_id: int, txn_id: int) -> Optional[str]:
    """Lock the row and verify it is a manual, still-unreconciled bank entry.
    Returns an error string, or None when the row is editable."""
    cursor.execute(
        """
        SELECT source, recon_status, COALESCE(raw_data->>'manual', '') = 'true'
        FROM zoho_bank_recon_transactions
        WHERE report_id = %s AND txn_id = %s
        FOR UPDATE
        """,
        (report_id, txn_id),
    )
    row = cursor.fetchone()
    if not row:
        return "Bank entry not found"
    source, recon_status, is_manual = row
    if source != "bank_statement" or not is_manual:
        return "Only manually added bank entries can be modified"
    if recon_status not in ("unmatched", "pending"):
        return "Entry is part of a match — remove the match first"
    return None


def update_manual_bank_entry(
    report_id: int,
    txn_id: int,
    *,
    date_str: str,
    description: str,
    reference: str = "",
    debit_amount: Optional[float] = None,
    credit_amount: Optional[float] = None,
) -> Dict:
    """Full-replace edit of a manual bank entry (only while unmatched/pending)."""
    tdate = _coerce_date(date_str)
    if not tdate:
        return {"error": "Invalid date"}
    debit, credit, err = _validate_bank_entry_amounts(debit_amount, credit_amount)
    if err:
        return {"error": err}

    conn = get_connection()
    if not conn:
        return {"error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        err = _get_editable_manual_bank_entry(cursor, report_id, txn_id)
        if err:
            conn.rollback()
            return {"error": err}
        cursor.execute(
            """
            UPDATE zoho_bank_recon_transactions
            SET transaction_date = %s, description = %s, reference = %s,
                debit_amount = %s, credit_amount = %s
            WHERE report_id = %s AND txn_id = %s
            """,
            (tdate, description or "", reference or "", debit, credit, report_id, txn_id),
        )
        conn.commit()
        return {"ok": True, "bank_txn_id": txn_id}
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho update_manual_bank_entry error: {e}")
        return {"error": "Failed to update bank entry"}
    finally:
        return_connection(conn)


def delete_manual_bank_entry(report_id: int, txn_id: int) -> Dict:
    """Delete a manual bank entry (only while unmatched/pending)."""
    conn = get_connection()
    if not conn:
        return {"error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        err = _get_editable_manual_bank_entry(cursor, report_id, txn_id)
        if err:
            conn.rollback()
            return {"error": err}
        cursor.execute(
            "DELETE FROM zoho_bank_recon_transactions WHERE report_id = %s AND txn_id = %s",
            (report_id, txn_id),
        )
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho delete_manual_bank_entry error: {e}")
        return {"error": "Failed to delete bank entry"}
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------
# Reconciliation Summary
# ---------------------------------------------------------------------------

def _effective_book_cb(report: Dict) -> Decimal:
    """User override wins over the auto-derived book closing balance."""
    override = report.get("zoho_closing_balance_override")
    if override is not None:
        return Decimal(str(override))
    return Decimal(str(report.get("book_closing_balance") or 0))


def get_summary(report_id: int, user_id: str) -> Optional[Dict]:
    report = get_report(report_id, user_id)
    if not report:
        return None

    unmatched_book = get_transactions(report_id, source="book", recon_status="unmatched")
    unmatched_bank = get_transactions(report_id, source="bank_statement", recon_status="unmatched")

    book_cb = _effective_book_cb(report)
    bank_cb = Decimal(str(report.get("bank_closing_balance") or 0))

    summary = compute_reconciliation_summary(book_cb, bank_cb, unmatched_book, unmatched_bank)

    pending_count = execute_query(
        "SELECT COUNT(*) as cnt FROM zoho_bank_recon_matches WHERE report_id = %s AND status = 'pending_review'",
        (report_id,),
    )
    accepted_count = execute_query(
        "SELECT COUNT(*) as cnt FROM zoho_bank_recon_matches WHERE report_id = %s AND status = 'accepted'",
        (report_id,),
    )
    pending_txn_count = execute_query(
        "SELECT COUNT(*) as cnt FROM zoho_bank_recon_transactions WHERE report_id = %s AND recon_status = 'pending'",
        (report_id,),
    )

    # Automatic reconciliation % = share of transactions reconciled by the engine
    # at the 'auto' tier (the Reconcilio-style headline).
    total_txn = execute_query(
        "SELECT COUNT(*) as cnt FROM zoho_bank_recon_transactions WHERE report_id = %s AND is_opening_balance = FALSE",
        (report_id,),
    )
    auto_txn = execute_query(
        """
        SELECT COUNT(DISTINCT mm.txn_id) as cnt
        FROM zoho_bank_recon_match_members mm
        JOIN zoho_bank_recon_matches m ON m.match_id = mm.match_id
        WHERE m.report_id = %s AND m.tier = 'auto'
        """,
        (report_id,),
    )
    total_n = total_txn[0]["cnt"] if total_txn else 0
    auto_n = auto_txn[0]["cnt"] if auto_txn else 0
    automatic_pct = round((auto_n / total_n) * 100, 2) if total_n else 0.0

    return {
        "report_id": report_id,
        "report_name": report.get("report_name"),
        "status": report.get("status"),
        "organization_id": report.get("organization_id"),
        "account_id": report.get("account_id"),
        "account_name": report.get("account_name"),
        "period_from": report["period_from"].isoformat() if report.get("period_from") else None,
        "period_to": report["period_to"].isoformat() if report.get("period_to") else None,
        "financial_year": report.get("financial_year"),
        "book_opening_balance": float(report.get("book_opening_balance") or 0),
        "book_closing_balance": float(book_cb),
        "bank_opening_balance": float(report.get("bank_opening_balance") or 0),
        "bank_closing_balance": float(bank_cb),
        "summary": summary,
        "counts": {
            "total_matched": report.get("matched_count", 0),
            "unmatched_book": len(unmatched_book),
            "unmatched_bank": len(unmatched_bank),
            "pending_review": pending_count[0]["cnt"] if pending_count else 0,
            "accepted": accepted_count[0]["cnt"] if accepted_count else 0,
            "pending_transactions": pending_txn_count[0]["cnt"] if pending_txn_count else 0,
            "automatic_pct": automatic_pct,
        },
    }


# ---------------------------------------------------------------------------
# Processing Pipeline
# ---------------------------------------------------------------------------

def _zoho_txn_description(t: Dict) -> str:
    return " ".join(s for s in (t.get("payee"), t.get("memo"), t.get("split_account")) if s)


def _claim_report_for_processing(report_id: int, user_id: str) -> bool:
    """Atomically claim a report for processing (status -> 'processing').

    Returns False when the report is finished ('completed') or another run is
    actively processing it (a 'processing' status touched within the last 15
    minutes). A stale 'processing' lock — server died mid-run — is reclaimed.
    This closes the race where two concurrent /process calls both pass a
    read-then-write status check and double-insert every transaction.
    """
    conn = get_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE zoho_bank_recon_reports
            SET status = 'processing', updated_at = NOW()
            WHERE report_id = %s AND user_id = %s
              AND status != 'completed'
              AND NOT (status = 'processing' AND updated_at > NOW() - INTERVAL '15 minutes')
            RETURNING report_id
            """,
            (report_id, user_id),
        )
        claimed = cursor.fetchone() is not None
        conn.commit()
        return claimed
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho _claim_report_for_processing error: {e}")
        return False
    finally:
        return_connection(conn)


def _clear_report_rows(report_id: int) -> None:
    """Reset a report's snapshot before a (re)processing run inserts fresh rows.

    Reprocessing must REPLACE the previous snapshot, not append to it — without
    this, retrying an errored run or re-running after fixing the books in Zoho
    duplicates every transaction and match.

    Manually added bank entries are preserved (they can't be re-derived — the
    parser missed them, which is why the user keyed them in); they're just
    unlinked from any match. Adjustment book rows ARE deleted: they were posted
    to Zoho, so the fresh GL fetch brings them back as regular book txns and
    keeping the local copy would double-count them. Matches go first — group
    headers with NULL single-id columns would survive the transaction cascade.
    """
    conn = get_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM zoho_bank_recon_matches WHERE report_id = %s", (report_id,))
        cursor.execute(
            """
            UPDATE zoho_bank_recon_transactions
            SET match_id = NULL,
                recon_status = CASE WHEN recon_status = 'pending' THEN 'pending' ELSE 'unmatched' END
            WHERE report_id = %s AND COALESCE(raw_data->>'manual', '') = 'true'
            """,
            (report_id,),
        )
        cursor.execute(
            """
            DELETE FROM zoho_bank_recon_transactions
            WHERE report_id = %s AND COALESCE(raw_data->>'manual', '') != 'true'
            """,
            (report_id,),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"zoho _clear_report_rows error: {e}")
    finally:
        return_connection(conn)


async def process_report(report_id: int, user_id: str, organization_id: Optional[str]) -> Dict:
    """
    Full processing pipeline (QB analogue of bank_recon_service.process_report):
      1. Load + parse the saved bank statement
      2. Fetch Zoho transactions for the account in the period (TransactionList)
      3. Derive the book closing balance as of period end
      4. Insert both sides into zoho_bank_recon_transactions
      5. Run the shared automatic matcher
      6. Persist matches (pending_review) + update report counts/balances
    """
    report = get_report(report_id, user_id)
    if not report:
        return {"error": "Report not found"}

    organization_id = organization_id or report.get("organization_id")

    # Atomic claim: completed reports and actively-processing ones (lock touched
    # within 15 min) return idempotently — frontend retries double-fire. Stale
    # 'processing' locks are reclaimed. draft / awaiting_source / error /
    # in_progress all (re)process; the stale snapshot is replaced below.
    current_status = report.get("status")
    if not _claim_report_for_processing(report_id, user_id):
        existing_matches = get_matches(report_id)
        return {
            "status": current_status,
            "message": f"Report already {current_status}",
            "matches_count": len(existing_matches),
            "idempotent": True,
        }

    try:
        # ── Step 1: load the bank statement file ──
        file_url = report.get("bank_statement_file_url", "")
        file_name = report.get("bank_statement_file_name", "statement.pdf")
        file_content: Optional[bytes] = None

        if file_url and file_url.startswith("pending://"):
            pending = pop_pending_file(file_url)
            if pending:
                file_content, file_name = pending
            else:
                update_report(report_id, user_id, {"status": "error"})
                return {"error": "Uploaded file not found in memory. Please re-upload the statement."}
        elif file_url:
            try:
                s3 = boto3.client(
                    "s3",
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                    region_name=settings.AWS_DEFAULT_REGION,
                )
                bucket = settings.AWS_BUCKET_NAME if hasattr(settings, "AWS_BUCKET_NAME") else "aureus-documents"
                s3_key = file_url.split(f"{bucket}.s3.{settings.AWS_DEFAULT_REGION}.amazonaws.com/")[-1]
                obj = s3.get_object(Bucket=bucket, Key=s3_key)
                file_content = obj["Body"].read()
            except Exception as e:
                logger.error(f"zoho S3 download failed: {e}")
                update_report(report_id, user_id, {"status": "error"})
                return {"error": "Could not retrieve bank statement file from storage."}
        else:
            update_report(report_id, user_id, {"status": "error"})
            return {"error": "No bank statement file associated with this report."}

        period_from: date = report["period_from"]
        period_to: date = report["period_to"]

        # ── Step 2: parse statement + fetch Zoho book txns (concurrently) ──
        # Use the SAME LLM parser Tally recon uses (parse_bank_statement_with_llm),
        # not the rule-based parser. The rule-based path can't tell debit from
        # credit on text-only PDFs (it dumps everything into debit_amount), which
        # silently breaks the cross-amount matcher. The LLM parser also filters
        # rows to the recon period. Same engine as Tally => same matches.
        #
        # The book side comes from the GeneralLedger report (account-scoped
        # Debit/Credit — trustworthy direction, see zoho_bank.fetch_account_ledger)
        # which ALSO carries the period closing balance, so no separate
        # full-history balance fetch is needed. The LLM parse and the Zoho fetch are
        # independent, so run them together to cut wall-clock.
        account_id = report["account_id"]
        parsed, ledger = await asyncio.gather(
            parse_bank_statement_with_llm(
                file_content, file_name, period_from=period_from, period_to=period_to,
            ),
            fetch_account_ledger(
                user_id,
                account_id=account_id,
                start_date=period_from.isoformat(),
                end_date=period_to.isoformat(),
                organization_id=organization_id,
            ),
        )
        bank_txns_raw = [
            t for t in (parsed.get("transactions") or [])
            if not t.get("is_opening_balance") and not t.get("is_closing_balance")
        ]
        bank_ob = parsed.get("opening_balance")
        bank_cb = parsed.get("closing_balance")

        zoho_txns = ledger.get("transactions") or []
        logger.info(
            f"zoho recon report {report_id}: parsed {len(bank_txns_raw)} statement rows, "
            f"fetched {len(zoho_txns)} Zoho GL txns"
        )

        # ── Step 3: derive book closing balance (override wins) ──
        # Prefer the GL report's period closing balance; only fall back to the
        # (slower) CurrentBalance roll-back if GL didn't expose it.
        override = report.get("zoho_closing_balance_override")
        if override is not None:
            book_cb = Decimal(str(override))
        elif ledger.get("closing_balance") is not None:
            book_cb = ledger["closing_balance"]
        else:
            book_cb = await get_account_closing_balance(
                user_id, account_id=account_id, as_of=period_to.isoformat(), organization_id=organization_id
            )

        # ── Step 4 (pre): replace any earlier snapshot ──
        # A reprocess (error retry, Zoho reconnect, or re-run after fixing the
        # books) must not append duplicate rows next to the old ones.
        _clear_report_rows(report_id)

        # ── Step 4a: insert Zoho book transactions ──
        # GL gives account-relative debit/credit directly (debit = money in =
        # inflow, credit = money out = outflow), so no sign inference here.
        book_inserts = []
        for t in zoho_txns:
            debit = t.get("debit") or None
            credit = t.get("credit") or None
            tdate = _parse_iso_date(t.get("date"))
            if not tdate:
                continue
            book_inserts.append({
                "source": "book",
                "date": tdate,
                "description": _zoho_txn_description(t),
                "reference": t.get("doc_number") or "",
                "debit_amount": float(debit) if debit else None,
                "credit_amount": float(credit) if credit else None,
                "zoho_txn_id": t.get("id"),
                "zoho_txn_type": t.get("type"),
                "raw_data": {k: (str(v) if v is not None else None) for k, v in t.items()},
            })
        book_ids = bulk_insert_transactions(report_id, book_inserts)

        # ── Step 4b: insert bank statement transactions ──
        bank_inserts = []
        for t in bank_txns_raw:
            tdate = _coerce_date(t.get("date"))
            if not tdate:
                continue
            bank_inserts.append({
                "source": "bank_statement",
                "date": tdate,
                "description": t.get("description", ""),
                "reference": t.get("reference", "") or "",
                "debit_amount": float(t["debit_amount"]) if t.get("debit_amount") else None,
                "credit_amount": float(t["credit_amount"]) if t.get("credit_amount") else None,
                "raw_data": {k: str(v) for k, v in t.items() if k not in ("date",)},
            })
        bank_ids = bulk_insert_transactions(report_id, bank_inserts)

        # ── Step 5: run the shared automatic matcher ──
        algo_book = [
            {
                "date": t["date"],
                "description": t.get("description", ""),
                "reference": t.get("reference", ""),
                "debit_amount": _dec(t.get("debit_amount")),
                "credit_amount": _dec(t.get("credit_amount")),
            }
            for t in book_inserts
        ]
        algo_bank = [
            {
                "date": t["date"],
                "description": t.get("description", ""),
                "reference": t.get("reference", ""),
                "debit_amount": _dec(t.get("debit_amount")),
                "credit_amount": _dec(t.get("credit_amount")),
            }
            for t in bank_inserts
        ]
        groups = run_group_matching(algo_book, algo_bank)

        # ── Step 6: persist match groups + update report ──
        group_stats = bulk_create_match_groups(
            report_id=report_id,
            groups=groups,
            book_ids=book_ids,
            bank_ids=bank_ids,
        )

        total_book = len(book_inserts)
        total_bank = len(bank_inserts)
        # A group can match many txns at once, so derive the leftovers from the DB
        # rather than from a 1:1 match count.
        unmatched_book = len(get_transactions(report_id, source="book", recon_status="unmatched"))
        unmatched_bank = len(get_transactions(report_id, source="bank_statement", recon_status="unmatched"))
        update_report(report_id, user_id, {
            "status": "in_progress",
            "book_closing_balance": float(book_cb) if book_cb is not None else None,
            "bank_opening_balance": float(bank_ob) if bank_ob is not None else None,
            "bank_closing_balance": float(bank_cb) if bank_cb is not None else None,
            "total_book_transactions": total_book,
            "total_bank_transactions": total_bank,
            "matched_count": group_stats["total"],
            "unmatched_book_count": unmatched_book,
            "unmatched_bank_count": unmatched_bank,
        })

        return {
            "report_id": report_id,
            "status": "in_progress",
            "total_bank_transactions": total_bank,
            "total_book_transactions": total_book,
            "matched_count": group_stats["total"],
            "auto_matched_groups": group_stats["auto"],
            "suggested_groups": group_stats["suggest"],
            "unmatched_bank_count": unmatched_bank,
            "unmatched_book_count": unmatched_book,
        }

    except Exception as e:
        logger.error(f"zoho process_report error for report {report_id}: {e}", exc_info=True)
        update_report(report_id, user_id, {"status": "error"})
        return {"error": str(e)}


def _parse_iso_date(value) -> Optional[date]:
    """Parse a Zoho 'YYYY-MM-DD' date string (or date object) to a date."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _coerce_date(value) -> Optional[date]:
    """Coerce a parsed statement-row date (date/datetime/str) to a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    from applicationca.agents.document_specific_functions.bank_reconciliation import _parse_date
    return _parse_date(value)


# ---------------------------------------------------------------------------
# Excel Report Generation
# ---------------------------------------------------------------------------

def generate_excel_report(report_id: int, user_id: str) -> Optional[bytes]:
    """Excel export mirroring the Tally recon report:
      Sheet 1: Reconciliation Summary
      Sheet 2: Matched Transactions
      Sheet 3: Unmatched Book (Zoho)
      Sheet 4: Unmatched Bank
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        logger.error("openpyxl not installed. Install with: pip install openpyxl")
        return None

    report = get_report(report_id, user_id)
    if not report:
        return None

    summary = get_summary(report_id, user_id)
    wb = openpyxl.Workbook()

    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    green_font = Font(color="006400", bold=True)
    red_font = Font(color="8B0000", bold=True)

    def _header_row(ws, values):
        row = ws.max_row + 1 if ws.max_row > 0 else 1
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

    def _data_row(ws, values):
        row = ws.max_row + 1
        for col, val in enumerate(values, 1):
            ws.cell(row=row, column=col, value=val)

    def _auto_width(ws):
        for col in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col), default=10)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 50)

    # ── Sheet 1: Summary ──
    ws1 = wb.active
    ws1.title = "Reconciliation Summary"
    ws1.append([f"Bank Reconciliation — {report['report_name']}"])
    ws1.cell(1, 1).font = Font(bold=True, size=14)
    ws1.append([f"Account: {report.get('account_name') or report.get('account_id')}"])
    ws1.append([f"Period: {report['period_from']} to {report['period_to']}   |   FY: {report.get('financial_year','')}"])
    ws1.append([])

    s = summary["summary"]
    bcb = summary["bank_closing_balance"]
    kcb = summary["book_closing_balance"]

    def _summary_row(label, amount, sign=""):
        ws1.append([label, sign, f"{amount:,.2f}"])

    ws1.append(["Reconciliation Statement", "", "Amount"])
    ws1.cell(ws1.max_row, 1).font = Font(bold=True, size=12)
    _summary_row("Zoho Book Closing Balance", kcb)
    _summary_row("Add: Book Credits not in Bank Statement", s["ledger_credits_not_in_bank"], "(+)")
    _summary_row("Less: Book Debits not in Bank Statement", s["ledger_debits_not_in_bank"], "(-)")
    _summary_row("Add: Bank Credits not in Book", s["bank_credits_not_in_ledger"], "(+)")
    _summary_row("Less: Bank Debits not in Book", s["bank_debits_not_in_ledger"], "(-)")
    _summary_row("Reconciled Amount", s["reconciled_amount"])
    _summary_row("Bank Statement Closing Balance", bcb)
    diff_row = ws1.max_row + 1
    ws1.append(["Reconciliation Difference", "", f"{s['reconciliation_difference']:,.2f}"])
    diff_cell = ws1.cell(diff_row, 3)
    diff_cell.font = green_font if abs(s["reconciliation_difference"]) < 0.01 else red_font
    _auto_width(ws1)

    # ── Sheet 2: Matched ──
    ws2 = wb.create_sheet("Matched Transactions")
    _header_row(ws2, [
        "Match Type", "Status",
        "Bank Date", "Bank Description", "Bank Ref", "Bank Dr", "Bank Cr",
        "Book Date", "Book Description", "Book Ref", "Book Dr", "Book Cr",
        "Zoho Txn Type", "Zoho Txn ID",
    ])
    for m in get_matches(report_id, status="accepted"):
        banks = m.get("bank_transactions") or []
        books = m.get("book_transactions") or []
        for idx in range(max(len(banks), len(books), 1)):
            bt = banks[idx] if idx < len(banks) else {}
            kt = books[idx] if idx < len(books) else {}
            _data_row(ws2, [
                m["match_type"] if idx == 0 else "", m["status"] if idx == 0 else "",
                bt.get("transaction_date"), bt.get("description"), bt.get("reference"),
                bt.get("debit_amount"), bt.get("credit_amount"),
                kt.get("transaction_date"), kt.get("description"), kt.get("reference"),
                kt.get("debit_amount"), kt.get("credit_amount"),
                kt.get("zoho_txn_type"), kt.get("zoho_txn_id"),
            ])
    _auto_width(ws2)

    # ── Sheet 3: Unmatched Book ──
    ws3 = wb.create_sheet("Unmatched Book Entries")
    _header_row(ws3, ["Date", "Description", "Reference", "Debit", "Credit", "Zoho Txn Type", "Zoho Txn ID"])
    for t in get_transactions(report_id, source="book", recon_status="unmatched"):
        _data_row(ws3, [
            t["transaction_date"], t["description"], t["reference"],
            t["debit_amount"], t["credit_amount"], t["zoho_txn_type"], t["zoho_txn_id"],
        ])
    _auto_width(ws3)

    # ── Sheet 4: Unmatched Bank ──
    ws4 = wb.create_sheet("Unmatched Bank Entries")
    _header_row(ws4, ["Date", "Description", "Reference", "Debit", "Credit"])
    for t in get_transactions(report_id, source="bank_statement", recon_status="unmatched"):
        _data_row(ws4, [
            t["transaction_date"], t["description"], t["reference"],
            t["debit_amount"], t["credit_amount"],
        ])
    _auto_width(ws4)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
