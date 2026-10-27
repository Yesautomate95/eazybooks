"""
Persistence layer for SAP S/4HANA bank-reconciliation reports.

A "report" is one user's snapshot of a recon attempt against a bank account
for a specific period: the parsed statement, the SAP txns at the time, the
auto + manual matches, and the summary. Stored as JSON so reopens don't need
SAP refetches.

Mirrors qb_recon_store with the SAP `company_code` company key and
`sap_txn*` naming.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection

logger = logging.getLogger(__name__)


def save_report(
    *,
    user_id: str,
    company_code: str,
    account_id: str,
    account_name: Optional[str],
    period_start: str,
    period_end: str,
    statement: Dict[str, Any],
    sap_txns: List[Dict[str, Any]],
    matches: List[Dict[str, Any]],
    summary: Dict[str, Any],
    sap_closing_balance: Optional[float],
    needs_review: bool,
    status: str = "draft",
) -> Optional[str]:
    """Insert a new report + its match rows. Returns the report id (UUID str)."""
    conn = get_connection()
    if conn is None:
        logger.error("save_report skipped — DB unavailable")
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO sap_recon_reports
                (user_id, company_code, account_id, account_name, period_start, period_end,
                 statement_json, sap_txns_json, summary_json, sap_closing_balance,
                 needs_review, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id, company_code, account_id, account_name, period_start, period_end,
                Json(statement), Json(sap_txns), Json(summary),
                sap_closing_balance, needs_review, status,
            ),
        )
        report_id = cur.fetchone()[0]

        if matches:
            rows = [
                (
                    report_id,
                    (m.get("sap_txn") or {}).get("id"),
                    (m.get("sap_txn") or {}).get("type"),
                    m.get("bank_idx"),
                    (m.get("statement_row") or {}).get("debit_amount") or (m.get("statement_row") or {}).get("credit_amount"),
                    m.get("confidence"),
                    m.get("reason"),
                    bool(m.get("manual")),
                    Json(m.get("sap_txn") or {}),
                    Json(m.get("statement_row") or {}),
                )
                for m in matches
            ]
            cur.executemany(
                """
                INSERT INTO sap_recon_matches
                    (report_id, sap_txn_id, sap_txn_type, statement_idx, amount,
                     confidence, reason, manual, sap_txn_json, statement_row_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

        conn.commit()
        cur.close()
        return str(report_id)
    finally:
        return_connection(conn)


def list_reports(user_id: str, company_code: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return report headers (no statement / sap_txns blob) for the user."""
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if company_code:
            cur.execute(
                """
                SELECT id, company_code, account_id, account_name, period_start, period_end,
                       summary_json, needs_review, status, created_at, updated_at
                FROM sap_recon_reports
                WHERE user_id = %s AND company_code = %s
                ORDER BY period_end DESC, created_at DESC
                """,
                (user_id, company_code),
            )
        else:
            cur.execute(
                """
                SELECT id, company_code, account_id, account_name, period_start, period_end,
                       summary_json, needs_review, status, created_at, updated_at
                FROM sap_recon_reports
                WHERE user_id = %s
                ORDER BY period_end DESC, created_at DESC
                """,
                (user_id,),
            )
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        return_connection(conn)


def get_report(report_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Return the full report (statement + sap_txns + matches) or None."""
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM sap_recon_reports WHERE id = %s AND user_id = %s",
            (report_id, user_id),
        )
        report = cur.fetchone()
        if not report:
            cur.close()
            return None

        cur.execute(
            "SELECT * FROM sap_recon_matches WHERE report_id = %s ORDER BY created_at",
            (report_id,),
        )
        matches = cur.fetchall()
        cur.close()

        return {**dict(report), "matches": [dict(m) for m in matches]}
    finally:
        return_connection(conn)


def delete_report(report_id: str, user_id: str) -> bool:
    conn = get_connection()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM sap_recon_reports WHERE id = %s AND user_id = %s",
            (report_id, user_id),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        cur.close()
        return deleted
    finally:
        return_connection(conn)
