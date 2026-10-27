"""
Xero controller — the main Xero ERP path.

Mirrors zoho_controller but cloud-only: Xero is OAuth-only (no desktop / Electron
bridge), so every endpoint talks to the Xero Accounting API through
`xero_connector`. The OAuth handshake itself lives in xero_oauth_controller; this
module assumes the user already holds stored tokens.

Company key is `tenant_id` (a Xero organisation / tenant id) everywhere QBO used
realm_id and Zoho used organization_id. The frontend passes it as a query param
(GET) or in the request body (POST). When omitted we fall back to the user's
first tenant (from GET /connections).

Xero specifics threaded through here:
  * Tenants come from `list_connections(user_id)` — there is no list_organizations.
    Each entry is {tenantId, tenantName, tenantType}.
  * Invoices / bills are the SAME resource (an Invoice with Type ACCREC / ACCPAY);
    the created object id is `InvoiceID` for both. Bank transactions return
    `BankTransactionID`.
  * Line items reference accounts / items by CODE (AccountCode / ItemCode), not
    by GUID — only the Contact is referenced by ContactID. Cards carry a `code`
    field so the frontend can show + resolve it.
  * There is no list_bills endpoint; open bills are Invoices with Type==ACCPAY.

Routes are namespaced /ca/xero/* (registered with /ca prefix in app/__init__.py).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from config.settings import settings
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from rapidfuzz import fuzz

from applicationca.agents.xero.bank_entries import (
    process_bank_statement_for_entries,
    push_one_entry as bank_entries_push_one,
    push_bank_vouchers_batch as bank_entries_push_batch,
    validate_bank_vouchers as bank_entries_validate,
)
from applicationca.agents.xero.bank_recon import (
    manual_match as bank_manual_match,
    process_bank_statement,
    recompute_summary as bank_recompute_summary,
)
from applicationca.agents.xero.chat import run_xero_chat
from applicationca.agents.xero.purchase import process_purchase_pdf
from applicationca.agents.xero.sales import process_sales_pdf
from applicationca.services import xero_chat_store
from applicationca.services import xero_drafts_store
from applicationca.services import xero_duplicate_check
from applicationca.services import xero_mapping_service
from applicationca.services import r2_service
from applicationca.services.ledger_matcher import normalize_name
from applicationca.services.xero_aliases import ENTITY_TYPES as ALIAS_TYPES, save_alias as alias_save
from applicationca.services.xero_bank import list_bank_accounts
from applicationca.services.xero_brief import load_brief as xero_load_brief, refresh_brief as xero_refresh_brief
from applicationca.services.xero_connector import (
    is_user_connected as xero_is_user_connected,
    list_connections,
    xero_connector,
)
from applicationca.services.xero_recon_store import (
    delete_report as recon_delete_report,
    get_report as recon_get_report,
    list_reports as recon_list_reports,
    save_report as recon_save_report,
)
from applicationca.services.xero_suggestion_service import record_from_bill_payload, record_suggestion_feedback
from utils.auth_utils import verify_jwt
from applicationca.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/xero", tags=["Xero"])
security = HTTPBearer()


def _verify(credentials: HTTPAuthorizationCredentials):
    user = verify_jwt(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


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
# Pydantic payloads (mirror QBO/Zoho's, with tenant_id)
# ---------------------------------------------------------------------------

class CreatePartyRequest(BaseModel):
    name: str
    company_name: Optional[str] = None
    email: Optional[str] = None
    tenant_id: Optional[str] = None  # which Xero tenant to write to


class CreateAccountRequest(BaseModel):
    name: str
    account_type: str  # Xero account Type (EXPENSE, REVENUE, BANK, etc.)
    code: Optional[str] = None  # Xero accounts need a Code; line items reference it
    tenant_id: Optional[str] = None


class CreateItemRequest(BaseModel):
    """Create a Xero item with sensible defaults. Xero items need a Code (line
    items reference items by ItemCode), so the frontend may supply one."""
    name: str
    code: Optional[str] = None
    rate: Optional[float] = None
    description: Optional[str] = None
    tenant_id: Optional[str] = None


class AliasSaveRequest(BaseModel):
    """Persist a user-confirmed (alias text) -> (xero entity id) mapping so the
    matcher resolves the same variant text instantly on future runs. `entity_code`
    is the Xero Code (account/item) that line items reference; optional (contacts
    have no code)."""
    entity_type: str  # customer | vendor | item | account
    alias_text: str
    entity_id: str
    entity_name: str
    entity_code: Optional[str] = None
    tenant_id: Optional[str] = None


class CreateBatchItem(BaseModel):
    entity_type: str  # customer | vendor | item | account
    name: str
    company_name: Optional[str] = None
    email: Optional[str] = None
    account_type: Optional[str] = None  # for account
    code: Optional[str] = None          # for account / item
    rate: Optional[float] = None        # for item


class CreateBatchRequest(BaseModel):
    """Fan-out create across multiple master-data types in one call."""
    items: List[CreateBatchItem]
    tenant_id: Optional[str] = None


class PreviewCreateAccountRequest(BaseModel):
    proposed_name: str
    account_type: Optional[str] = None  # if set, only match against same-type accounts
    tenant_id: Optional[str] = None


class PushXeroRequest(BaseModel):
    """Push a Xero Invoice (ACCREC) / Bill (ACCPAY). `xero_payload` is the
    resource body the pipeline produced (PascalCase, Contact.ContactID + line
    items referencing AccountCode/ItemCode, plus display-only fields under
    underscore keys + customer_name/vendor_name).

    `extracted_total` is what the extractor computed (gross, includes tax) and is
    what `check_bill`/`check_invoice` queried against. We thread it through so
    `record_pushed` stores the same number, keeping dedupe keys aligned across
    extracts.
    """
    xero_payload: Optional[dict] = None
    tenant_id: Optional[str] = None
    extracted_total: Optional[float] = None
    skip_duplicate_check: bool = False


# ---------------------------------------------------------------------------
# Xero card -> frontend card-shape adapters
# ---------------------------------------------------------------------------
# xero_connector already returns normalized cards (list_id / name / code / ...),
# so the adapters only flatten them to the exact keys the frontend expects.
# Account / item cards carry `code` (Xero line items reference accounts/items by
# CODE, not GUID) so the frontend can display + resolve it.

def _customer_card(c: dict) -> dict:
    return {
        "list_id": c.get("list_id"),
        "name": c.get("name"),
        "company": c.get("company"),
        "email": c.get("email"),
        "balance": str(c.get("balance", "0.00")),
    }


def _vendor_card(v: dict) -> dict:
    return {
        "list_id": v.get("list_id"),
        "name": v.get("name"),
        "company": v.get("company"),
        "email": v.get("email"),
        "balance": str(v.get("balance", "0.00")),
    }


def _account_card(a: dict) -> dict:
    return {
        "list_id": a.get("list_id"),
        "code": a.get("code"),
        "name": a.get("name"),
        "account_type": a.get("account_type"),
        "balance": str(a.get("balance", "0.00")),
    }


def _item_card(i: dict) -> dict:
    return {
        "list_id": i.get("list_id"),
        "code": i.get("code"),
        "name": i.get("name"),
        "rate": str(i.get("rate", "0.00")),
    }


# ---------------------------------------------------------------------------
# Connection / company
# ---------------------------------------------------------------------------

@router.get("/check-connection", dependencies=[Depends(require_permission("xero.read"))])
async def check_connection(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Connection check: does the user have stored Xero OAuth tokens?"""
    user = _verify(credentials)
    connected = xero_is_user_connected(user["uid"])
    return {
        "connected": connected,
        "message": "Xero connected" if connected else "Xero not connected — start the OAuth flow",
    }


@router.get("/status", dependencies=[Depends(require_permission("xero.read"))])
async def direct_status():
    """Dev-only direct check (cloud-only ERP, so it just reports the configured
    environment — the QBO-mode analog of qb_controller's /status; there is no
    Desktop bridge for Xero)."""
    return {
        "mode": "cloud",
        "scopes": settings.XERO_SCOPES,
        "client_id_configured": bool(settings.XERO_CLIENT_ID),
    }


@router.get("/get-companies", dependencies=[Depends(require_permission("xero.read"))])
async def get_companies(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """List the tenants (organisations) the user's token grants access to."""
    user = _verify(credentials)
    try:
        tenants = await list_connections(user["uid"])
        out = [
            {
                "tenant_id": str(t.get("tenantId")),
                "name": t.get("tenantName") or str(t.get("tenantId")),
                "type": t.get("tenantType"),
            }
            for t in tenants
        ]
        return {"tenants": out}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"get_companies failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------

@router.get("/get-accounts", dependencies=[Depends(require_permission("xero.read"))])
async def get_accounts(
    tenant_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    rows = await xero_connector.list_accounts(user["uid"], tenant)
    return {"accounts": [_account_card(a) for a in rows]}


@router.get("/get-customers", dependencies=[Depends(require_permission("xero.read"))])
async def get_customers(
    tenant_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    rows = await xero_connector.list_customers(user["uid"], tenant)
    return {"customers": [_customer_card(c) for c in rows]}


@router.get("/get-vendors", dependencies=[Depends(require_permission("xero.read"))])
async def get_vendors(
    tenant_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    rows = await xero_connector.list_vendors(user["uid"], tenant)
    return {"vendors": [_vendor_card(v) for v in rows]}


@router.get("/get-items", dependencies=[Depends(require_permission("xero.read"))])
async def get_items(
    tenant_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    rows = await xero_connector.list_items(user["uid"], tenant)
    return {"items": [_item_card(i) for i in rows]}


# ---------------------------------------------------------------------------
# Master writes
# ---------------------------------------------------------------------------

@router.post("/create-customer", dependencies=[Depends(require_permission("xero.push"))])
async def create_customer(
    body: CreatePartyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    xero_body: Dict = {"Name": body.name}
    if body.company_name:
        # Xero contacts have no separate company field; Name carries it.
        xero_body["Name"] = body.company_name or body.name
        xero_body["FirstName"] = body.name  # VERIFY: contact name layout
    if body.email:
        xero_body["EmailAddress"] = body.email
    result = await xero_connector.create_customer(user["uid"], xero_body, tenant)
    return {"customer": result}


@router.post("/create-vendor", dependencies=[Depends(require_permission("xero.push"))])
async def create_vendor(
    body: CreatePartyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    xero_body: Dict = {"Name": body.name}
    if body.company_name:
        xero_body["Name"] = body.company_name or body.name
        xero_body["FirstName"] = body.name  # VERIFY: contact name layout
    if body.email:
        xero_body["EmailAddress"] = body.email
    result = await xero_connector.create_vendor(user["uid"], xero_body, tenant)
    return {"vendor": result}


@router.post("/create-account", dependencies=[Depends(require_permission("xero.push"))])
async def create_account(
    body: CreateAccountRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    xero_body: Dict = {"Name": body.name, "Type": body.account_type}
    if body.code:
        xero_body["Code"] = body.code
    result = await xero_connector.create_account(user["uid"], xero_body, tenant)
    return {"account": result}


@router.post("/create-item", dependencies=[Depends(require_permission("xero.push"))])
async def create_item_endpoint(
    body: CreateItemRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a Xero product/service item."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    xero_body: Dict = {"Name": body.name}
    if body.code:
        xero_body["Code"] = body.code
    if body.rate is not None:
        # VERIFY: UnitPrice lives under SalesDetails for sellable items.
        xero_body["SalesDetails"] = {"UnitPrice": body.rate}
    if body.description:
        xero_body["Description"] = body.description
    result = await xero_connector.create_item(user["uid"], xero_body, tenant)
    return {"item": result}


@router.post("/alias", dependencies=[Depends(require_permission("xero.push"))])
async def save_alias_endpoint(
    body: AliasSaveRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Persist a confirmed alias so future extractions of the same variant text
    auto-resolve to the same Xero entity (Tier 4)."""
    user = _verify(credentials)
    if body.entity_type not in ALIAS_TYPES:
        raise HTTPException(status_code=400, detail=f"entity_type must be one of {ALIAS_TYPES}")
    tenant = body.tenant_id or ""
    ok = alias_save(
        user_id=user["uid"],
        tenant_id=tenant,
        entity_type=body.entity_type,
        alias_text=body.alias_text,
        entity_id=body.entity_id,
        entity_name=body.entity_name,
        entity_code=body.entity_code,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="failed to save alias")
    return {"saved": True}


@router.post("/create-batch", dependencies=[Depends(require_permission("xero.push"))])
async def create_batch_endpoint(
    body: CreateBatchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Bulk-create master entities (Tier 5). Each item in `items` declares its
    own entity_type and gets routed to the correct create endpoint. Returns a
    per-item result so the frontend can patch IDs and show per-row status
    instead of failing the whole batch on one bad row."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    results: List[Dict] = []

    for item in body.items:
        try:
            if item.entity_type == "customer":
                xero_body: Dict = {"Name": item.company_name or item.name}
                if item.email:
                    xero_body["EmailAddress"] = item.email
                created = await xero_connector.create_customer(user["uid"], xero_body, tenant)
                results.append({
                    "ok": True,
                    "entity_type": "customer",
                    "name": item.name,
                    "entity_id": str(created.get("ContactID")) if created.get("ContactID") is not None else None,
                    "entity_name": created.get("Name") or item.name,
                })
            elif item.entity_type == "vendor":
                xero_body = {"Name": item.company_name or item.name}
                if item.email:
                    xero_body["EmailAddress"] = item.email
                created = await xero_connector.create_vendor(user["uid"], xero_body, tenant)
                results.append({
                    "ok": True,
                    "entity_type": "vendor",
                    "name": item.name,
                    "entity_id": str(created.get("ContactID")) if created.get("ContactID") is not None else None,
                    "entity_name": created.get("Name") or item.name,
                })
            elif item.entity_type == "account":
                if not item.account_type:
                    raise ValueError("account_type is required for account creation")
                xero_body = {"Name": item.name, "Type": item.account_type}
                if item.code:
                    xero_body["Code"] = item.code
                created = await xero_connector.create_account(user["uid"], xero_body, tenant)
                results.append({
                    "ok": True,
                    "entity_type": "account",
                    "name": item.name,
                    "entity_id": str(created.get("AccountID")) if created.get("AccountID") is not None else None,
                    "entity_name": created.get("Name") or item.name,
                    "code": created.get("Code"),
                })
            elif item.entity_type == "item":
                xero_body = {"Name": item.name}
                if item.code:
                    xero_body["Code"] = item.code
                if item.rate is not None:
                    xero_body["SalesDetails"] = {"UnitPrice": item.rate}
                created = await xero_connector.create_item(user["uid"], xero_body, tenant)
                results.append({
                    "ok": True,
                    "entity_type": "item",
                    "name": item.name,
                    "entity_id": str(created.get("ItemID")) if created.get("ItemID") is not None else None,
                    "entity_name": created.get("Name") or item.name,
                    "code": created.get("Code"),
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


@router.post("/preview-create-account", dependencies=[Depends(require_permission("xero.read"))])
async def preview_create_account(
    body: PreviewCreateAccountRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Look for likely-duplicate accounts before the user actually creates a new
    one. Returns the top fuzzy matches (similarity >= 0.80) so the UI can suggest
    'did you mean Stationery?' instead of letting a near-duplicate name slip in.
    Idempotent: nothing is created here."""
    proposed = (body.proposed_name or "").strip()
    if not proposed:
        return {"closest_matches": []}
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)

    accounts = await xero_connector.list_accounts(user["uid"], tenant)
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
                "code": a.get("code"),
                "name": name,
                "account_type": a.get("account_type"),
                "similarity": round(similarity, 3),
            })

    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return {"closest_matches": scored[:3]}


class MappingRuleBody(BaseModel):
    tenant_id: Optional[str] = None
    rule_name: Optional[str] = None
    rule_type: Optional[str] = "keyword"
    document_type: Optional[str] = None      # xero_sales | xero_purchase | xero_bank
    account_slot: Optional[str] = None       # party | account
    target_account_id: Optional[str] = None
    target_account_name: Optional[str] = None
    keywords: Optional[List[str]] = None
    pattern: Optional[str] = None
    vendor_name: Optional[str] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    conditions: Optional[Dict[str, Any]] = None
    priority: Optional[int] = 0
    is_active: Optional[bool] = True


class MappingRulePreviewBody(BaseModel):
    tenant_id: Optional[str] = None
    rule: Dict[str, Any]
    transactions: List[Dict[str, Any]] = []


class SuggestionFeedbackBody(BaseModel):
    tenant_id: Optional[str] = None
    slot: str = "account"                    # account | party
    context_text: Optional[str] = None
    vendor_name: Optional[str] = None
    amount: Optional[float] = None
    suggested_id: Optional[str] = None
    suggested_name: Optional[str] = None
    suggested_confidence: Optional[float] = None
    suggested_method: Optional[str] = None
    picked_id: Optional[str] = None
    picked_name: Optional[str] = None


class DraftFileUrlBody(BaseModel):
    r2_key: str


# ---------------------------------------------------------------------------
# Mapping rules (Phase 3)
# ---------------------------------------------------------------------------

@router.get("/mapping-rules", dependencies=[Depends(require_permission("xero.read"))])
async def list_mapping_rules_endpoint(
    tenant_id: Optional[str] = None,
    document_type: Optional[str] = None,
    account_slot: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    rules = xero_mapping_service.list_rules(
        user["uid"], tenant_id or "",
        document_type=document_type, account_slot=account_slot, active_only=False,
    )
    return {"rules": rules}


@router.post("/mapping-rules", dependencies=[Depends(require_permission("xero.push"))])
async def create_mapping_rule_endpoint(
    body: MappingRuleBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    created = xero_mapping_service.create_rule(user["uid"], body.tenant_id or "", body.dict(exclude={"tenant_id"}))
    if not created:
        raise HTTPException(status_code=500, detail="failed to create rule")
    return {"rule": created}


@router.put("/mapping-rules/{rule_id}", dependencies=[Depends(require_permission("xero.push"))])
async def update_mapping_rule_endpoint(
    rule_id: int,
    body: MappingRuleBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = xero_mapping_service.update_rule(user["uid"], rule_id, body.dict(exclude={"tenant_id"}, exclude_unset=True))
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"updated": True}


@router.delete("/mapping-rules/{rule_id}", dependencies=[Depends(require_permission("xero.push"))])
async def delete_mapping_rule_endpoint(
    rule_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = xero_mapping_service.delete_rule(user["uid"], rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"deleted": True}


@router.post("/mapping-rules/preview", dependencies=[Depends(require_permission("xero.read"))])
async def preview_mapping_rule_endpoint(
    body: MappingRulePreviewBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    matched = xero_mapping_service.preview_rule(user["uid"], body.tenant_id or "", body.rule, body.transactions)
    return {"matched_indices": matched, "matched_count": len(matched)}


# ---------------------------------------------------------------------------
# Suggestion feedback (Phase 4)
# ---------------------------------------------------------------------------

@router.post("/suggest-account/feedback", dependencies=[Depends(require_permission("xero.read"))])
async def suggestion_feedback_endpoint(
    body: SuggestionFeedbackBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Record that the user accepted/overrode an AI account/party suggestion.
    Feeds future rule-mining + scoring. Never fails the caller's flow."""
    user = _verify(credentials)
    record_suggestion_feedback(
        user_id=user["uid"], tenant_id=body.tenant_id or "", slot=body.slot,
        context_text=body.context_text, vendor_name=body.vendor_name, amount=body.amount,
        suggested_id=body.suggested_id, suggested_name=body.suggested_name,
        suggested_confidence=body.suggested_confidence, suggested_method=body.suggested_method,
        picked_id=body.picked_id, picked_name=body.picked_name,
    )
    return {"recorded": True}


# ---------------------------------------------------------------------------
# Document extraction (PDF -> structured rows + xero_payload)
# ---------------------------------------------------------------------------

def _upload_doc_to_r2(user_id: str, flow: str, filename: str, file_bytes: bytes) -> Dict[str, Any]:
    """Best-effort upload of the source document to R2 so a reopened draft can
    preview it (Phase 6). Returns {file_url, r2_key, file_name} or {} when R2 is
    unconfigured / fails — never raises, never blocks extraction."""
    try:
        import uuid as _uuid
        name = filename or "document.pdf"
        key = r2_service.build_r2_key(user_id, f"xero_{flow}", _uuid.uuid4().hex, name)
        url = r2_service.upload_to_r2(file_bytes, key)
        return {"file_url": url, "r2_key": key, "file_name": name}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Xero doc R2 upload skipped: %s", exc)
        return {}

@router.post("/extract-invoice", dependencies=[Depends(require_permission("xero.push"))])
async def extract_invoice_endpoint(
    file: UploadFile = File(...),
    tenant_id: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upload a sales-invoice PDF, extract fields, match to a Xero customer +
    items, and return a payload ready for /push-invoice."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty PDF")
    result = await process_sales_pdf(pdf_bytes, user_id=user["uid"], tenant_id=tenant)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    result.update(_upload_doc_to_r2(user["uid"], "sales", file.filename, pdf_bytes))
    return result


@router.post("/extract-bill", dependencies=[Depends(require_permission("xero.push"))])
async def extract_bill_endpoint(
    file: UploadFile = File(...),
    tenant_id: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upload a purchase-bill PDF, extract fields, match to a Xero vendor +
    expense accounts, and return a payload ready for /push-bill."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty PDF")
    result = await process_purchase_pdf(pdf_bytes, user_id=user["uid"], tenant_id=tenant)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    result.update(_upload_doc_to_r2(user["uid"], "purchase", file.filename, pdf_bytes))
    return result


@router.post("/draft-file-url", dependencies=[Depends(require_permission("xero.read"))])
async def draft_file_url_endpoint(
    body: DraftFileUrlBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return a short-lived presigned URL to preview a draft's source document
    (Phase 6). Ownership is enforced by requiring the key to be under the
    caller's own R2 prefix."""
    user = _verify(credentials)
    key = body.r2_key or ""
    if not key.startswith(f"{user['uid']}/"):
        raise HTTPException(status_code=403, detail="not your file")
    try:
        url = r2_service.generate_presigned_download_url(key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"could not sign url: {exc}")
    return {"url": url}


# ---------------------------------------------------------------------------
# Document push
# ---------------------------------------------------------------------------

def _contact_id(payload: Dict) -> Optional[str]:
    contact = payload.get("Contact") or {}
    return contact.get("ContactID") if isinstance(contact, dict) else None


def _validate_invoice_payload(payload: Dict) -> None:
    """Xero invoices need a Contact (ContactID) and at least one line item.
    Catch the common 'no customer matched' case before hitting Xero."""
    if not _contact_id(payload):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_customer",
                "message": "No Xero customer is selected for this invoice. Match or create the customer first.",
            },
        )
    if not payload.get("LineItems"):
        raise HTTPException(status_code=400, detail="invoice has no line items")


def _validate_bill_payload(payload: Dict) -> None:
    """Xero bills need a Contact (ContactID) and account-coded lines. Each line
    must reference an AccountCode (line items reference accounts by CODE)."""
    if not _contact_id(payload):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_vendor",
                "message": "No Xero vendor is selected for this bill. Match or create the vendor first.",
            },
        )
    if not payload.get("LineItems"):
        raise HTTPException(status_code=400, detail="bill has no line items")
    missing = [i for i, l in enumerate(payload.get("LineItems") or []) if not l.get("AccountCode")]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_account",
                "message": f"{len(missing)} bill line(s) have no expense account assigned. Pick an account first.",
                "line_indices": missing,
            },
        )


def _sum_lines(payload: Dict) -> float:
    """Sum a Xero payload's line items (UnitAmount * Quantity)."""
    return sum(
        float((l.get("UnitAmount") or 0)) * float((l.get("Quantity") or 1))
        for l in payload.get("LineItems") or []
    )


@router.post("/push-invoice", dependencies=[Depends(require_permission("xero.push"))])
async def push_invoice(
    body: PushXeroRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a sales invoice. body.xero_payload = Xero Invoice JSON (Type=ACCREC,
    Contact.ContactID, line items with ItemCode/AccountCode, inline customer_name)."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    payload = body.xero_payload
    if not payload:
        raise HTTPException(status_code=400, detail="xero_payload required for invoice push")
    _validate_invoice_payload(payload)

    if not body.skip_duplicate_check:
        dup = await xero_duplicate_check.check_invoice(
            user_id=user["uid"], tenant_id=tenant,
            doc_date=payload.get("Date"),
            total_amount=body.extracted_total or payload.get("Total"),
            doc_number=payload.get("InvoiceNumber"),
            customer_name=payload.get("customer_name"),
        )
        if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.7:
            raise HTTPException(status_code=409, detail={
                "message": "This invoice appears to be a duplicate and was already pushed.",
                "duplicate_check": dup,
            })

    created = await xero_connector.create_invoice(user["uid"], payload, tenant)
    # Prefer the extractor's gross total (what `check_invoice` queried) over the
    # Xero-returned total so the next extract's dedupe lookup uses the same key.
    record_amount = (
        body.extracted_total
        if body.extracted_total is not None
        else (created or {}).get("Total") or payload.get("Total")
    )
    xero_duplicate_check.record_pushed(
        user_id=user["uid"], tenant_id=tenant,
        doc_type=xero_duplicate_check.DOC_SALES,
        date_iso=payload.get("Date") or (created or {}).get("Date"),
        amount=record_amount,
        doc_number=payload.get("InvoiceNumber") or (created or {}).get("InvoiceNumber"),
        party_name=payload.get("customer_name"),
        xero_entity_id=str((created or {}).get("InvoiceID")) if (created or {}).get("InvoiceID") is not None else None,
    )
    return {"result": created}


@router.post("/push-bill", dependencies=[Depends(require_permission("xero.push"))])
async def push_bill(
    body: PushXeroRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a purchase bill. body.xero_payload = Xero Bill JSON (Type=ACCPAY,
    Contact.ContactID, line items with AccountCode + inline vendor_name /
    _account_name)."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    payload = body.xero_payload
    if not payload:
        raise HTTPException(status_code=400, detail="xero_payload required for bill push")
    _validate_bill_payload(payload)

    if not body.skip_duplicate_check:
        dup = await xero_duplicate_check.check_bill(
            user_id=user["uid"], tenant_id=tenant,
            doc_date=payload.get("Date"),
            total_amount=body.extracted_total or _sum_lines(payload),
            doc_number=payload.get("InvoiceNumber"),
            vendor_name=payload.get("vendor_name"),
        )
        if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.7:
            raise HTTPException(status_code=409, detail={
                "message": "This bill appears to be a duplicate and was already pushed.",
                "duplicate_check": dup,
            })

    created = await xero_connector.create_bill(user["uid"], payload, tenant)
    # Record vendor->account + item->account history from the un-stripped payload
    # (display names ride inline under underscore keys + vendor_name; the
    # connector strips them before POSTing).
    record_from_bill_payload(
        user_id=user["uid"], tenant_id=tenant, payload=payload,
    )
    # Use the extractor's gross total (what `check_bill` queried) when available;
    # fall back to the Xero-returned total or sum of lines.
    total = body.extracted_total
    if total is None:
        total = (created or {}).get("Total")
    if total is None:
        try:
            total = _sum_lines(payload)
        except Exception:
            total = None
    xero_duplicate_check.record_pushed(
        user_id=user["uid"], tenant_id=tenant,
        doc_type=xero_duplicate_check.DOC_PURCHASE,
        date_iso=payload.get("Date") or (created or {}).get("Date"),
        amount=total,
        doc_number=payload.get("InvoiceNumber") or (created or {}).get("InvoiceNumber"),
        party_name=payload.get("vendor_name"),
        xero_entity_id=str((created or {}).get("InvoiceID")) if (created or {}).get("InvoiceID") is not None else None,
    )
    return {"result": created}


# ---------------------------------------------------------------------------
# Bank reconciliation
# ---------------------------------------------------------------------------

class ManualMatchRequest(BaseModel):
    xero_txn: dict
    statement_row: dict
    # Optional: if the frontend sends the current unmatched lists + closing
    # balances, we recompute the summary on the same call and return it. Older
    # callers that omit these still get a wrapped match dict (no summary).
    unmatched_xero: Optional[List[dict]] = None
    unmatched_statement: Optional[List[dict]] = None
    xero_closing_balance: Optional[float] = None
    bank_closing_balance: Optional[float] = None


class ResolvePendingRequest(BaseModel):
    """Mark or post leftover unmatched bank-recon rows.

    mode = "mark_pending":   no Xero writes. The frontend already moved the rows
        into its own pending list; the backend just recomputes the summary from
        the remaining unmatched lists (which exclude them).

    mode = "post_to_suspense": create a Xero bank transaction (RECEIVE for
        unmatched bank credits, SPEND for unmatched bank debits) per row, booked
        to the chosen suspense account. Returns per-row status + the updated
        summary.
    """
    mode: str  # "mark_pending" | "post_to_suspense"
    unmatched_xero: List[dict] = []
    unmatched_statement: List[dict] = []
    xero_closing_balance: Optional[float] = None
    bank_closing_balance: Optional[float] = None

    # Post-to-suspense only:
    tenant_id: Optional[str] = None
    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    suspense_account_id: Optional[str] = None
    suspense_account_name: Optional[str] = None
    suspense_account_code: Optional[str] = None  # Xero lines reference by CODE
    # Indices into unmatched_statement to post. Defaults to all.
    statement_row_indices: Optional[List[int]] = None


class BankEntryPushRequest(BaseModel):
    tenant_id: Optional[str] = None
    entry_type: str  # "deposit" | "purchase" | "payment"
    payload: dict
    extracted_total: Optional[float] = None


class PushBankVouchersRequest(BaseModel):
    """Push a batch of NORMALIZED bank vouchers to Xero — the Xero analog of
    Tally's /push-vouchers-batch. The frontend sends the ERP-agnostic contract;
    the backend builds every Xero payload via build_xero_bank_payload."""
    tenant_id: Optional[str] = None
    bank_account_id: str
    bank_account_name: Optional[str] = None
    vouchers: List[Dict[str, Any]]
    auto_create_masters: bool = False


class ValidateBankVouchersRequest(BaseModel):
    """Pre-push validation for normalized bank vouchers — Xero analog of Tally's
    /validate-pre-push."""
    tenant_id: Optional[str] = None
    bank_account_id: str
    bank_account_name: Optional[str] = None
    vouchers: List[Dict[str, Any]]


class XeroChatRequest(BaseModel):
    message: str
    tenant_id: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None
    thread_id: Optional[str] = None


class SaveDraftRequest(BaseModel):
    """Persist in-progress purchase / sales / bank-entries work as a draft."""
    flow: str  # 'purchase' | 'sales' | 'bank_entries'
    payload: Dict
    title: Optional[str] = None
    draft_id: Optional[str] = None   # if set, updates the existing draft
    tenant_id: Optional[str] = None
    # Source-document reference (Phase 6) so a reopened draft can preview its PDF.
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    r2_key: Optional[str] = None


class SaveReconReportRequest(BaseModel):
    tenant_id: Optional[str] = None
    account_id: str
    account_name: Optional[str] = None
    period_start: str
    period_end: str
    statement: dict
    xero_txns: List[dict]
    matches: List[dict]
    summary: dict
    xero_closing_balance: Optional[float] = None
    needs_review: bool = False
    status: str = "draft"
    # Rows the user flagged "pending review" — persisted inside summary_json so
    # no schema migration is needed. Each entry: {side: "xero"|"statement", row}.
    pending_review: Optional[List[dict]] = None


@router.get("/bank/list-accounts", dependencies=[Depends(require_permission("xero.read"))])
async def bank_list_accounts(
    tenant_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return Bank accounts the user can reconcile against."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    try:
        accounts = await list_bank_accounts(user["uid"], tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"accounts": accounts}


@router.post("/bank/extract-and-match", dependencies=[Depends(require_permission("xero.push"))])
async def bank_extract_and_match(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    tenant_id: Optional[str] = Form(None),
    period_start: Optional[str] = Form(None),
    period_end: Optional[str] = Form(None),
    xero_closing_balance: Optional[float] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """One-shot: parse statement, fetch Xero txns for the chosen account in the
    same window, run the auto-matcher, return both sides + pairs."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")

    result = await process_bank_statement(
        file_content=file_bytes,
        file_name=file.filename or "statement.pdf",
        user_id=user["uid"],
        tenant_id=tenant,
        account_id=account_id,
        period_start=period_start,
        period_end=period_end,
        xero_closing_balance=xero_closing_balance,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/bank/manual-match", dependencies=[Depends(require_permission("xero.push"))])
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
        xero_txn=payload.xero_txn,
        statement_row=payload.statement_row,
    )
    response: Dict = {"match": match}
    # Older callers send only xero_txn / statement_row and expect the bare match
    # dict back. Keep that working by spreading the match fields too.
    response.update(match)

    if payload.unmatched_xero is not None and payload.unmatched_statement is not None:
        response["summary"] = bank_recompute_summary(
            unmatched_xero=payload.unmatched_xero,
            unmatched_statement=payload.unmatched_statement,
            xero_closing_balance=payload.xero_closing_balance,
            bank_closing_balance=payload.bank_closing_balance,
        )
    return response


@router.post("/bank/resolve-pending", dependencies=[Depends(require_permission("xero.push"))])
async def bank_resolve_pending_endpoint(
    payload: ResolvePendingRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Resolve leftover unmatched rows in one of two ways:

    * mode="mark_pending"      — recompute summary from the trimmed unmatched
                                  lists (frontend has already moved the rows into
                                  its pending bucket). No Xero writes.
    * mode="post_to_suspense"  — create one Xero bank transaction (RECEIVE for a
                                  bank credit row, SPEND for a bank debit row)
                                  per selected statement row, booked to the
                                  chosen suspense account. Posts run
                                  sequentially; partial-failure is surfaced per
                                  row.
    """
    if payload.mode not in ("mark_pending", "post_to_suspense"):
        raise HTTPException(status_code=400, detail="mode must be 'mark_pending' or 'post_to_suspense'")
    user = _verify(credentials)

    posted: List[Dict] = []
    remaining_statement = list(payload.unmatched_statement or [])

    if payload.mode == "post_to_suspense":
        tenant = await _resolve_tenant(user["uid"], payload.tenant_id)
        if not payload.suspense_account_id:
            raise HTTPException(status_code=400, detail="suspense_account_id required for post_to_suspense")
        if not payload.bank_account_id:
            raise HTTPException(status_code=400, detail="bank_account_id required for post_to_suspense")
        # Xero line items book to an account by CODE, not GUID — the suspense
        # account's Code must be supplied.
        if not payload.suspense_account_code:
            raise HTTPException(status_code=400, detail="suspense_account_code required for post_to_suspense")

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
            # RECEIVE = money in (bank credit / deposit); SPEND = money out (debit).
            txn_type = "RECEIVE" if credit and not debit else "SPEND"
            amount = float(credit if txn_type == "RECEIVE" else debit) or 0.0
            desc = row.get("description") or "Suspense entry"
            txn_date = row.get("date")

            # Xero BankTransaction: Type SPEND/RECEIVE, BankAccount.AccountID is
            # the bank leg, the LineItem AccountCode is the suspense leg.
            xero_payload = {
                "Type": txn_type,
                "BankAccount": {"AccountID": payload.bank_account_id},
                "LineItems": [{
                    "Description": desc,
                    "Quantity": 1,
                    "UnitAmount": amount,
                    "AccountCode": payload.suspense_account_code,
                    "_account_id": payload.suspense_account_id,
                    "_account_name": payload.suspense_account_name or "",
                }],
            }
            if txn_date:
                xero_payload["Date"] = txn_date

            try:
                created = await xero_connector.create_bank_transaction(user["uid"], xero_payload, tenant)
                posted.append({
                    "ok": True,
                    "statement_index": idx,
                    "entry_type": txn_type,
                    "amount": amount,
                    "xero_id": (created or {}).get("BankTransactionID"),
                })
                # Drop the row from remaining so the recomputed summary excludes it.
                remaining_statement.pop(idx)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"suspense post failed for statement row {idx}")
                posted.append({
                    "ok": False,
                    "statement_index": idx,
                    "entry_type": txn_type,
                    "amount": amount,
                    "error": str(exc),
                })

    summary = bank_recompute_summary(
        unmatched_xero=payload.unmatched_xero or [],
        unmatched_statement=remaining_statement,
        xero_closing_balance=payload.xero_closing_balance,
        bank_closing_balance=payload.bank_closing_balance,
    )

    return {
        "mode": payload.mode,
        "posted": posted,
        "remaining_statement": remaining_statement,
        "summary": summary,
    }


@router.post("/bank/save", dependencies=[Depends(require_permission("xero.push"))])
async def bank_save_report(
    payload: SaveReconReportRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Persist the current working state as a draft (or completed) report."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], payload.tenant_id)
    # Embed pending_review inside summary_json to avoid a schema migration.
    summary_to_store = dict(payload.summary or {})
    if payload.pending_review is not None:
        summary_to_store["pending_review"] = payload.pending_review
        summary_to_store["pending_count"] = len(payload.pending_review)
    report_id = recon_save_report(
        user_id=user["uid"],
        tenant_id=tenant,
        account_id=payload.account_id,
        account_name=payload.account_name,
        period_start=payload.period_start,
        period_end=payload.period_end,
        statement=payload.statement,
        xero_txns=payload.xero_txns,
        matches=payload.matches,
        summary=summary_to_store,
        xero_closing_balance=payload.xero_closing_balance,
        needs_review=payload.needs_review,
        status=payload.status,
    )
    if not report_id:
        raise HTTPException(status_code=500, detail="Could not persist report")
    return {"report_id": report_id}


@router.get("/bank/reports", dependencies=[Depends(require_permission("xero.read"))])
async def bank_list_reports(
    tenant_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    reports = recon_list_reports(user["uid"], tenant_id)
    return {"reports": reports}


@router.get("/bank/reports/{report_id}", dependencies=[Depends(require_permission("xero.read"))])
async def bank_get_report(
    report_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    report = recon_get_report(report_id, user["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.delete("/bank/reports/{report_id}", dependencies=[Depends(require_permission("xero.push"))])
async def bank_delete_report(
    report_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if not recon_delete_report(report_id, user["uid"]):
        raise HTTPException(status_code=404, detail="Report not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Bank Entries (statement -> create Xero txns)
# ---------------------------------------------------------------------------

@router.post("/bank-entries/extract", dependencies=[Depends(require_permission("xero.push"))])
async def bank_entries_extract(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    account_name: Optional[str] = Form(None),
    tenant_id: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Parse statement, classify rows, match counterparty + account, return draft
    Xero payloads ready for user review before pushing."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    result = await process_bank_statement_for_entries(
        file_content=content,
        file_name=file.filename or "statement.pdf",
        user_id=user["uid"],
        tenant_id=tenant,
        bank_account_id=account_id,
        bank_account_name=account_name,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/bank-entries/push-one", dependencies=[Depends(require_permission("xero.push"))])
async def bank_entries_push_one_endpoint(
    body: BankEntryPushRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a single entry (deposit, purchase, or payment) to Xero."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    return await bank_entries_push_one(
        user_id=user["uid"],
        tenant_id=tenant,
        entry_type=body.entry_type,
        payload=body.payload,
        extracted_total=body.extracted_total,
    )


@router.post("/bank-entries/validate", dependencies=[Depends(require_permission("xero.push"))])
async def bank_entries_validate_endpoint(
    body: ValidateBankVouchersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Pre-push validation for normalized bank vouchers (Xero analog of Tally's
    /validate-pre-push). Returns errors, missing_masters, and duplicates."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    return await bank_entries_validate(
        user_id=user["uid"],
        tenant_id=tenant,
        bank_account_id=body.bank_account_id,
        bank_account_name=body.bank_account_name,
        vouchers=body.vouchers,
    )


@router.post("/bank-entries/push-batch", dependencies=[Depends(require_permission("xero.push"))])
async def bank_entries_push_batch_endpoint(
    body: PushBankVouchersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Build and push a batch of normalized bank vouchers to Xero (Xero analog
    of Tally's /push-vouchers-batch). The backend owns all payload construction."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], body.tenant_id)
    return await bank_entries_push_batch(
        user_id=user["uid"],
        tenant_id=tenant,
        bank_account_id=body.bank_account_id,
        bank_account_name=body.bank_account_name,
        vouchers=body.vouchers,
        auto_create_masters=body.auto_create_masters,
    )


@router.get("/bank-entries/open-bills", dependencies=[Depends(require_permission("xero.read"))])
async def bank_entries_open_bills(
    tenant_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Open vendor bills available to be paid via a vendor payment. Used by the
    Payment-type picker in bank entries. Xero bills are Invoices with
    Type==ACCPAY; we filter to those with an outstanding AmountDue."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    # VERIFY: confirm Xero Invoices `where` Status filter values — AUTHORISED /
    # SUBMITTED bills are open. We re-filter client-side on AmountDue so a wrong
    # server param only affects which page we fetch, not correctness.
    rows = await xero_connector.list_invoices(
        user["uid"], tenant, params={"where": 'Type=="ACCPAY" && Status=="AUTHORISED"'}
    )
    out = []
    for r in rows:
        balance = float(r.get("AmountDue") or 0)
        if balance <= 0:
            continue
        contact = r.get("Contact") or {}
        out.append({
            "id": str(r.get("InvoiceID")),
            "doc_number": r.get("InvoiceNumber"),
            "txn_date": r.get("Date"),
            "due_date": r.get("DueDate"),
            "total": float(r.get("Total") or 0),
            "balance": balance,
            "vendor_id": contact.get("ContactID"),
            "vendor_name": contact.get("Name"),
        })
    return {"bills": out}


@router.get("/bank-entries/open-invoices", dependencies=[Depends(require_permission("xero.read"))])
async def bank_entries_open_invoices(
    tenant_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Open customer invoices available to receive a payment against. Used by the
    Payment-type picker in bank entries when the row is a credit. Xero sales
    invoices are Invoices with Type==ACCREC; we filter client-side on AmountDue."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    # VERIFY: confirm Xero Invoices `where` Status filter values — AUTHORISED is
    # the open/awaiting-payment state. We re-filter client-side on AmountDue.
    rows = await xero_connector.list_invoices(
        user["uid"], tenant, params={"where": 'Type=="ACCREC" && Status=="AUTHORISED"'}
    )
    out = []
    for r in rows:
        balance = float(r.get("AmountDue") or 0)
        if balance <= 0:
            continue
        contact = r.get("Contact") or {}
        out.append({
            "id": str(r.get("InvoiceID")),
            "doc_number": r.get("InvoiceNumber"),
            "txn_date": r.get("Date"),
            "due_date": r.get("DueDate"),
            "total": float(r.get("Total") or 0),
            "balance": balance,
            "customer_id": contact.get("ContactID"),
            "customer_name": contact.get("Name"),
        })
    return {"invoices": out}


# ---------------------------------------------------------------------------
# Drafts (purchase / sales / bank_entries)
# ---------------------------------------------------------------------------

@router.post("/drafts", dependencies=[Depends(require_permission("xero.push"))])
async def xero_drafts_save(
    body: SaveDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upsert a draft. If `draft_id` is set we update; otherwise we insert."""
    user = _verify(credentials)
    new_id = xero_drafts_store.upsert(
        user_id=user["uid"],
        tenant_id=body.tenant_id or "",
        flow=body.flow,
        payload=body.payload or {},
        title=body.title,
        draft_id=body.draft_id,
        file_url=body.file_url,
        file_name=body.file_name,
        r2_key=body.r2_key,
    )
    if not new_id:
        raise HTTPException(status_code=500, detail="Could not persist draft")
    return {"draft_id": new_id}


@router.get("/drafts", dependencies=[Depends(require_permission("xero.read"))])
async def xero_drafts_list(
    flow: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="CSV filter, e.g. draft,pushed"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    rows = xero_drafts_store.list_drafts(
        user_id=user["uid"], tenant_id=tenant_id, flow=flow, status=status
    )
    return {"drafts": rows}


@router.get("/drafts/{draft_id}", dependencies=[Depends(require_permission("xero.read"))])
async def xero_drafts_get(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    row = xero_drafts_store.get_draft(user_id=user["uid"], draft_id=draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    return row


@router.delete("/drafts/{draft_id}", dependencies=[Depends(require_permission("xero.push"))])
async def xero_drafts_delete(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = xero_drafts_store.delete_draft(user_id=user["uid"], draft_id=draft_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"deleted": True}


class UpdateDraftRequest(BaseModel):
    """Save edits to an existing draft's payload."""
    payload: Dict


class ValidateDraftRequest(BaseModel):
    draft_id: str


@router.put("/drafts/{draft_id}", dependencies=[Depends(require_permission("xero.push"))])
async def xero_drafts_update(
    draft_id: str,
    body: UpdateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Update the payload of an existing draft (save user edits). This is the
    explicit user-driven save path, so we promote the row to 'draft'."""
    user = _verify(credentials)
    ok = xero_drafts_store.update_payload(
        draft_id=draft_id,
        user_id=user["uid"],
        payload=body.payload,
        set_status="draft",
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.patch("/drafts/{draft_id}/push", dependencies=[Depends(require_permission("xero.push"))])
async def xero_drafts_mark_pushed(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Mark a draft as pushed to Xero (lifecycle bookkeeping for the document
    library / bulk-push view)."""
    user = _verify(credentials)
    ok = xero_drafts_store.mark_pushed(draft_id=draft_id, user_id=user["uid"])
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.post("/validate-draft-pushable", dependencies=[Depends(require_permission("xero.read"))])
async def xero_validate_draft_pushable(
    body: ValidateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Pre-push validation for the bulk-push flow: is Xero still connected and
    does the draft's tenant still resolve? Surfaces fixable issues before we
    attempt a push. Mirrors Tally's /validate-draft-pushable, adapted for the
    cloud OAuth model — connectivity + tenant presence rather than ledger checks."""
    user = _verify(credentials)
    uid = user["uid"]

    draft = xero_drafts_store.get_draft(user_id=uid, draft_id=body.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    issues: List[str] = []
    connected = xero_is_user_connected(uid)
    if not connected:
        issues.append("Xero is not connected. Reconnect to push.")

    company_id = (draft.get("tenant_id") or "").strip()
    company_ok = True
    if connected and company_id:
        try:
            tenants = await list_connections(uid) or []
            tenant_ids = {
                str(t.get("tenantId") or t.get("tenant_id"))
                for t in tenants
                if (t.get("tenantId") or t.get("tenant_id"))
            }
            company_ok = company_id in tenant_ids
            if not company_ok:
                issues.append("This draft's Xero organisation is no longer connected.")
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
# Xero Chat (NDJSON streaming, mirrors /ca/qb/chat shape)
# ---------------------------------------------------------------------------

@router.post("/chat", dependencies=[Depends(require_permission("chat.use"))])
async def xero_chat_endpoint(
    body: XeroChatRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Stream NDJSON events for Xero chat. Persists user + assistant messages so
    history survives page reloads. Reuses the latest thread for (user, tenant)
    unless the frontend pins an explicit `thread_id`.

    Stream event types: status / tool_call / tool_result / answer_delta / error /
    done. The first event is always `meta` carrying the thread_id so the frontend
    can store it for future turns / reloads."""
    user = _verify(credentials)

    thread_id = body.thread_id or xero_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], tenant_id=body.tenant_id,
    )

    # Hydrate history from the DB when the frontend didn't pass one. This is what
    # makes a fresh page load keep prior turns in context.
    history = body.history
    if not history:
        rows = xero_chat_store.load_thread(
            user_id=user["uid"], tenant_id=body.tenant_id, thread_id=thread_id,
        )
        history = [{"role": r["role"], "content": r["content"]} for r in rows if r.get("content")]

    # Record the new user turn before streaming starts so it survives even if the
    # assistant blows up mid-answer.
    xero_chat_store.append_message(
        user_id=user["uid"], tenant_id=body.tenant_id, thread_id=thread_id,
        role="user", content=body.message,
    )

    async def event_generator():
        final_answer_parts: list[str] = []
        tool_trace: list[dict] = []
        try:
            yield json.dumps({"type": "meta", "thread_id": thread_id}) + "\n\n"
            async for event in run_xero_chat(
                user_id=user["uid"],
                tenant_id=body.tenant_id,
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
            logger.exception("xero chat agent error")
            yield json.dumps({"type": "error", "content": str(exc)}) + "\n\n"
            yield json.dumps({"type": "done"}) + "\n\n"
        finally:
            answer = "".join(final_answer_parts).strip()
            if answer:
                xero_chat_store.append_message(
                    user_id=user["uid"], tenant_id=body.tenant_id, thread_id=thread_id,
                    role="assistant", content=answer,
                    metadata={"tools": tool_trace} if tool_trace else None,
                )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def xero_chat_history(
    tenant_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the chat history for (user, tenant, thread). If thread_id is
    omitted, returns the latest thread for the user/tenant."""
    user = _verify(credentials)
    tid = thread_id or xero_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], tenant_id=tenant_id,
    )
    messages = xero_chat_store.load_thread(
        user_id=user["uid"], tenant_id=tenant_id, thread_id=tid,
    )
    return {"thread_id": tid, "messages": messages}


@router.delete("/chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def xero_chat_clear(
    tenant_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Clear messages for (user, tenant, thread). If no thread_id is given, clears
    the latest one — frontend treats this as 'start a fresh chat'."""
    user = _verify(credentials)
    tid = thread_id or xero_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], tenant_id=tenant_id,
    )
    xero_chat_store.clear_thread(
        user_id=user["uid"], tenant_id=tenant_id, thread_id=tid,
    )
    return {"ok": True, "thread_id": tid}


# ---------------------------------------------------------------------------
# Company brief (cached Xero data summary for the chat agent)
# ---------------------------------------------------------------------------

@router.post("/brief/refresh", dependencies=[Depends(require_permission("xero.push"))])
async def xero_brief_refresh(
    tenant_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Rebuild the company brief from live Xero data and cache it."""
    user = _verify(credentials)
    tenant = await _resolve_tenant(user["uid"], tenant_id)
    brief = await xero_refresh_brief(user["uid"], tenant)
    return {"ok": True, "tenant_id": tenant, "brief_length": len(brief)}


@router.get("/brief", dependencies=[Depends(require_permission("xero.read"))])
async def xero_brief_get(
    tenant_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the cached company brief (if any)."""
    user = _verify(credentials)
    tenant = tenant_id
    if not tenant:
        tenants = await list_connections(user["uid"])
        if not tenants:
            return {"brief": None}
        tenant = str(tenants[0].get("tenantId"))
    brief = xero_load_brief(user["uid"], tenant)
    return {"tenant_id": tenant, "brief": brief}
