"""
SAP S/4HANA Cloud controller — the main SAP ERP path.

Endpoint-for-endpoint port of zoho_controller, cloud-only: SAP is OAuth-only (no
desktop / Electron bridge), so every endpoint talks to the S/4HANA OData / SOAP
API through `sap_connector`. The OAuth handshake itself lives in
sap_oauth_controller; this module assumes the user already holds a token (or, in
client_credentials mode, that the server is configured).

Company key is `company_code` (a SAP Company Code, e.g. '1000') everywhere Zoho
used organization_id / Xero used tenant_id. The frontend passes it as a query
param (GET) or in the request body (POST); when omitted we fall back to
settings.SAP_DEFAULT_COMPANY_CODE.

SAP FI data-model differences from the Zoho/Xero invoice model:
  * Sales / customer (A/R) invoice  -> post_journal_entry doc_type='DR'
    (debit the debtor BP, credit revenue + tax GL accounts; must balance).
  * Purchase / supplier (A/P) invoice -> post_supplier_invoice (OData), each
    expense GL line a debit; SAP credits the supplier A/P recon account.
  * Bank entries -> post_journal_entry doc_type='SA' (generic GL JE).
  * GL accounts ARE the line ledgers; there is no separate item master, so the
    pipeline matches GL accounts where Zoho matched items.

Routes are namespaced /ca/sap/* (registered with the /ca prefix in app/__init__.py).
"""

import json
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from rapidfuzz import fuzz

from applicationca.agents.sap.bank_entries import (
    process_bank_statement_for_entries,
    push_one_entry as bank_entries_push_one,
)
from applicationca.agents.sap.bank_recon import (
    manual_match as bank_manual_match,
    process_bank_statement,
    recompute_summary as bank_recompute_summary,
)
from applicationca.agents.sap.chat import run_sap_chat
from applicationca.agents.sap.purchase import process_purchase_pdf
from applicationca.agents.sap.sales import process_sales_pdf
from applicationca.services import sap_chat_store
from applicationca.services import sap_drafts_store
from applicationca.services import sap_duplicate_check
from applicationca.services.ledger_matcher import normalize_name
from applicationca.services.sap_aliases import ENTITY_TYPES as ALIAS_TYPES, save_alias as alias_save
from applicationca.services.sap_bank import list_bank_accounts
from applicationca.services.sap_brief import load_brief as sap_load_brief, refresh_brief as sap_refresh_brief
from applicationca.services.sap_connector import (
    is_user_connected as sap_is_user_connected,
    sap_connector,
)
from applicationca.services.sap_recon_store import (
    delete_report as recon_delete_report,
    get_report as recon_get_report,
    list_reports as recon_list_reports,
    save_report as recon_save_report,
)
from applicationca.services.sap_suggestion_service import record_from_bill_payload
from config.settings import settings
from utils.auth_utils import verify_jwt
from applicationca.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sap", tags=["SAP S/4HANA"])
security = HTTPBearer()


def _verify(credentials: HTTPAuthorizationCredentials):
    user = verify_jwt(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


def _resolve_company_code(company_code: Optional[str]) -> str:
    """Resolve the active Company Code. When the caller omits it we fall back to
    settings.SAP_DEFAULT_COMPANY_CODE. Raises 400 when neither is available."""
    cc = company_code or settings.SAP_DEFAULT_COMPANY_CODE
    if not cc:
        raise HTTPException(
            status_code=400,
            detail="No Company Code provided and SAP_DEFAULT_COMPANY_CODE is unset",
        )
    return cc


# ---------------------------------------------------------------------------
# Pydantic payloads (mirror Zoho's, with company_code)
# ---------------------------------------------------------------------------

class CreatePartyRequest(BaseModel):
    name: str
    company_name: Optional[str] = None
    email: Optional[str] = None
    company_code: Optional[str] = None  # which SAP company code to write to


class CreateAccountRequest(BaseModel):
    name: str
    account_type: str  # kept for shape parity; SAP GL create raises 501
    company_code: Optional[str] = None


class CreateItemRequest(BaseModel):
    """Kept for shape parity with Zoho. SAP FI uses GL accounts, not item masters,
    so create_item raises 501 in the connector."""
    name: str
    rate: Optional[float] = None
    description: Optional[str] = None
    company_code: Optional[str] = None


class AliasSaveRequest(BaseModel):
    """Persist a user-confirmed (alias text) -> (SAP entity id) mapping so the
    matcher resolves the same variant text instantly on future runs."""
    entity_type: str  # customer | vendor | item | account
    alias_text: str
    entity_id: str
    entity_name: str
    entity_code: Optional[str] = None
    company_code: Optional[str] = None


class CreateBatchItem(BaseModel):
    entity_type: str  # customer | vendor | item | account
    name: str
    company_name: Optional[str] = None
    email: Optional[str] = None
    account_type: Optional[str] = None  # for account
    rate: Optional[float] = None        # for item


class CreateBatchRequest(BaseModel):
    """Fan-out create across multiple master-data types in one call."""
    items: List[CreateBatchItem]
    company_code: Optional[str] = None


class PreviewCreateAccountRequest(BaseModel):
    proposed_name: str
    account_type: Optional[str] = None  # if set, only match against same-type accounts
    company_code: Optional[str] = None


class PushSapRequest(BaseModel):
    """Push a SAP document. `sap_payload` is the resource body the pipeline
    produced (the sales/purchase agent's built doc: header + debit/credit items,
    with display names on a `_meta` block we strip before posting).

    `extracted_total` is what the extractor computed (gross, includes tax) and is
    what `check_*` queried against. We thread it through so `record_pushed` stores
    the same number, keeping dedupe keys aligned across extracts.
    """
    sap_payload: Optional[dict] = None
    company_code: Optional[str] = None
    extracted_total: Optional[float] = None
    skip_duplicate_check: bool = False


# ---------------------------------------------------------------------------
# Connection / company
# ---------------------------------------------------------------------------

@router.get("/check-connection", dependencies=[Depends(require_permission("sap.read"))])
async def check_connection(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Connection check: does the user have stored SAP tokens (or is the server
    configured for client_credentials), and does the token reach the API?"""
    user = _verify(credentials)
    if not sap_is_user_connected(user["uid"]):
        return {
            "connected": False,
            "mode": settings.SAP_AUTH_MODE,
            "message": "SAP not connected — start the OAuth flow",
        }
    try:
        await sap_connector.ping(user["uid"])
        return {
            "connected": True,
            "mode": settings.SAP_AUTH_MODE,
            "api_base": settings.SAP_API_BASE,
            "message": "SAP S/4HANA connected",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"SAP ping failed: {exc}")
        return {"connected": False, "mode": settings.SAP_AUTH_MODE, "reason": "ping_failed"}


@router.get("/get-companies", dependencies=[Depends(require_permission("sap.read"))])
async def get_companies(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """List the company codes the user's token can write to."""
    user = _verify(credentials)
    try:
        codes = await sap_connector.list_companies(user["uid"])
        organizations = [
            {
                "organization_id": str(c.get("company_code")),
                "company_code": str(c.get("company_code")),
                "name": c.get("name") or str(c.get("company_code")),
            }
            for c in codes
        ]
        return {"organizations": organizations}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"get_companies failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------

@router.get("/get-accounts", dependencies=[Depends(require_permission("sap.read"))])
async def get_accounts(
    company_code: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    rows = await sap_connector.list_accounts(user["uid"], cc)
    return {"accounts": rows}


@router.get("/get-customers", dependencies=[Depends(require_permission("sap.read"))])
async def get_customers(
    company_code: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    rows = await sap_connector.list_customers(user["uid"], cc)
    return {"customers": rows}


@router.get("/get-vendors", dependencies=[Depends(require_permission("sap.read"))])
async def get_vendors(
    company_code: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    rows = await sap_connector.list_vendors(user["uid"], cc)
    return {"vendors": rows}


@router.get("/get-items", dependencies=[Depends(require_permission("sap.read"))])
async def get_items(
    company_code: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """SAP FI has no item master; the connector returns [] so the shared pipeline
    (which treats line items as GL-account matches) degrades cleanly."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    rows = await sap_connector.list_items(user["uid"], cc)
    return {"items": rows}


# ---------------------------------------------------------------------------
# Master writes
# ---------------------------------------------------------------------------

@router.post("/create-customer", dependencies=[Depends(require_permission("sap.push"))])
async def create_customer(
    body: CreatePartyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)
    sap_body: Dict = {"name": body.name}
    if body.company_name:
        sap_body["company_name"] = body.company_name
    if body.email:
        sap_body["email"] = body.email
    result = await sap_connector.create_customer(user["uid"], sap_body, cc)
    return {"customer": result}


@router.post("/create-vendor", dependencies=[Depends(require_permission("sap.push"))])
async def create_vendor(
    body: CreatePartyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)
    sap_body: Dict = {"name": body.name}
    if body.company_name:
        sap_body["company_name"] = body.company_name
    if body.email:
        sap_body["email"] = body.email
    result = await sap_connector.create_vendor(user["uid"], sap_body, cc)
    return {"vendor": result}


@router.post("/create-account", dependencies=[Depends(require_permission("sap.push"))])
async def create_account(
    body: CreateAccountRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """GL account creation is not available via API in S/4HANA Cloud Public; the
    connector raises HTTP 501. Endpoint kept for shape parity with Zoho."""
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)
    sap_body = {"name": body.name, "account_type": body.account_type}
    result = await sap_connector.create_account(user["uid"], sap_body, cc)
    return {"account": result}


@router.post("/create-item", dependencies=[Depends(require_permission("sap.push"))])
async def create_item_endpoint(
    body: CreateItemRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """SAP FI has no item master; the connector raises HTTP 501. Endpoint kept
    for shape parity with Zoho."""
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)
    sap_body: Dict = {"name": body.name}
    if body.rate is not None:
        sap_body["rate"] = body.rate
    if body.description:
        sap_body["description"] = body.description
    result = await sap_connector.create_item(user["uid"], sap_body, cc)
    return {"item": result}


@router.post("/alias", dependencies=[Depends(require_permission("sap.push"))])
async def save_alias_endpoint(
    body: AliasSaveRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Persist a confirmed alias so future extractions of the same variant text
    auto-resolve to the same SAP entity (Tier 4)."""
    user = _verify(credentials)
    if body.entity_type not in ALIAS_TYPES:
        raise HTTPException(status_code=400, detail=f"entity_type must be one of {ALIAS_TYPES}")
    cc = body.company_code or ""
    ok = alias_save(
        user_id=user["uid"],
        company_code=cc,
        entity_type=body.entity_type,
        alias_text=body.alias_text,
        entity_id=body.entity_id,
        entity_name=body.entity_name,
        entity_code=body.entity_code,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="failed to save alias")
    return {"saved": True}


@router.post("/create-batch", dependencies=[Depends(require_permission("sap.push"))])
async def create_batch_endpoint(
    body: CreateBatchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Bulk-create master entities (Tier 5). Each item in `items` declares its
    own entity_type and gets routed to the correct connector method. Returns a
    per-item result so the frontend can patch IDs and show per-row status
    instead of failing the whole batch on one bad row. (account / item rows
    surface SAP's 501 as a per-row error.)"""
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)
    results: List[Dict] = []

    for item in body.items:
        try:
            if item.entity_type == "customer":
                sap_body = {"name": item.name}
                if item.company_name:
                    sap_body["company_name"] = item.company_name
                if item.email:
                    sap_body["email"] = item.email
                created = await sap_connector.create_customer(user["uid"], sap_body, cc)
                results.append({
                    "ok": True,
                    "entity_type": "customer",
                    "name": item.name,
                    "entity_id": str(created.get("list_id")) if created.get("list_id") is not None else None,
                    "entity_name": created.get("name") or item.name,
                })
            elif item.entity_type == "vendor":
                sap_body = {"name": item.name}
                if item.company_name:
                    sap_body["company_name"] = item.company_name
                if item.email:
                    sap_body["email"] = item.email
                created = await sap_connector.create_vendor(user["uid"], sap_body, cc)
                results.append({
                    "ok": True,
                    "entity_type": "vendor",
                    "name": item.name,
                    "entity_id": str(created.get("list_id")) if created.get("list_id") is not None else None,
                    "entity_name": created.get("name") or item.name,
                })
            elif item.entity_type == "account":
                if not item.account_type:
                    raise ValueError("account_type is required for account creation")
                sap_body = {"name": item.name, "account_type": item.account_type}
                created = await sap_connector.create_account(user["uid"], sap_body, cc)
                results.append({
                    "ok": True,
                    "entity_type": "account",
                    "name": item.name,
                    "entity_id": str(created.get("list_id")) if created.get("list_id") is not None else None,
                    "entity_name": created.get("name") or item.name,
                })
            elif item.entity_type == "item":
                sap_body = {"name": item.name}
                if item.rate is not None:
                    sap_body["rate"] = item.rate
                created = await sap_connector.create_item(user["uid"], sap_body, cc)
                results.append({
                    "ok": True,
                    "entity_type": "item",
                    "name": item.name,
                    "entity_id": str(created.get("list_id")) if created.get("list_id") is not None else None,
                    "entity_name": created.get("name") or item.name,
                })
            else:
                raise ValueError(f"unsupported entity_type: {item.entity_type!r}")
        except HTTPException as exc:
            results.append({
                "ok": False,
                "entity_type": item.entity_type,
                "name": item.name,
                "error": exc.detail if isinstance(exc.detail, str) else "create failed",
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"create-batch row failed for {item.entity_type}:{item.name}")
            results.append({
                "ok": False,
                "entity_type": item.entity_type,
                "name": item.name,
                "error": str(exc),
            })

    return {"results": results}


_PREVIEW_SIMILARITY_FLOOR = 0.80


@router.post("/preview-create-account", dependencies=[Depends(require_permission("sap.read"))])
async def preview_create_account(
    body: PreviewCreateAccountRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Look for likely-duplicate GL accounts before the user creates a new one.
    Returns the top fuzzy matches (similarity >= 0.80) so the UI can suggest
    'did you mean ...?'. Idempotent: nothing is created here. (SAP GL create is
    unavailable via API, but the preview still helps pick an existing account.)"""
    proposed = (body.proposed_name or "").strip()
    if not proposed:
        return {"closest_matches": []}
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)

    accounts = await sap_connector.list_accounts(user["uid"], cc)
    target_type = (body.account_type or "").strip() or None
    target_norm = normalize_name(proposed)

    scored: List[Dict[str, object]] = []
    for a in accounts:
        name = a.get("name") or ""
        if not name:
            continue
        if target_type and a.get("account_type") and a.get("account_type") != target_type:
            continue
        cand_norm = normalize_name(name)
        if not cand_norm:
            continue
        similarity = fuzz.token_set_ratio(target_norm, cand_norm) / 100.0
        if similarity >= _PREVIEW_SIMILARITY_FLOOR:
            scored.append({
                "list_id": a.get("list_id"),
                "name": name,
                "account_type": a.get("account_type"),
                "similarity": round(similarity, 3),
            })

    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return {"closest_matches": scored[:3]}


# ---------------------------------------------------------------------------
# Document extraction (PDF -> structured rows + sap_doc / sap_payload)
# ---------------------------------------------------------------------------

@router.post("/extract-invoice", dependencies=[Depends(require_permission("sap.push"))])
async def extract_invoice_endpoint(
    file: UploadFile = File(...),
    company_code: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upload a sales-invoice PDF, extract fields, match to a SAP customer +
    revenue GL accounts, and return a payload ready for /push-invoice (an A/R
    journal entry, doc_type 'DR')."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty PDF")
    result = await process_sales_pdf(pdf_bytes, user_id=user["uid"], company_code=cc)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@router.post("/extract-bill", dependencies=[Depends(require_permission("sap.push"))])
async def extract_bill_endpoint(
    file: UploadFile = File(...),
    company_code: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upload a purchase-bill PDF, extract fields, match to a SAP vendor +
    expense GL accounts, and return a payload ready for /push-bill (an A/P
    supplier invoice)."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty PDF")
    result = await process_purchase_pdf(pdf_bytes, user_id=user["uid"], company_code=cc)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


# ---------------------------------------------------------------------------
# Document push
# ---------------------------------------------------------------------------

def _validate_invoice_payload(payload: Dict) -> None:
    """A SAP A/R journal entry (doc_type 'DR') needs a debtor (customer) debit
    line and at least one GL credit line. Catch the common 'no customer matched'
    case before hitting SAP."""
    if not payload.get("debtor_items"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_customer",
                "message": "No SAP customer is selected for this invoice. Match or create the customer first.",
            },
        )
    if not payload.get("gl_items"):
        raise HTTPException(status_code=400, detail="invoice has no GL (revenue) lines")


def _validate_bill_payload(payload: Dict) -> None:
    """A SAP supplier invoice needs an invoicing_party (vendor BP id) and
    GL-account-based lines."""
    if not payload.get("invoicing_party"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_vendor",
                "message": "No SAP vendor is selected for this bill. Match or create the vendor first.",
            },
        )
    items = payload.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="bill has no line items")
    missing = [i for i, l in enumerate(items) if not l.get("gl_account")]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_account",
                "message": f"{len(missing)} bill line(s) have no GL account assigned. Pick an account first.",
                "line_indices": missing,
            },
        )


@router.post("/push-invoice", dependencies=[Depends(require_permission("sap.push"))])
async def push_invoice(
    body: PushSapRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a sales (A/R) invoice as a SAP journal entry (doc_type 'DR') via
    post_journal_entry. body.sap_payload = the sales agent's `sap_doc` (header +
    debtor_items + gl_items), optionally carrying a `_meta` block with display
    names which we strip before posting."""
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)
    payload = body.sap_payload
    if not payload:
        raise HTTPException(status_code=400, detail="sap_payload required for invoice push")
    _validate_invoice_payload(payload)

    # Display names ride on `_meta`; pull them for dedupe + history, then strip.
    meta = payload.get("_meta") or {}
    customer_name = meta.get("customer_name")
    doc_date = payload.get("document_date") or payload.get("posting_date")
    doc_number = payload.get("reference")

    if not body.skip_duplicate_check:
        dup = await sap_duplicate_check.check_invoice(
            user_id=user["uid"], company_code=cc,
            doc_date=doc_date,
            total_amount=body.extracted_total,
            doc_number=doc_number,
            customer_name=customer_name,
        )
        if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.7:
            raise HTTPException(status_code=409, detail={
                "message": "This invoice appears to be a duplicate and was already pushed.",
                "duplicate_check": dup,
            })

    doc = {k: v for k, v in payload.items() if k != "_meta"}
    created = await sap_connector.post_journal_entry(user["uid"], doc, cc)
    # Thread the extractor's gross total (what `check_invoice` queried) so the
    # next extract's dedupe lookup uses the same key.
    sap_entity_id = (created or {}).get("accounting_document")
    sap_duplicate_check.record_pushed(
        user_id=user["uid"], company_code=cc,
        doc_type=sap_duplicate_check.DOC_SALES,
        date_iso=doc_date,
        amount=body.extracted_total,
        doc_number=doc_number,
        party_name=customer_name,
        sap_entity_id=str(sap_entity_id) if sap_entity_id is not None else None,
    )
    return {"result": created}


@router.post("/push-bill", dependencies=[Depends(require_permission("sap.push"))])
async def push_bill(
    body: PushSapRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a purchase (A/P) supplier invoice via post_supplier_invoice.
    body.sap_payload = the purchase agent's `sap_payload` (invoicing_party +
    items + gross_amount), carrying a `_meta` block (vendor + line account names)
    which we strip before posting and reuse to record vendor->account history."""
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)
    payload = body.sap_payload
    if not payload:
        raise HTTPException(status_code=400, detail="sap_payload required for bill push")
    _validate_bill_payload(payload)

    meta = payload.get("_meta") or {}
    vendor_name = meta.get("vendor_name")
    doc_date = payload.get("document_date") or payload.get("posting_date")
    doc_number = payload.get("invoice_id")

    if not body.skip_duplicate_check:
        dup = await sap_duplicate_check.check_bill(
            user_id=user["uid"], company_code=cc,
            doc_date=doc_date,
            total_amount=body.extracted_total or payload.get("gross_amount"),
            doc_number=doc_number,
            vendor_name=vendor_name,
        )
        if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.7:
            raise HTTPException(status_code=409, detail={
                "message": "This bill appears to be a duplicate and was already pushed.",
                "duplicate_check": dup,
            })

    doc = {k: v for k, v in payload.items() if k != "_meta"}
    created = await sap_connector.post_supplier_invoice(user["uid"], doc, cc)

    # Record vendor->account + item->account history from the display names on
    # `_meta` (flattened into the shape record_from_bill_payload expects).
    history_payload = {
        "vendor_name": vendor_name,
        "vendor_id": meta.get("vendor_id"),
        "line_items": meta.get("line_items") or [],
    }
    record_from_bill_payload(
        user_id=user["uid"], company_code=cc, payload=history_payload,
    )

    total = body.extracted_total
    if total is None:
        total = payload.get("gross_amount")
    sap_entity_id = (created or {}).get("SupplierInvoice")
    sap_duplicate_check.record_pushed(
        user_id=user["uid"], company_code=cc,
        doc_type=sap_duplicate_check.DOC_PURCHASE,
        date_iso=doc_date,
        amount=total,
        doc_number=doc_number,
        party_name=vendor_name,
        sap_entity_id=str(sap_entity_id) if sap_entity_id is not None else None,
    )
    return {"result": created}


# ---------------------------------------------------------------------------
# Bank reconciliation
# ---------------------------------------------------------------------------

class ManualMatchRequest(BaseModel):
    sap_txn: dict
    statement_row: dict
    # Optional: if the frontend sends the current unmatched lists + closing
    # balances, we recompute the summary on the same call and return it. Older
    # callers that omit these still get a wrapped match dict (no summary).
    unmatched_sap: Optional[List[dict]] = None
    unmatched_statement: Optional[List[dict]] = None
    sap_closing_balance: Optional[float] = None
    bank_closing_balance: Optional[float] = None


class ResolvePendingRequest(BaseModel):
    """Mark or post leftover unmatched bank-recon rows.

    mode = "mark_pending":   no SAP writes. The frontend already moved the rows
        into its own pending list; the backend just recomputes the summary from
        the remaining unmatched lists (which exclude them).

    mode = "post_to_suspense": post one SAP journal entry (doc_type 'SA') per
        unmatched statement row, booking the bank GL leg against the chosen
        suspense GL account. Returns per-row status + the updated summary.
    """
    mode: str  # "mark_pending" | "post_to_suspense"
    unmatched_sap: List[dict] = []
    unmatched_statement: List[dict] = []
    sap_closing_balance: Optional[float] = None
    bank_closing_balance: Optional[float] = None

    # Post-to-suspense only:
    company_code: Optional[str] = None
    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    suspense_account_id: Optional[str] = None
    suspense_account_name: Optional[str] = None
    # Indices into unmatched_statement to post. Defaults to all.
    statement_row_indices: Optional[List[int]] = None


class BankEntryPushRequest(BaseModel):
    company_code: Optional[str] = None
    entry_type: str  # "deposit" | "expense"
    payload: dict
    extracted_total: Optional[float] = None


class SapChatRequest(BaseModel):
    message: str
    company_code: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None
    thread_id: Optional[str] = None


class SaveDraftRequest(BaseModel):
    """Persist in-progress purchase / sales / bank-entries work as a draft."""
    flow: str  # 'purchase' | 'sales' | 'bank_entries'
    payload: Dict
    title: Optional[str] = None
    draft_id: Optional[str] = None   # if set, updates the existing draft
    company_code: Optional[str] = None


class SaveReconReportRequest(BaseModel):
    company_code: Optional[str] = None
    account_id: str
    account_name: Optional[str] = None
    period_start: str
    period_end: str
    statement: dict
    sap_txns: List[dict]
    matches: List[dict]
    summary: dict
    sap_closing_balance: Optional[float] = None
    needs_review: bool = False
    status: str = "draft"
    # Rows the user flagged "pending review" — persisted inside summary_json so
    # no schema migration is needed. Each entry: {side: "sap"|"statement", row}.
    pending_review: Optional[List[dict]] = None


@router.get("/bank/list-accounts", dependencies=[Depends(require_permission("sap.read"))])
async def bank_list_accounts(
    company_code: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return Bank + cash GL accounts the user can reconcile against."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    try:
        accounts = await list_bank_accounts(user["uid"], cc)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"accounts": accounts}


@router.post("/bank/extract-and-match", dependencies=[Depends(require_permission("sap.push"))])
async def bank_extract_and_match(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    company_code: Optional[str] = Form(None),
    period_start: Optional[str] = Form(None),
    period_end: Optional[str] = Form(None),
    sap_closing_balance: Optional[float] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """One-shot: parse statement, fetch SAP open items for the window, run the
    auto-matcher, return both sides + pairs."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")

    result = await process_bank_statement(
        file_content=file_bytes,
        file_name=file.filename or "statement.pdf",
        user_id=user["uid"],
        company_code=cc,
        account_id=account_id,
        period_start=period_start,
        period_end=period_end,
        sap_closing_balance=sap_closing_balance,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/bank/manual-match", dependencies=[Depends(require_permission("sap.push"))])
async def bank_manual_match_endpoint(
    payload: ManualMatchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Wrap a user-driven pair as a manual match. Frontend keeps the working set;
    /bank/save persists once done. If the caller sends the current unmatched
    lists + closing balances we also return a fresh summary so the UI numbers
    stay in sync (rather than freezing at extract time)."""
    _verify(credentials)
    match = await bank_manual_match(
        sap_txn=payload.sap_txn,
        statement_row=payload.statement_row,
    )
    response: Dict = {"match": match}
    # Older callers send only sap_txn / statement_row and expect the bare match
    # dict back. Keep that working by spreading the match fields too.
    response.update(match)

    if payload.unmatched_sap is not None and payload.unmatched_statement is not None:
        response["summary"] = bank_recompute_summary(
            unmatched_sap=payload.unmatched_sap,
            unmatched_statement=payload.unmatched_statement,
            sap_closing_balance=payload.sap_closing_balance,
            bank_closing_balance=payload.bank_closing_balance,
        )
    return response


@router.post("/bank/resolve-pending", dependencies=[Depends(require_permission("sap.push"))])
async def bank_resolve_pending_endpoint(
    payload: ResolvePendingRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Resolve leftover unmatched rows in one of two ways:

    * mode="mark_pending"      — recompute summary from the trimmed unmatched
                                  lists (frontend has already moved the rows into
                                  its pending bucket). No SAP writes.
    * mode="post_to_suspense"  — post one SAP journal entry (doc_type 'SA') per
                                  selected statement row, booking the bank GL leg
                                  against the chosen suspense GL account. Posts
                                  run sequentially; partial-failure is surfaced
                                  per row.

    SAP money-in (bank credit) -> debit bank GL, credit suspense GL.
    SAP money-out (bank debit)  -> debit suspense GL, credit bank GL.
    """
    if payload.mode not in ("mark_pending", "post_to_suspense"):
        raise HTTPException(status_code=400, detail="mode must be 'mark_pending' or 'post_to_suspense'")
    user = _verify(credentials)

    posted: List[Dict] = []
    remaining_statement = list(payload.unmatched_statement or [])

    if payload.mode == "post_to_suspense":
        cc = _resolve_company_code(payload.company_code)
        if not payload.suspense_account_id:
            raise HTTPException(status_code=400, detail="suspense_account_id required for post_to_suspense")
        if not payload.bank_account_id:
            raise HTTPException(status_code=400, detail="bank_account_id required for post_to_suspense")

        indices = (
            payload.statement_row_indices
            if payload.statement_row_indices is not None
            else list(range(len(remaining_statement)))
        )
        # Sort descending so popping by index in-place stays correct.
        indices = sorted(set(i for i in indices if 0 <= i < len(remaining_statement)), reverse=True)

        for idx in indices:
            row = remaining_statement[idx]
            credit = row.get("credit_amount") or 0
            debit = row.get("debit_amount") or 0
            is_money_in = bool(credit) and not debit
            amount = float(credit if is_money_in else debit) or 0.0
            desc = row.get("description") or "Suspense entry"
            txn_date = row.get("date")

            # Money in: Dr bank GL / Cr suspense GL. Money out: Dr suspense / Cr bank.
            if is_money_in:
                gl_items = [
                    {"gl_account": payload.bank_account_id, "amount": amount, "dc": "S", "text": desc[:50]},
                    {"gl_account": payload.suspense_account_id, "amount": amount, "dc": "H", "text": desc[:50]},
                ]
            else:
                gl_items = [
                    {"gl_account": payload.suspense_account_id, "amount": amount, "dc": "S", "text": desc[:50]},
                    {"gl_account": payload.bank_account_id, "amount": amount, "dc": "H", "text": desc[:50]},
                ]
            sap_doc = {
                "document_date": txn_date,
                "posting_date": txn_date,
                "doc_type": "SA",
                "currency": row.get("currency") or "INR",
                "header_text": desc[:25],
                "gl_items": gl_items,
                "debtor_items": [],
                "creditor_items": [],
            }

            try:
                created = await sap_connector.post_journal_entry(user["uid"], sap_doc, cc)
                posted.append({
                    "ok": True,
                    "statement_index": idx,
                    "entry_type": "deposit" if is_money_in else "expense",
                    "amount": amount,
                    "sap_id": (created or {}).get("accounting_document"),
                })
                # Drop the row from remaining so the recomputed summary excludes it.
                remaining_statement.pop(idx)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"suspense post failed for statement row {idx}")
                posted.append({
                    "ok": False,
                    "statement_index": idx,
                    "entry_type": "deposit" if is_money_in else "expense",
                    "amount": amount,
                    "error": str(exc),
                })

    summary = bank_recompute_summary(
        unmatched_sap=payload.unmatched_sap or [],
        unmatched_statement=remaining_statement,
        sap_closing_balance=payload.sap_closing_balance,
        bank_closing_balance=payload.bank_closing_balance,
    )

    return {
        "mode": payload.mode,
        "posted": posted,
        "remaining_statement": remaining_statement,
        "summary": summary,
    }


@router.post("/bank/save", dependencies=[Depends(require_permission("sap.push"))])
async def bank_save_report(
    payload: SaveReconReportRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Persist the current working state as a draft (or completed) report."""
    user = _verify(credentials)
    cc = _resolve_company_code(payload.company_code)
    # Embed pending_review inside summary_json to avoid a schema migration.
    summary_to_store = dict(payload.summary or {})
    if payload.pending_review is not None:
        summary_to_store["pending_review"] = payload.pending_review
        summary_to_store["pending_count"] = len(payload.pending_review)
    report_id = recon_save_report(
        user_id=user["uid"],
        company_code=cc,
        account_id=payload.account_id,
        account_name=payload.account_name,
        period_start=payload.period_start,
        period_end=payload.period_end,
        statement=payload.statement,
        sap_txns=payload.sap_txns,
        matches=payload.matches,
        summary=summary_to_store,
        sap_closing_balance=payload.sap_closing_balance,
        needs_review=payload.needs_review,
        status=payload.status,
    )
    if not report_id:
        raise HTTPException(status_code=500, detail="Could not persist report")
    return {"report_id": report_id}


@router.get("/bank/reports", dependencies=[Depends(require_permission("sap.read"))])
async def bank_list_reports(
    company_code: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    reports = recon_list_reports(user["uid"], company_code)
    return {"reports": reports}


@router.get("/bank/reports/{report_id}", dependencies=[Depends(require_permission("sap.read"))])
async def bank_get_report(
    report_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    report = recon_get_report(report_id, user["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.delete("/bank/reports/{report_id}", dependencies=[Depends(require_permission("sap.push"))])
async def bank_delete_report(
    report_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if not recon_delete_report(report_id, user["uid"]):
        raise HTTPException(status_code=404, detail="Report not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Bank Entries (statement -> create SAP journal entries)
# ---------------------------------------------------------------------------

@router.post("/bank-entries/extract", dependencies=[Depends(require_permission("sap.push"))])
async def bank_entries_extract(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    account_name: Optional[str] = Form(None),
    company_code: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Parse statement, classify rows, match counterparty + GL account, return
    draft SAP journal-entry payloads ready for user review before pushing."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    result = await process_bank_statement_for_entries(
        content,
        file.filename or "statement.pdf",
        user_id=user["uid"],
        company_code=cc,
        bank_account_id=account_id,
        bank_account_name=account_name,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/bank-entries/push-one", dependencies=[Depends(require_permission("sap.push"))])
async def bank_entries_push_one_endpoint(
    body: BankEntryPushRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a single entry (deposit/money-in or expense/money-out) to SAP as a
    journal entry (doc_type 'SA')."""
    user = _verify(credentials)
    cc = _resolve_company_code(body.company_code)
    return await bank_entries_push_one(
        user_id=user["uid"],
        company_code=cc,
        entry_type=body.entry_type,
        payload=body.payload,
        extracted_total=body.extracted_total,
    )


@router.get("/bank-entries/open-bills", dependencies=[Depends(require_permission("sap.read"))])
async def bank_entries_open_bills(
    company_code: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Open supplier (A/P) items available to be paid. Used by the Payment-type
    picker in bank entries. SAP open items carry no Zoho-style 'balance'; we
    surface the absolute open amount as both total and balance."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    rows = await sap_connector.list_bills(user["uid"], cc)
    out = []
    for r in rows:
        amount = abs(_amount_from_open_item(r))
        if amount <= 0:
            continue
        out.append({
            "id": str(r.get("AccountingDocument")) if r.get("AccountingDocument") is not None else None,
            "doc_number": r.get("DocumentReferenceID") or r.get("AccountingDocument"),
            "txn_date": r.get("PostingDate") or r.get("DocumentDate"),
            "due_date": r.get("NetDueDate"),
            "total": amount,
            "balance": amount,
            "vendor_id": r.get("Supplier"),
            "vendor_name": r.get("SupplierName") or r.get("BusinessPartnerName"),
        })
    return {"bills": out}


@router.get("/bank-entries/open-invoices", dependencies=[Depends(require_permission("sap.read"))])
async def bank_entries_open_invoices(
    company_code: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Open customer (A/R) items available to receive a payment against. Used by
    the Payment-type picker in bank entries when the row is a credit."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    rows = await sap_connector.list_invoices(user["uid"], cc)
    out = []
    for r in rows:
        amount = abs(_amount_from_open_item(r))
        if amount <= 0:
            continue
        out.append({
            "id": str(r.get("AccountingDocument")) if r.get("AccountingDocument") is not None else None,
            "doc_number": r.get("DocumentReferenceID") or r.get("AccountingDocument"),
            "txn_date": r.get("PostingDate") or r.get("DocumentDate"),
            "due_date": r.get("NetDueDate"),
            "total": amount,
            "balance": amount,
            "customer_id": r.get("Customer"),
            "customer_name": r.get("CustomerName") or r.get("BusinessPartnerName"),
        })
    return {"invoices": out}


_OPEN_ITEM_AMOUNT_FIELDS = (
    "AmountInCompanyCodeCurrency",
    "AmountInTransactionCurrency",
    "AbsoluteAmountInCoCodeCrcy",
    "AmountInBalanceTransacCrcy",
)


def _amount_from_open_item(r: Dict) -> float:
    """Pull the first present amount field off a SAP open-item row -> float."""
    for f in _OPEN_ITEM_AMOUNT_FIELDS:
        v = r.get(f)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


# ---------------------------------------------------------------------------
# Drafts (purchase / sales / bank_entries)
# ---------------------------------------------------------------------------

@router.post("/drafts", dependencies=[Depends(require_permission("sap.push"))])
async def sap_drafts_save(
    body: SaveDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upsert a draft. If `draft_id` is set we update; otherwise we insert."""
    user = _verify(credentials)
    new_id = sap_drafts_store.upsert(
        user_id=user["uid"],
        company_code=body.company_code or "",
        flow=body.flow,
        payload=body.payload or {},
        title=body.title,
        draft_id=body.draft_id,
    )
    if not new_id:
        raise HTTPException(status_code=500, detail="Could not persist draft")
    return {"draft_id": new_id}


@router.get("/drafts", dependencies=[Depends(require_permission("sap.read"))])
async def sap_drafts_list(
    flow: Optional[str] = Query(None),
    company_code: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="CSV filter, e.g. draft,pushed"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    rows = sap_drafts_store.list_drafts(
        user_id=user["uid"], company_code=company_code, flow=flow, status=status
    )
    return {"drafts": rows}


@router.get("/drafts/{draft_id}", dependencies=[Depends(require_permission("sap.read"))])
async def sap_drafts_get(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    row = sap_drafts_store.get_draft(user_id=user["uid"], draft_id=draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    return row


@router.delete("/drafts/{draft_id}", dependencies=[Depends(require_permission("sap.push"))])
async def sap_drafts_delete(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = sap_drafts_store.delete_draft(user_id=user["uid"], draft_id=draft_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"deleted": True}


class UpdateDraftRequest(BaseModel):
    """Save edits to an existing draft's payload."""
    payload: Dict


class ValidateDraftRequest(BaseModel):
    draft_id: str


@router.put("/drafts/{draft_id}", dependencies=[Depends(require_permission("sap.push"))])
async def sap_drafts_update(
    draft_id: str,
    body: UpdateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Update the payload of an existing draft (save user edits). This is the
    explicit user-driven save path, so we promote the row to 'draft'."""
    user = _verify(credentials)
    ok = sap_drafts_store.update_payload(
        draft_id=draft_id,
        user_id=user["uid"],
        payload=body.payload,
        set_status="draft",
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.patch("/drafts/{draft_id}/push", dependencies=[Depends(require_permission("sap.push"))])
async def sap_drafts_mark_pushed(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Mark a draft as pushed to SAP (lifecycle bookkeeping for the document
    library / bulk-push view)."""
    user = _verify(credentials)
    ok = sap_drafts_store.mark_pushed(draft_id=draft_id, user_id=user["uid"])
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.post("/validate-draft-pushable", dependencies=[Depends(require_permission("sap.read"))])
async def sap_validate_draft_pushable(
    body: ValidateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Pre-push validation for the bulk-push flow: is SAP still connected and
    does the draft's company code still resolve? Surfaces fixable issues before
    we attempt a push. Adapted for the cloud OAuth model — connectivity + company
    presence rather than ledger existence."""
    user = _verify(credentials)
    uid = user["uid"]

    draft = sap_drafts_store.get_draft(user_id=uid, draft_id=body.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    issues: List[str] = []
    connected = sap_is_user_connected(uid)
    if not connected:
        issues.append("SAP is not connected. Reconnect to push.")

    company_id = (draft.get("company_code") or "").strip()
    company_ok = True
    if connected and company_id:
        try:
            companies = await sap_connector.list_companies(uid) or []
            codes = {str(c.get("company_code")) for c in companies if c.get("company_code")}
            company_ok = company_id in codes
            if not company_ok:
                issues.append("This draft's SAP company code is no longer available.")
        except Exception:
            # Probe failed — don't hard-block; let the push attempt surface it.
            company_ok = True

    return {
        "pushable": not issues,
        "draft_id": body.draft_id,
        "flow": draft.get("flow"),
        "company_id": company_id,
        "connected": connected,
        "company_ok": company_ok,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# SAP Chat (NDJSON streaming, mirrors /ca/qb/chat shape)
# ---------------------------------------------------------------------------

@router.post("/chat", dependencies=[Depends(require_permission("chat.use"))])
async def sap_chat_endpoint(
    body: SapChatRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Stream NDJSON events for SAP chat. Persists user + assistant messages so
    history survives page reloads. Reuses the latest thread for (user, company)
    unless the frontend pins an explicit `thread_id`.

    Stream event types: status / tool_call / tool_result / answer_delta / error /
    done. The first event is always `meta` carrying the thread_id so the frontend
    can store it for future turns / reloads."""
    user = _verify(credentials)

    thread_id = body.thread_id or sap_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], company_code=body.company_code,
    )

    # Hydrate history from the DB when the frontend didn't pass one. This is what
    # makes a fresh page load keep prior turns in context.
    history = body.history
    if not history:
        rows = sap_chat_store.load_thread(
            user_id=user["uid"], company_code=body.company_code, thread_id=thread_id,
        )
        history = [{"role": r["role"], "content": r["content"]} for r in rows if r.get("content")]

    # Record the new user turn before streaming starts so it survives even if the
    # assistant blows up mid-answer.
    sap_chat_store.append_message(
        user_id=user["uid"], company_code=body.company_code, thread_id=thread_id,
        role="user", content=body.message,
    )

    async def event_generator():
        final_answer_parts: list[str] = []
        tool_trace: list[dict] = []
        try:
            yield json.dumps({"type": "meta", "thread_id": thread_id}) + "\n\n"
            async for event in run_sap_chat(
                user_id=user["uid"],
                company_code=body.company_code,
                question=body.message,
                history=history,
            ):
                # Capture the assistant's final answer + tool trace so we can
                # persist them when the stream wraps up.
                etype = event.get("type")
                if etype == "answer_delta":
                    final_answer_parts.append(event.get("content") or "")
                elif etype == "tool_call":
                    tool_trace.append({
                        "name": event.get("name"),
                        "args": event.get("arguments"),
                        "result": None,
                    })
                elif etype == "tool_result":
                    for t in reversed(tool_trace):
                        if t.get("name") == event.get("name") and t.get("result") is None:
                            t["result"] = event.get("content")
                            break
                yield json.dumps(event) + "\n\n"
        except Exception as exc:
            logger.exception("sap chat agent error")
            yield json.dumps({"type": "error", "content": str(exc)}) + "\n\n"
            yield json.dumps({"type": "done"}) + "\n\n"
        finally:
            answer = "".join(final_answer_parts).strip()
            if answer:
                sap_chat_store.append_message(
                    user_id=user["uid"], company_code=body.company_code, thread_id=thread_id,
                    role="assistant", content=answer,
                    metadata={"tools": tool_trace} if tool_trace else None,
                )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def sap_chat_history(
    company_code: Optional[str] = None,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the chat history for (user, company, thread). If thread_id is
    omitted, returns the latest thread for the user/company."""
    user = _verify(credentials)
    tid = thread_id or sap_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], company_code=company_code,
    )
    messages = sap_chat_store.load_thread(
        user_id=user["uid"], company_code=company_code, thread_id=tid,
    )
    return {"thread_id": tid, "messages": messages}


@router.delete("/chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def sap_chat_clear(
    company_code: Optional[str] = None,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Clear messages for (user, company, thread). If no thread_id is given,
    clears the latest one — frontend treats this as 'start a fresh chat'."""
    user = _verify(credentials)
    tid = thread_id or sap_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], company_code=company_code,
    )
    sap_chat_store.clear_thread(
        user_id=user["uid"], company_code=company_code, thread_id=tid,
    )
    return {"ok": True, "thread_id": tid}


# ---------------------------------------------------------------------------
# Company brief (cached SAP data summary for the chat agent)
# ---------------------------------------------------------------------------

@router.post("/brief/refresh", dependencies=[Depends(require_permission("sap.push"))])
async def sap_brief_refresh(
    company_code: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Rebuild the company brief from live SAP data and cache it."""
    user = _verify(credentials)
    cc = _resolve_company_code(company_code)
    brief = await sap_refresh_brief(user["uid"], cc)
    return {"ok": True, "company_code": cc, "brief_length": len(brief)}


@router.get("/brief", dependencies=[Depends(require_permission("sap.read"))])
async def sap_brief_get(
    company_code: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the cached company brief (if any)."""
    user = _verify(credentials)
    cc = company_code or settings.SAP_DEFAULT_COMPANY_CODE
    if not cc:
        return {"brief": None}
    brief = sap_load_brief(user["uid"], cc)
    return {"company_code": cc, "brief": brief}
