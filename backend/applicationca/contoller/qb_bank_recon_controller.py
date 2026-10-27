"""
QuickBooks Bank Reconciliation REST controller.

Clone of bank_recon_controller (the Tally flow) for QBO. All routes are
prefixed /qb/bank-recon and registered under /ca in app/__init__.py, so the
full paths are /ca/qb/bank-recon/*.

Flow mirrors Tally exactly:
    accounts -> create report -> process -> review matches -> summary -> save
The only ERP-specific difference is the connectivity gate: instead of Tally's
desktop-bridge check, we check the QBO OAuth connection and fall back to an
`awaiting_source` status when the user isn't connected.
"""

import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from utils.auth_utils import verify_jwt
import applicationca.services.qb_bank_recon_service as svc
from applicationca.services.qbo_bank import list_bank_accounts
from applicationca.services.qbo_connector import qbo_connector
from applicationca.services.qbo_connector import is_user_connected as qbo_is_user_connected
from applicationca.agents.document_specific_functions.bank_reconciliation import get_financial_year
from applicationca.rbac import require_permission

router = APIRouter(prefix="/qb/bank-recon", tags=["QuickBooks Bank Reconciliation"])
logger = logging.getLogger(__name__)
security = HTTPBearer()


def _auth(credentials: HTTPAuthorizationCredentials):
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_data


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class UpdateReportBody(BaseModel):
    report_name: Optional[str] = None
    status: Optional[str] = None


class MatchStatusBody(BaseModel):
    status: str  # accepted | rejected


class BulkMatchBody(BaseModel):
    match_ids: List[int]
    status: str


class ManualMatchBody(BaseModel):
    # Many-to-many: arrays are preferred. Single-id fields kept for back-compat.
    book_txn_ids: Optional[List[int]] = None
    bank_txn_ids: Optional[List[int]] = None
    book_txn_id: Optional[int] = None
    bank_txn_id: Optional[int] = None
    notes: Optional[str] = ""


class AdjustmentBody(BaseModel):
    date: str                       # DD/MM/YYYY or YYYY-MM-DD
    description: str
    voucher_type: str               # Receipt | Payment | Contra | Journal
    amount: float
    party_ledger: str               # QB account name (Income/Expense/etc.)
    party_ledger_id: Optional[str] = None
    reference: Optional[str] = ""
    bank_txn_ids: Optional[List[int]] = None   # bank rows this adjustment reconciles
    auto_create_masters: bool = True
    auto_match: bool = False         # default: leave the new row for manual reconcile


class PendingBody(BaseModel):
    txn_ids: List[int]


class BankEntryBody(BaseModel):
    date: str                       # DD/MM/YYYY or YYYY-MM-DD
    description: str
    reference: Optional[str] = ""
    debit_amount: Optional[float] = None    # exactly one of debit/credit, > 0
    credit_amount: Optional[float] = None


# ---------------------------------------------------------------------------
# Bank account list
# ---------------------------------------------------------------------------

@router.get("/accounts", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_bank_accounts(
    qb_company: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Bank + CreditCard accounts the user can reconcile against."""
    user_data = _auth(credentials)
    try:
        accounts = await list_bank_accounts(user_data["uid"], qb_company)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"accounts": accounts}


# Account types an adjusting entry can post its non-bank side against. Banks,
# A/R and A/P are excluded — those are reached via the bank account itself or a
# vendor/customer, not picked as the "other side" of an adjustment.
_POSTING_ACCOUNT_TYPES = {
    "Income", "Other Income",
    "Expense", "Other Expense", "Cost of Goods Sold",
    "Other Current Asset", "Other Current Liability", "Fixed Asset",
    "Long Term Liability", "Equity",
}


@router.get("/posting-accounts", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_posting_accounts(
    qb_company: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Active income/expense (and other postable) accounts for the adjusting-entry
    'Account' dropdown. The user can still type a new name (created on post)."""
    user_data = _auth(credentials)
    try:
        raw = await qbo_connector.list_accounts(user_data["uid"], qb_company)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    out = []
    for a in raw:
        if a.get("Active") is False:
            continue
        if a.get("AccountType") not in _POSTING_ACCOUNT_TYPES:
            continue
        out.append({
            "id": a.get("Id"),
            "name": a.get("FullyQualifiedName") or a.get("Name"),
            "account_type": a.get("AccountType"),
            "account_sub_type": a.get("AccountSubType"),
        })
    out.sort(key=lambda x: (x["account_type"] or "", x["name"] or ""))
    return {"accounts": out}


# ---------------------------------------------------------------------------
# Next report serial
# ---------------------------------------------------------------------------

@router.get("/next-report-name", dependencies=[Depends(require_permission("quickbooks.read"))])
async def next_report_name(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    name = svc.get_next_report_serial(user_data["uid"], date.today())
    return {"report_name": name}


# ---------------------------------------------------------------------------
# Report CRUD
# ---------------------------------------------------------------------------

@router.get("/reports", dependencies=[Depends(require_permission("quickbooks.read"))])
async def list_reports(
    account_id: str,
    qb_company: Optional[str] = "",
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    reports = svc.get_reports(user_data["uid"], qb_company or "", account_id)
    for r in reports:
        for f in ("period_from", "period_to", "created_at", "updated_at"):
            if r.get(f) and hasattr(r[f], "isoformat"):
                r[f] = r[f].isoformat()
        for f in ("book_opening_balance", "book_closing_balance",
                  "bank_opening_balance", "bank_closing_balance", "reconciliation_difference"):
            if r.get(f) is not None:
                r[f] = float(r[f])
    return {"reports": reports, "total": len(reports)}


@router.post("/reports", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_report(
    account_id: str = Form(...),
    report_name: str = Form(...),
    period_from: str = Form(...),  # YYYY-MM-DD
    period_to: str = Form(...),    # YYYY-MM-DD
    statement_file: UploadFile = File(...),
    qb_company: Optional[str] = Form(""),
    account_name: Optional[str] = Form(None),
    qbo_closing_balance: Optional[float] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)

    allowed_exts = {".pdf", ".xlsx", ".xls"}
    fname_lower = statement_file.filename.lower()
    if not any(fname_lower.endswith(ext) for ext in allowed_exts):
        raise HTTPException(status_code=400, detail="Only PDF and Excel files are supported")

    try:
        pf = date.fromisoformat(period_from)
        pt = date.fromisoformat(period_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    if pf > pt:
        raise HTTPException(status_code=400, detail="period_from must be before period_to")

    fy = get_financial_year(pf)

    file_content = await statement_file.read()
    content_type = "application/pdf" if fname_lower.endswith(".pdf") else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    s3_key = (
        f"qb-bank-recon/{user_data['uid']}/{(qb_company or 'default')}/"
        f"{report_name}/{statement_file.filename}"
    )
    try:
        file_url = svc._upload_to_s3(file_content, s3_key, content_type)
    except Exception as e:
        logger.warning(f"S3 upload skipped ({e}); storing file in memory")
        file_url = svc.store_pending_file(file_content, statement_file.filename)

    report = svc.create_report(
        user_id=user_data["uid"],
        realm_id=qb_company or "",
        account_id=account_id,
        account_name=account_name,
        report_name=report_name,
        period_from=pf,
        period_to=pt,
        financial_year=fy,
        file_url=file_url,
        file_name=statement_file.filename,
        qbo_closing_balance_override=qbo_closing_balance,
    )
    if not report:
        raise HTTPException(status_code=500, detail="Failed to create report")

    return {**report, "message": "Report created. Call /process to start matching."}


@router.get("/reports/{report_id}", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_report(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    for f in ("period_from", "period_to", "created_at", "updated_at"):
        if report.get(f) and hasattr(report[f], "isoformat"):
            report[f] = report[f].isoformat()
    for f in ("book_opening_balance", "book_closing_balance",
              "bank_opening_balance", "bank_closing_balance", "reconciliation_difference",
              "qbo_closing_balance_override",
              "book_credits_not_in_bank", "book_debits_not_in_bank",
              "bank_credits_not_in_book", "bank_debits_not_in_book"):
        if report.get(f) is not None:
            report[f] = float(report[f])
    return report


@router.patch("/reports/{report_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def update_report(
    report_id: int,
    body: UpdateReportBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    fields = {k: v for k, v in body.dict().items() if v is not None}
    ok = svc.update_report(report_id, user_data["uid"], fields)
    if not ok:
        raise HTTPException(status_code=404, detail="Report not found or no changes")
    return {"success": True}


@router.delete("/reports/{report_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def delete_report(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    ok = svc.delete_report(report_id, user_data["uid"])
    if not ok:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"success": True, "message": "Report deleted"}


# ---------------------------------------------------------------------------
# Process (fetch QBO txns + run matching)
# ---------------------------------------------------------------------------

@router.post("/reports/{report_id}/process", dependencies=[Depends(require_permission("quickbooks.push"))])
async def process_report(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Parse statement + fetch QBO txns + match. If the QBO connection is not
    available, mark the report awaiting_source and let the user retry — bank
    reconciliation needs the book transactions, which only QBO can supply."""
    user_data = _auth(credentials)
    uid = user_data["uid"]

    if not qbo_is_user_connected(uid):
        try:
            svc.update_report(report_id, uid, {"status": "awaiting_source"})
        except Exception:
            pass
        return JSONResponse(
            {
                "report_id": report_id,
                "status": "awaiting_source",
                "message": (
                    "QuickBooks is not connected. The bank statement is saved; "
                    "reconnect QuickBooks and run process again to fetch book "
                    "transactions and match."
                ),
            },
            status_code=202,
        )

    report = svc.get_report(report_id, uid)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    result = await svc.process_report(report_id, uid, report.get("realm_id"))
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@router.get("/reports/{report_id}/transactions", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_transactions(
    report_id: int,
    source: Optional[str] = None,
    recon_status: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    txns = svc.get_transactions(report_id, source=source, recon_status=recon_status)
    for t in txns:
        for f in ("transaction_date", "created_at"):
            if t.get(f) and hasattr(t[f], "isoformat"):
                t[f] = t[f].isoformat()
        for f in ("debit_amount", "credit_amount"):
            if t.get(f) is not None:
                t[f] = float(t[f])
    return {"transactions": txns, "total": len(txns)}


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------

@router.get("/reports/{report_id}/matches", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_matches(
    report_id: int,
    status: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    matches = svc.get_matches(report_id, status=status)
    pending = sum(1 for m in matches if m["status"] == "pending_review")
    accepted = sum(1 for m in matches if m["status"] == "accepted")
    rejected = sum(1 for m in matches if m["status"] == "rejected")
    return {
        "matches": matches,
        "total": len(matches),
        "pending_review_count": pending,
        "accepted_count": accepted,
        "rejected_count": rejected,
    }


@router.patch("/matches/{match_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def update_match_status(
    match_id: int,
    body: MatchStatusBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    _auth(credentials)
    if body.status not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="status must be 'accepted' or 'rejected'")
    result = svc.update_match_status(match_id, body.status)
    if not result:
        raise HTTPException(status_code=404, detail="Match not found")
    return result


@router.post("/matches/bulk", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bulk_update_matches(
    body: BulkMatchBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    _auth(credentials)
    if body.status not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="status must be 'accepted' or 'rejected'")
    updated = svc.bulk_update_match_status(body.match_ids, body.status)
    return {"updated": updated}


@router.post("/reports/{report_id}/manual-match", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_manual_match(
    report_id: int,
    body: ManualMatchBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    book_ids = body.book_txn_ids or ([body.book_txn_id] if body.book_txn_id else [])
    bank_ids = body.bank_txn_ids or ([body.bank_txn_id] if body.bank_txn_id else [])
    if not book_ids and not bank_ids:
        raise HTTPException(status_code=400, detail="Provide at least one book or bank transaction")

    result = svc.create_manual_match_group(
        report_id=report_id,
        book_txn_ids=book_ids,
        bank_txn_ids=bank_ids,
        notes=body.notes or "",
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to create manual match")
    return result


@router.post("/reports/{report_id}/adjustment", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_adjustment(
    report_id: int,
    body: AdjustmentBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a missing book entry, POST it to QuickBooks, and (optionally) match
    it to the bank line(s) it explains — the inline 'Create adjusting entry' flow
    of manual reconciliation."""
    user_data = _auth(credentials)
    uid = user_data["uid"]
    report = svc.get_report(report_id, uid)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if not qbo_is_user_connected(uid):
        raise HTTPException(
            status_code=400,
            detail="QuickBooks is not connected. Reconnect to post adjustments.",
        )

    result = await svc.create_adjustment(
        report_id,
        uid,
        report.get("realm_id"),
        date_str=body.date,
        description=body.description,
        voucher_type=body.voucher_type,
        amount=body.amount,
        party_ledger=body.party_ledger,
        party_ledger_id=body.party_ledger_id,
        reference=body.reference or "",
        bank_txn_ids=body.bank_txn_ids,
        auto_create_masters=body.auto_create_masters,
        auto_match=body.auto_match,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Manual bank-statement entries (local-only — no bank/QBO writes)
# ---------------------------------------------------------------------------

@router.post("/reports/{report_id}/bank-entries", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_bank_entry(
    report_id: int,
    body: BankEntryBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Add a bank-statement row the parser missed. Local-only; the row is
    flagged `manual` and can be edited/deleted while unreconciled."""
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    result = svc.create_manual_bank_entry(
        report_id,
        date_str=body.date,
        description=body.description,
        reference=body.reference or "",
        debit_amount=body.debit_amount,
        credit_amount=body.credit_amount,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.patch("/reports/{report_id}/bank-entries/{txn_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def update_bank_entry(
    report_id: int,
    txn_id: int,
    body: BankEntryBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    result = svc.update_manual_bank_entry(
        report_id,
        txn_id,
        date_str=body.date,
        description=body.description,
        reference=body.reference or "",
        debit_amount=body.debit_amount,
        credit_amount=body.credit_amount,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/reports/{report_id}/bank-entries/{txn_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def delete_bank_entry(
    report_id: int,
    txn_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    result = svc.delete_manual_bank_entry(report_id, txn_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/matches/{match_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def delete_match(
    match_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    _auth(credentials)
    ok = svc.delete_match(match_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Match not found")
    return {"success": True}


# ---------------------------------------------------------------------------
# Pending review (triage leftover unmatched rows — no QBO writes)
# ---------------------------------------------------------------------------

@router.post("/reports/{report_id}/mark-pending", dependencies=[Depends(require_permission("quickbooks.push"))])
async def mark_pending(
    report_id: int,
    body: PendingBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Set aside unmatched transactions for later review (unmatched -> pending)."""
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    updated = svc.mark_transactions_pending(report_id, body.txn_ids, pending=True)
    return {"success": True, "updated": updated}


@router.post("/reports/{report_id}/unmark-pending", dependencies=[Depends(require_permission("quickbooks.push"))])
async def unmark_pending(
    report_id: int,
    body: PendingBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return pending transactions to the active unmatched list (pending -> unmatched)."""
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    updated = svc.mark_transactions_pending(report_id, body.txn_ids, pending=False)
    return {"success": True, "updated": updated}


# ---------------------------------------------------------------------------
# Reconciliation Summary
# ---------------------------------------------------------------------------

@router.get("/reports/{report_id}/summary", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_summary(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    summary = svc.get_summary(report_id, user_data["uid"])
    if not summary:
        raise HTTPException(status_code=404, detail="Report not found")
    return summary


# ---------------------------------------------------------------------------
# Save (finalize) Report
# ---------------------------------------------------------------------------

@router.post("/reports/{report_id}/save", dependencies=[Depends(require_permission("quickbooks.push"))])
async def save_report(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    summary = svc.get_summary(report_id, user_data["uid"])
    if not summary:
        raise HTTPException(status_code=404, detail="Report not found")

    s = summary.get("summary", {})
    svc.update_report(report_id, user_data["uid"], {
        "status": "completed",
        "book_credits_not_in_bank": s.get("ledger_credits_not_in_bank", 0),
        "book_debits_not_in_bank": s.get("ledger_debits_not_in_bank", 0),
        "bank_credits_not_in_book": s.get("bank_credits_not_in_ledger", 0),
        "bank_debits_not_in_book": s.get("bank_debits_not_in_ledger", 0),
        "reconciliation_difference": s.get("reconciliation_difference", 0),
        "matched_count": summary.get("counts", {}).get("accepted", 0),
        "unmatched_book_count": summary.get("counts", {}).get("unmatched_book", 0),
        "unmatched_bank_count": summary.get("counts", {}).get("unmatched_bank", 0),
    })
    return {"success": True, "status": "completed", "report_id": report_id}


# ---------------------------------------------------------------------------
# Download Report (Excel)
# ---------------------------------------------------------------------------

@router.get("/reports/{report_id}/download", dependencies=[Depends(require_permission("reports.export"))])
async def download_report(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    xlsx_bytes = svc.generate_excel_report(report_id, user_data["uid"])
    if not xlsx_bytes:
        raise HTTPException(status_code=500, detail="Failed to generate report")

    safe_name = report["report_name"].replace(" ", "_")
    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.xlsx"'},
    )
