"""
Xero Bank Reconciliation REST controller.

Clone of qb_bank_recon_controller (itself a clone of the Tally bank_recon flow)
for Xero. All routes are prefixed /xero/bank-recon and registered under /ca in
app/__init__.py, so the full paths are /ca/xero/bank-recon/*.

Flow mirrors Tally/QBO exactly:
    accounts -> create report -> process -> review matches -> summary -> save
The only ERP-specific difference is the connectivity gate: instead of Tally's
desktop-bridge check, we check the Xero OAuth connection and fall back to an
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
import applicationca.services.xero_bank_recon_service as svc
from applicationca.services.xero_bank import list_bank_accounts
from applicationca.services.xero_connector import (
    is_user_connected as xero_is_user_connected,
    list_connections,
)
from applicationca.agents.document_specific_functions.bank_reconciliation import get_financial_year
from applicationca.rbac import require_permission

router = APIRouter(prefix="/xero/bank-recon", tags=["Xero Bank Reconciliation"])
logger = logging.getLogger(__name__)
security = HTTPBearer()


def _auth(credentials: HTTPAuthorizationCredentials):
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_data


async def _resolve_tenant(user_id: str, tenant_id: Optional[str]) -> str:
    """Resolve the active tenant id. When the caller omits it we fall back to the
    user's first Xero tenant (from GET /connections). Raises 400 if the user has
    none."""
    if tenant_id:
        return tenant_id
    tenants = await list_connections(user_id)
    if not tenants:
        raise HTTPException(status_code=400, detail="No Xero organisation connected")
    return str(tenants[0].get("tenantId"))


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
    book_txn_id: int
    bank_txn_id: int
    notes: Optional[str] = ""


class PendingBody(BaseModel):
    txn_ids: List[int]


# ---------------------------------------------------------------------------
# Bank account list
# ---------------------------------------------------------------------------

@router.get("/accounts", dependencies=[Depends(require_permission("xero.read"))])
async def get_bank_accounts(
    tenant_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Bank accounts the user can reconcile against."""
    user_data = _auth(credentials)
    tenant = await _resolve_tenant(user_data["uid"], tenant_id)
    try:
        accounts = await list_bank_accounts(user_data["uid"], tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"accounts": accounts}


# ---------------------------------------------------------------------------
# Next report serial
# ---------------------------------------------------------------------------

@router.get("/next-report-name", dependencies=[Depends(require_permission("xero.read"))])
async def next_report_name(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    name = svc.get_next_report_serial(user_data["uid"], date.today())
    return {"report_name": name}


# ---------------------------------------------------------------------------
# Report CRUD
# ---------------------------------------------------------------------------

@router.get("/reports", dependencies=[Depends(require_permission("xero.read"))])
async def list_reports(
    account_id: str,
    tenant_id: Optional[str] = "",
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    reports = svc.get_reports(user_data["uid"], tenant_id or "", account_id)
    for r in reports:
        for f in ("period_from", "period_to", "created_at", "updated_at"):
            if r.get(f) and hasattr(r[f], "isoformat"):
                r[f] = r[f].isoformat()
        for f in ("book_opening_balance", "book_closing_balance",
                  "bank_opening_balance", "bank_closing_balance", "reconciliation_difference"):
            if r.get(f) is not None:
                r[f] = float(r[f])
    return {"reports": reports, "total": len(reports)}


@router.post("/reports", dependencies=[Depends(require_permission("xero.push"))])
async def create_report(
    account_id: str = Form(...),
    report_name: str = Form(...),
    period_from: str = Form(...),  # YYYY-MM-DD
    period_to: str = Form(...),    # YYYY-MM-DD
    statement_file: UploadFile = File(...),
    tenant_id: Optional[str] = Form(""),
    account_name: Optional[str] = Form(None),
    xero_closing_balance: Optional[float] = Form(None),
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
        f"xero-bank-recon/{user_data['uid']}/{(tenant_id or 'default')}/"
        f"{report_name}/{statement_file.filename}"
    )
    try:
        file_url = svc._upload_to_s3(file_content, s3_key, content_type)
    except Exception as e:
        logger.warning(f"S3 upload skipped ({e}); storing file in memory")
        file_url = svc.store_pending_file(file_content, statement_file.filename)

    report = svc.create_report(
        user_id=user_data["uid"],
        tenant_id=tenant_id or "",
        account_id=account_id,
        account_name=account_name,
        report_name=report_name,
        period_from=pf,
        period_to=pt,
        financial_year=fy,
        file_url=file_url,
        file_name=statement_file.filename,
        xero_closing_balance_override=xero_closing_balance,
    )
    if not report:
        raise HTTPException(status_code=500, detail="Failed to create report")

    return {**report, "message": "Report created. Call /process to start matching."}


@router.get("/reports/{report_id}", dependencies=[Depends(require_permission("xero.read"))])
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
              "xero_closing_balance_override",
              "book_credits_not_in_bank", "book_debits_not_in_bank",
              "bank_credits_not_in_book", "bank_debits_not_in_book"):
        if report.get(f) is not None:
            report[f] = float(report[f])
    return report


@router.patch("/reports/{report_id}", dependencies=[Depends(require_permission("xero.push"))])
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


@router.delete("/reports/{report_id}", dependencies=[Depends(require_permission("xero.push"))])
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
# Process (fetch Xero txns + run matching)
# ---------------------------------------------------------------------------

@router.post("/reports/{report_id}/process", dependencies=[Depends(require_permission("xero.push"))])
async def process_report(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Parse statement + fetch Xero txns + match. If the Xero connection is not
    available, mark the report awaiting_source and let the user retry — bank
    reconciliation needs the book transactions, which only Xero can supply."""
    user_data = _auth(credentials)
    uid = user_data["uid"]

    if not xero_is_user_connected(uid):
        try:
            svc.update_report(report_id, uid, {"status": "awaiting_source"})
        except Exception:
            pass
        return JSONResponse(
            {
                "report_id": report_id,
                "status": "awaiting_source",
                "message": (
                    "Xero is not connected. The bank statement is saved; "
                    "reconnect Xero and run process again to fetch book "
                    "transactions and match."
                ),
            },
            status_code=202,
        )

    report = svc.get_report(report_id, uid)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    result = await svc.process_report(report_id, uid, report.get("tenant_id"))
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@router.get("/reports/{report_id}/transactions", dependencies=[Depends(require_permission("xero.read"))])
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

@router.get("/reports/{report_id}/matches", dependencies=[Depends(require_permission("xero.read"))])
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


@router.patch("/matches/{match_id}", dependencies=[Depends(require_permission("xero.push"))])
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


@router.post("/matches/bulk", dependencies=[Depends(require_permission("xero.push"))])
async def bulk_update_matches(
    body: BulkMatchBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    _auth(credentials)
    if body.status not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="status must be 'accepted' or 'rejected'")
    updated = svc.bulk_update_match_status(body.match_ids, body.status)
    return {"updated": updated}


@router.post("/reports/{report_id}/manual-match", dependencies=[Depends(require_permission("xero.push"))])
async def create_manual_match(
    report_id: int,
    body: ManualMatchBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    result = svc.create_manual_match(
        report_id=report_id,
        book_txn_id=body.book_txn_id,
        bank_txn_id=body.bank_txn_id,
        notes=body.notes or "",
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to create manual match")
    return result


@router.delete("/matches/{match_id}", dependencies=[Depends(require_permission("xero.push"))])
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
# Pending review (triage leftover unmatched rows — no Xero writes)
# ---------------------------------------------------------------------------

@router.post("/reports/{report_id}/mark-pending", dependencies=[Depends(require_permission("xero.push"))])
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


@router.post("/reports/{report_id}/unmark-pending", dependencies=[Depends(require_permission("xero.push"))])
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

@router.get("/reports/{report_id}/summary", dependencies=[Depends(require_permission("xero.read"))])
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

@router.post("/reports/{report_id}/save", dependencies=[Depends(require_permission("xero.push"))])
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
