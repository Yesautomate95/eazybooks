"""
Persistence layer for Zoho Books bank-reconciliation reports.

A "report" is one user's snapshot of a recon attempt against a bank account
for a specific period: the parsed statement, the Zoho txns at the time, the
auto + manual matches, and the summary. Stored as JSON so reopens don't need
Zoho refetches.

Mirrors qb_recon_store with the Zoho `organization_id` company key and
`zoho_txn*` naming.
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
    organization_id: str,
    account_id: str,
    account_name: Optional[str],
    period_start: str,
    period_end: str,
    statement: Dict[str, Any],
    zoho_txns: List[Dict[str, Any]],
    matches: List[Dict[str, Any]],
    summary: Dict[str, Any],
    zoho_closing_balance: Optional[float],
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
            INSERT INTO zoho_recon_reports
                (user_id, organization_id, account_id, account_name, period_start, period_end,
                 statement_json, zoho_txns_json, summary_json, zoho_closing_balance,
                 needs_review, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id, organization_id, account_id, account_name, period_start, period_end,
                Json(statement), Json(zoho_txns), Json(summary),
                zoho_closing_balance, needs_review, status,
            ),
        )
        report_id = cur.fetchone()[0]

        if matches:
            rows = [
                (
                    report_id,
                    (m.get("zoho_txn") or {}).get("id"),
                    (m.get("zoho_txn") or {}).get("type"),
                    m.get("bank_idx"),
                    (m.get("statement_row") or {}).get("debit_amount") or (m.get("statement_row") or {}).get("credit_amount"),
                    m.get("confidence"),
                    m.get("reason"),
                    bool(m.get("manual")),
                    Json(m.get("zoho_txn") or {}),
                    Json(m.get("statement_row") or {}),
                )
                for m in matches
            ]
            cur.executemany(
                """
                INSERT INTO zoho_recon_matches
                    (report_id, zoho_txn_id, zoho_txn_type, statement_idx, amount,
                     confidence, reason, manual, zoho_txn_json, statement_row_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

        conn.commit()
        cur.close()
        return str(report_id)
    finally:
        return_connection(conn)


def list_reports(user_id: str, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return report headers (no statement / zoho_txns blob) for the user."""
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if organization_id:
            cur.execute(
                """
                SELECT id, organization_id, account_id, account_name, period_start, period_end,
                       summary_json, needs_review, status, created_at, updated_at
                FROM zoho_recon_reports
                WHERE user_id = %s AND organization_id = %s
                ORDER BY period_end DESC, created_at DESC
                """,
                (user_id, organization_id),
            )
        else:
            cur.execute(
                """
                SELECT id, organization_id, account_id, account_name, period_start, period_end,
                       summary_json, needs_review, status, created_at, updated_at
                FROM zoho_recon_reports
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
    """Return the full report (statement + zoho_txns + matches) or None."""
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM zoho_recon_reports WHERE id = %s AND user_id = %s",
            (report_id, user_id),
        )
        report = cur.fetchone()
        if not report:
            cur.close()
            return None

        cur.execute(
            "SELECT * FROM zoho_recon_matches WHERE report_id = %s ORDER BY created_at",
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
            "DELETE FROM zoho_recon_reports WHERE id = %s AND user_id = %s",
            (report_id, user_id),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        cur.close()
        return deleted
    finally:
        return_connection(conn)
