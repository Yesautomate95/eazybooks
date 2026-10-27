"""
Bank Reconciliation REST API controller.
All routes are prefixed with /ca (registered in app/__init__.py).
Endpoint paths: /ca/bank-recon/*
"""

import logging
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from utils.auth_utils import verify_jwt
from applicationca.contoller.websocket_controller import manager
import applicationca.services.bank_recon_service as svc
from applicationca.agents.document_specific_functions.bank_reconciliation import get_financial_year
from applicationca.rbac import require_permission

router = APIRouter()
logger = logging.getLogger(__name__)
security = HTTPBearer()


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

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
    ledger_txn_ids: Optional[List[int]] = None
    bank_txn_ids: Optional[List[int]] = None
    ledger_txn_id: Optional[int] = None
    bank_txn_id: Optional[int] = None
    notes: Optional[str] = ""


class AdjustmentBody(BaseModel):
    date: str                       # DD/MM/YYYY or YYYY-MM-DD
    description: str
    voucher_type: str               # Receipt | Payment | Contra | Journal
    amount: float
    party_ledger: str               # counter ledger name (the non-bank side)
    party_ledger_group: Optional[str] = None   # Tally group for auto-create
    reference: Optional[str] = ""
    bank_txn_ids: Optional[List[int]] = None    # bank rows this adjustment reconciles
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
# Bank Ledger list (filtered to bank-type ledgers)
# ---------------------------------------------------------------------------

@router.get("/bank-recon/bank-ledgers", dependencies=[Depends(require_permission("bookkeeping.read"))])
async def get_bank_ledgers(
    company_name: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Fetch bank-type ledgers from Tally and filter by group."""
    user_data = _auth(credentials)
    try:
        all_ledgers = await manager.get_tally_ledgers(user_data["uid"], company_name)
    except HTTPException as e:
        if e.status_code == 404:
            return JSONResponse(
                {"error": "Desktop app not connected. Please ensure it is running."},
                status_code=400,
            )
        raise

    bank_ledgers = [
        {"name": l.get("name", l) if isinstance(l, dict) else l,
         "group": l.get("group") or l.get("parent", "") if isinstance(l, dict) else ""}
        for l in all_ledgers
        if (
            (isinstance(l, dict) and (
                (l.get("group", "") or l.get("parent", "")).lower() in _BANK_GROUPS
                or "bank" in l.get("name", "").lower()
            ))
            or (isinstance(l, str) and "bank" in l.lower())
        )
    ]
    return {"bank_ledgers": bank_ledgers}


# Ledger groups that should NOT be offered as an adjustment counter-account: the
# bank side of an adjustment is fixed (it is the recon's bank ledger), so bank
# ledgers are excluded from the picker.
_BANK_GROUPS = {"bank accounts", "bank od a/c", "bank occ a/c", "bank od accounts"}


@router.get("/bank-recon/posting-accounts", dependencies=[Depends(require_permission("bookkeeping.read"))])
async def get_posting_accounts(
    company_name: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Non-bank ledgers for the adjusting-entry counter-account dropdown. The user
    can still type a new name (auto-created in Tally on post). Falls back to the
    cached ledger snapshot when the desktop bridge is briefly offline."""
    user_data = _auth(credentials)
    uid = user_data["uid"]

    raw = None
    from_cache = False
    try:
        raw = await manager.get_tally_ledgers(uid, company_name)
    except HTTPException as e:
        if e.status_code != 404:
            raise
    if raw is None:
        from_cache = True
        try:
            from applicationca.services import tally_cache_store
            cached = tally_cache_store.get_cached_ledgers(user_id=uid, company_name=company_name)
            raw = (cached or {}).get("ledgers") if cached else None
        except Exception:
            raw = None
    if not raw:
        return {"accounts": [], "from_cache": from_cache}

    accounts = []
    for l in raw:
        if isinstance(l, dict):
            name = l.get("name", "")
            group = (l.get("group") or l.get("parent") or l.get("parent_group") or "")
        else:
            name, group = str(l), ""
        if not name:
            continue
        if str(group).strip().lower() in _BANK_GROUPS:
            continue
        accounts.append({"name": name, "group": group})
    accounts.sort(key=lambda a: (a["group"] or "", a["name"] or ""))
    return {"accounts": accounts, "from_cache": from_cache}


# ---------------------------------------------------------------------------
# Next report serial
# ---------------------------------------------------------------------------

@router.get("/bank-recon/next-report-name", dependencies=[Depends(require_permission("bookkeeping.read"))])
async def next_report_name(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    name = svc.get_next_report_serial(user_data["uid"], date.today())
    return {"report_name": name}


# ---------------------------------------------------------------------------
# Report CRUD
# ---------------------------------------------------------------------------

@router.get("/bank-recon/reports", dependencies=[Depends(require_permission("bookkeeping.read"))])
async def list_reports(
    company_name: str,
    bank_ledger_name: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    reports = svc.get_reports(user_data["uid"], company_name, bank_ledger_name)
    # Serialize dates
    for r in reports:
        for f in ("period_from", "period_to", "created_at", "updated_at"):
            if r.get(f) and hasattr(r[f], "isoformat"):
                r[f] = r[f].isoformat()
        for f in ("ledger_opening_balance", "ledger_closing_balance",
                  "bank_opening_balance", "bank_closing_balance", "reconciliation_difference"):
            if r.get(f) is not None:
                r[f] = float(r[f])
    return {"reports": reports, "total": len(reports)}


@router.post("/bank-recon/reports", dependencies=[Depends(require_permission("bookkeeping.create"))])
async def create_report(
    company_name: str = Form(...),
    bank_ledger_name: str = Form(...),
    report_name: str = Form(...),
    period_from: str = Form(...),  # YYYY-MM-DD
    period_to: str = Form(...),    # YYYY-MM-DD
    statement_file: UploadFile = File(...),
    ledger_closing_balance: Optional[float] = Form(None),  # optional CB override
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)

    # Validate file type
    allowed_exts = {".pdf", ".xlsx", ".xls"}
    fname_lower = statement_file.filename.lower()
    if not any(fname_lower.endswith(ext) for ext in allowed_exts):
        raise HTTPException(status_code=400, detail="Only PDF and Excel files are supported")

    # Parse dates
    try:
        pf = date.fromisoformat(period_from)
        pt = date.fromisoformat(period_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    if pf > pt:
        raise HTTPException(status_code=400, detail="period_from must be before period_to")

    fy = get_financial_year(pf)

    # Upload to S3
    file_content = await statement_file.read()
    content_type = "application/pdf" if fname_lower.endswith(".pdf") else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    s3_key = (
        f"bank-recon/{user_data['uid']}/{company_name.replace(' ', '_')}/"
        f"{report_name}/{statement_file.filename}"
    )
    try:
        file_url = svc._upload_to_s3(file_content, s3_key, content_type)
    except Exception as e:
        logger.warning(f"S3 upload skipped ({e}); storing file in memory")
        file_url = svc.store_pending_file(file_content, statement_file.filename)

    report = svc.create_report(
        user_id=user_data["uid"],
        company_name=company_name,
        bank_ledger_name=bank_ledger_name,
        report_name=report_name,
        period_from=pf,
        period_to=pt,
        financial_year=fy,
        file_url=file_url,
        file_name=statement_file.filename,
        ledger_closing_balance_override=ledger_closing_balance,
    )
    if not report:
        raise HTTPException(status_code=500, detail="Failed to create report")

    return {**report, "message": "Report created. Call /process to start matching."}


@router.get("/bank-recon/reports/{report_id}", dependencies=[Depends(require_permission("bookkeeping.read"))])
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
    for f in ("ledger_opening_balance", "ledger_closing_balance",
              "bank_opening_balance", "bank_closing_balance", "reconciliation_difference",
              "ledger_credits_not_in_bank", "ledger_debits_not_in_bank",
              "bank_credits_not_in_ledger", "bank_debits_not_in_ledger"):
        if report.get(f) is not None:
            report[f] = float(report[f])
    return report


@router.patch("/bank-recon/reports/{report_id}", dependencies=[Depends(require_permission("bookkeeping.update"))])
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


@router.delete("/bank-recon/reports/{report_id}", dependencies=[Depends(require_permission("bookkeeping.delete"))])
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
# Process (fetch ledger transactions + run matching)
# ---------------------------------------------------------------------------

@router.post("/bank-recon/reports/{report_id}/process", dependencies=[Depends(require_permission("bookkeeping.update"))])
async def process_report(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Run the parse + Tally fetch + match pipeline. If Tally is not online,
    mark the report as awaiting_tally and let the user retry later — bank
    reconciliation needs ledger transactions, which only Tally can supply."""
    user_data = _auth(credentials)
    uid = user_data["uid"]

    # Pre-check connectivity so we don't half-process and end up in `error`.
    try:
        is_connected = await manager.check_tally_connection(uid)
    except Exception:
        is_connected = False

    if not is_connected:
        try:
            svc.update_report(report_id, uid, {"status": "awaiting_tally"})
        except Exception:
            pass
        return JSONResponse(
            {
                "report_id": report_id,
                "status": "awaiting_tally",
                "message": (
                    "Tally desktop bridge is not connected. The bank statement is "
                    "saved; run /process again once Tally is online to fetch ledger "
                    "transactions and match."
                ),
            },
            status_code=202,
        )

    result = await svc.process_report(report_id, uid, uid)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@router.get("/bank-recon/reports/{report_id}/transactions", dependencies=[Depends(require_permission("bookkeeping.read"))])
async def get_transactions(
    report_id: int,
    source: Optional[str] = None,
    recon_status: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    # Ensure report belongs to user
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

@router.get("/bank-recon/reports/{report_id}/matches", dependencies=[Depends(require_permission("bookkeeping.read"))])
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


@router.patch("/bank-recon/matches/{match_id}", dependencies=[Depends(require_permission("bookkeeping.update"))])
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


@router.post("/bank-recon/matches/bulk", dependencies=[Depends(require_permission("bookkeeping.update"))])
async def bulk_update_matches(
    body: BulkMatchBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    _auth(credentials)
    if body.status not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="status must be 'accepted' or 'rejected'")
    updated = svc.bulk_update_match_status(body.match_ids, body.status)
    return {"updated": updated}


@router.post("/bank-recon/reports/{report_id}/manual-match", dependencies=[Depends(require_permission("bookkeeping.update"))])
async def create_manual_match(
    report_id: int,
    body: ManualMatchBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    report = svc.get_report(report_id, user_data["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    ledger_ids = body.ledger_txn_ids or ([body.ledger_txn_id] if body.ledger_txn_id else [])
    bank_ids = body.bank_txn_ids or ([body.bank_txn_id] if body.bank_txn_id else [])
    if not ledger_ids and not bank_ids:
        raise HTTPException(status_code=400, detail="Provide at least one ledger or bank transaction")

    result = svc.create_manual_match_group(
        report_id=report_id,
        ledger_txn_ids=ledger_ids,
        bank_txn_ids=bank_ids,
        notes=body.notes or "",
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to create manual match")
    return result


@router.post("/bank-recon/reports/{report_id}/adjustment", dependencies=[Depends(require_permission("bookkeeping.update"))])
async def create_adjustment(
    report_id: int,
    body: AdjustmentBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a missing ledger entry, push the voucher to Tally via the desktop
    bridge, and (optionally) match it to the bank line(s) it explains — the inline
    'Create adjusting entry' flow of manual reconciliation."""
    user_data = _auth(credentials)
    uid = user_data["uid"]
    report = svc.get_report(report_id, uid)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # Posting a voucher needs the live bridge (parity with QB's connect check).
    try:
        connected = await manager.check_tally_connection(uid)
    except Exception:
        connected = False
    if not connected:
        raise HTTPException(
            status_code=400,
            detail="Tally desktop bridge is not connected. Reconnect to post adjustments.",
        )

    result = await svc.create_adjustment(
        report_id, uid, uid,
        date_str=body.date,
        description=body.description,
        voucher_type=body.voucher_type,
        amount=body.amount,
        party_ledger=body.party_ledger,
        party_ledger_group=body.party_ledger_group,
        reference=body.reference or "",
        bank_txn_ids=body.bank_txn_ids,
        auto_create_masters=body.auto_create_masters,
        auto_match=body.auto_match,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Manual bank-statement entries (statement rows the parser missed — local only)
# ---------------------------------------------------------------------------

@router.post("/bank-recon/reports/{report_id}/bank-entries", dependencies=[Depends(require_permission("bookkeeping.update"))])
async def create_bank_entry(
    report_id: int,
    body: BankEntryBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Add a bank-statement row the parser missed. Local-only; the row is flagged
    `manual` and can be edited/deleted while unreconciled."""
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


@router.patch("/bank-recon/reports/{report_id}/bank-entries/{txn_id}", dependencies=[Depends(require_permission("bookkeeping.update"))])
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


@router.delete("/bank-recon/reports/{report_id}/bank-entries/{txn_id}", dependencies=[Depends(require_permission("bookkeeping.update"))])
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


@router.delete("/bank-recon/matches/{match_id}", dependencies=[Depends(require_permission("bookkeeping.update"))])
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
# Pending review (triage leftover unmatched rows — no Tally writes)
# ---------------------------------------------------------------------------

@router.post("/bank-recon/reports/{report_id}/mark-pending", dependencies=[Depends(require_permission("bookkeeping.update"))])
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


@router.post("/bank-recon/reports/{report_id}/unmark-pending", dependencies=[Depends(require_permission("bookkeeping.update"))])
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

@router.get("/bank-recon/reports/{report_id}/summary", dependencies=[Depends(require_permission("bookkeeping.read"))])
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
# Save (finalize) Report — re-openable
# ---------------------------------------------------------------------------

@router.post("/bank-recon/reports/{report_id}/save", dependencies=[Depends(require_permission("bookkeeping.update"))])
async def save_report(
    report_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_data = _auth(credentials)
    # Recompute final summary values and mark completed
    summary = svc.get_summary(report_id, user_data["uid"])
    if not summary:
        raise HTTPException(status_code=404, detail="Report not found")

    s = summary.get("summary", {})
    svc.update_report(report_id, user_data["uid"], {
        "status": "completed",
        "ledger_credits_not_in_bank": s.get("ledger_credits_not_in_bank", 0),
        "ledger_debits_not_in_bank": s.get("ledger_debits_not_in_bank", 0),
        "bank_credits_not_in_ledger": s.get("bank_credits_not_in_ledger", 0),
        "bank_debits_not_in_ledger": s.get("bank_debits_not_in_ledger", 0),
        "reconciliation_difference": s.get("reconciliation_difference", 0),
        "matched_count": summary.get("counts", {}).get("accepted", 0),
        "unmatched_ledger_count": summary.get("counts", {}).get("unmatched_ledger", 0),
        "unmatched_bank_count": summary.get("counts", {}).get("unmatched_bank", 0),
    })
    return {"success": True, "status": "completed", "report_id": report_id}


# ---------------------------------------------------------------------------
# Download Report (Excel)
# ---------------------------------------------------------------------------

@router.get("/bank-recon/reports/{report_id}/download", dependencies=[Depends(require_permission("reports.export"))])
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
