"""
Zoho Books controller — the main Zoho ERP path.

Mirrors qb_controller but cloud-only: Zoho is OAuth-only (no desktop / Electron
bridge), so every endpoint talks to the Zoho Books REST API through
`zoho_connector`. The OAuth handshake itself lives in zoho_oauth_controller; this
module assumes the user already holds stored tokens.

Company key is `organization_id` (a Zoho org id) everywhere QBO used realm_id.
The frontend passes it as a query param (GET) or in the request body (POST). When
omitted we fall back to the user's first organization.

Routes are namespaced /ca/zoho/* (registered with /ca prefix in app/__init__.py).
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

from applicationca.agents.zoho.bank_entries import (
    process_bank_statement_for_entries,
    push_bank_vouchers_batch as bank_entries_push_batch,
    push_one_entry as bank_entries_push_one,
    validate_bank_vouchers as bank_entries_validate,
)
from applicationca.agents.zoho.bank_recon import (
    manual_match as bank_manual_match,
    process_bank_statement,
    recompute_summary as bank_recompute_summary,
)
from applicationca.agents.zoho.chat import run_zoho_chat
from applicationca.agents.zoho.purchase import process_purchase_pdf
from applicationca.agents.zoho.sales import process_sales_pdf
from applicationca.services import r2_service
from applicationca.services import zoho_chat_store
from applicationca.services import zoho_drafts_store
from applicationca.services import zoho_duplicate_check
from applicationca.services import zoho_mapping_service
from applicationca.services.ledger_matcher import normalize_name
from applicationca.services.zoho_aliases import ENTITY_TYPES as ALIAS_TYPES, save_alias as alias_save
from applicationca.services.zoho_bank import list_bank_accounts
from applicationca.services.zoho_brief import load_brief as zoho_load_brief, refresh_brief as zoho_refresh_brief
from applicationca.services.zoho_connector import (
    is_user_connected as zoho_is_user_connected,
    zoho_connector,
)
from applicationca.services.zoho_recon_store import (
    delete_report as recon_delete_report,
    get_report as recon_get_report,
    list_reports as recon_list_reports,
    save_report as recon_save_report,
)
from applicationca.services.zoho_suggestion_service import (
    record_from_bill_payload,
    record_suggestion_feedback,
)
from utils.auth_utils import verify_jwt
from applicationca.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/zoho", tags=["Zoho Books"])
security = HTTPBearer()


def _verify(credentials: HTTPAuthorizationCredentials):
    user = verify_jwt(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


async def _resolve_org(user_id: str, organization_id: Optional[str]) -> str:
    """Resolve the active organization id. When the caller omits it we fall back
    to the user's first Zoho organization. Raises 400 if the user has none."""
    if organization_id:
        return organization_id
    orgs = await zoho_connector.list_organizations(user_id)
    if not orgs:
        raise HTTPException(status_code=400, detail="No Zoho organization connected")
    return str(orgs[0].get("organization_id"))


# ---------------------------------------------------------------------------
# Pydantic payloads (mirror QBO's, with organization_id)
# ---------------------------------------------------------------------------

class PartyAddress(BaseModel):
    """Zoho Books billing_address sub-object. All fields optional — we only emit
    the address when at least one is set."""
    attention: Optional[str] = None
    address: Optional[str] = None   # street line 1
    street2: Optional[str] = None   # street line 2
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None


class CreatePartyRequest(BaseModel):
    # `name` stays the required handle (becomes contact_name). Everything below
    # is optional — the modal surfaces the full Zoho contact schema so the CA can
    # capture the primary contact person, channels, GST/PAN and billing address
    # at create time instead of needing an immediate Zoho edit pass.
    name: str
    company_name: Optional[str] = None
    email: Optional[str] = None
    # Primary contact person. Zoho stores name parts on contact_persons[], not on
    # the contact itself, so these flow into a single primary contact person.
    salutation: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    # Contact channels (contact-level on Zoho)
    phone: Optional[str] = None
    mobile: Optional[str] = None
    website: Optional[str] = None
    # India tax identifiers (Zoho-native fields).
    gst_no: Optional[str] = None   # VERIFY: India edition may also require
    pan_no: Optional[str] = None   #   gst_treatment/place_of_contact alongside gst_no
    billing_address: Optional[PartyAddress] = None
    organization_id: Optional[str] = None  # which Zoho org to write to


class CreateAccountRequest(BaseModel):
    name: str
    account_type: str  # expense, income, bank, etc. (Zoho account_type)
    organization_id: Optional[str] = None


class CreateItemRequest(BaseModel):
    """Create a Zoho item with sensible defaults. Zoho items don't require an
    income account up front the way QBO does, so the shape is simpler."""
    name: str
    rate: Optional[float] = None
    description: Optional[str] = None
    organization_id: Optional[str] = None


class AliasSaveRequest(BaseModel):
    """Persist a user-confirmed (alias text) -> (zoho entity id) mapping so the
    matcher resolves the same variant text instantly on future runs."""
    entity_type: str  # customer | vendor | item | account
    alias_text: str
    entity_id: str
    entity_name: str
    organization_id: Optional[str] = None


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
    organization_id: Optional[str] = None


class PreviewCreateAccountRequest(BaseModel):
    proposed_name: str
    account_type: Optional[str] = None  # if set, only match against same-type accounts
    organization_id: Optional[str] = None


class PushZohoRequest(BaseModel):
    """Push a Zoho Invoice / Bill. `zoho_payload` is the resource body the
    pipeline produced (flat ids + inline display names).

    `extracted_total` is what the extractor computed (gross, includes tax) and is
    what `check_bill`/`check_invoice` queried against. We thread it through so
    `record_pushed` stores the same number, keeping dedupe keys aligned across
    extracts.
    """
    zoho_payload: Optional[dict] = None
    organization_id: Optional[str] = None
    extracted_total: Optional[float] = None
    skip_duplicate_check: bool = False


# ---------------------------------------------------------------------------
# Zoho card -> frontend card-shape adapters
# ---------------------------------------------------------------------------
# zoho_connector already returns normalized cards (list_id / name / ...), so the
# adapters only flatten them to the exact keys the QBO endpoints returned.

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
        "name": a.get("name"),
        "account_type": a.get("account_type"),
        "balance": str(a.get("balance", "0.00")),
    }


def _item_card(i: dict) -> dict:
    return {
        "list_id": i.get("list_id"),
        "name": i.get("name"),
        "rate": str(i.get("rate", "0.00")),
    }


# ---------------------------------------------------------------------------
# Connection / company
# ---------------------------------------------------------------------------

@router.get("/check-connection", dependencies=[Depends(require_permission("zoho.read"))])
async def check_connection(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Connection check: does the user have stored Zoho OAuth tokens?"""
    user = _verify(credentials)
    connected = zoho_is_user_connected(user["uid"])
    return {
        "connected": connected,
        "message": "Zoho Books connected" if connected else "Zoho Books not connected — start the OAuth flow",
    }


@router.get("/status", dependencies=[Depends(require_permission("zoho.read"))])
async def direct_status():
    """Dev-only direct check (cloud-only ERP, so it just reports the configured
    environment — the QBO-mode analog of qb_controller's /status; there is no
    Desktop bridge for Zoho)."""
    return {
        "mode": "cloud",
        "dc": settings.ZOHO_DC,
        "scopes": settings.ZOHO_SCOPES,
        "client_id_configured": bool(settings.ZOHO_CLIENT_ID),
    }


@router.get("/get-companies", dependencies=[Depends(require_permission("zoho.read"))])
async def get_companies(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """List the organizations the user's token grants access to."""
    user = _verify(credentials)
    try:
        orgs = await zoho_connector.list_organizations(user["uid"])
        organizations = [
            {
                "organization_id": str(o.get("organization_id")),
                "name": o.get("name") or o.get("company_name") or str(o.get("organization_id")),
                "currency": o.get("currency_code"),
                "country": o.get("country"),
            }
            for o in orgs
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

@router.get("/get-accounts", dependencies=[Depends(require_permission("zoho.read"))])
async def get_accounts(
    organization_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    rows = await zoho_connector.list_accounts(user["uid"], org)
    return {"accounts": [_account_card(a) for a in rows]}


@router.get("/get-customers", dependencies=[Depends(require_permission("zoho.read"))])
async def get_customers(
    organization_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    rows = await zoho_connector.list_customers(user["uid"], org)
    return {"customers": [_customer_card(c) for c in rows]}


@router.get("/get-vendors", dependencies=[Depends(require_permission("zoho.read"))])
async def get_vendors(
    organization_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    rows = await zoho_connector.list_vendors(user["uid"], org)
    return {"vendors": [_vendor_card(v) for v in rows]}


@router.get("/get-items", dependencies=[Depends(require_permission("zoho.read"))])
async def get_items(
    organization_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    rows = await zoho_connector.list_items(user["uid"], org)
    return {"items": [_item_card(i) for i in rows]}


# ---------------------------------------------------------------------------
# Master writes
# ---------------------------------------------------------------------------

def _clean_party_str(value: Any, max_len: Optional[int] = None) -> Optional[str]:
    """Strip and clamp; return None for blank so empty strings never reach Zoho
    (Zoho treats an empty value as 'set to empty', not 'leave unset')."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if max_len and len(s) > max_len:
        s = s[:max_len]
    return s


def _build_zoho_party_payload(source: CreatePartyRequest) -> Dict[str, Any]:
    """Translate a CreatePartyRequest into the JSON body Zoho Books expects on
    POST /contacts (same shape for customer and vendor — contact_type is added by
    the connector). Blank fields are omitted so Zoho keeps its own defaults."""
    name = _clean_party_str(source.name)
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    payload: Dict[str, Any] = {"contact_name": name}

    # Contact-level simple passthroughs — (attr, zoho_key).
    for attr, zoho_key in (
        ("company_name", "company_name"),
        ("email", "email"),
        ("phone", "phone"),
        ("mobile", "mobile"),
        ("website", "website"),
        ("gst_no", "gst_no"),
        ("pan_no", "pan_no"),
    ):
        val = _clean_party_str(getattr(source, attr, None))
        if val:
            payload[zoho_key] = val

    # GST number on the India edition needs a gst_treatment; default a registered
    # business so a lone gst_no doesn't 400. VERIFY against the live org edition.
    if payload.get("gst_no"):
        payload.setdefault("gst_treatment", "business_gst")

    # Name parts live on the primary contact person, not the contact.
    cp: Dict[str, str] = {}
    for attr, zoho_key in (
        ("salutation", "salutation"),
        ("first_name", "first_name"),
        ("last_name", "last_name"),
        ("email", "email"),
        ("phone", "phone"),
        ("mobile", "mobile"),
    ):
        val = _clean_party_str(getattr(source, attr, None))
        if val:
            cp[zoho_key] = val
    # Only emit a contact person when a name part was supplied (channels alone
    # already live on the contact, so a person with just email is noise).
    if cp.get("first_name") or cp.get("last_name") or cp.get("salutation"):
        cp["is_primary_contact"] = True
        payload["contact_persons"] = [cp]

    # Billing address — only emit when at least one sub-field is set.
    if source.billing_address:
        addr: Dict[str, str] = {}
        for attr in ("attention", "address", "street2", "city", "state", "zip", "country"):
            val = _clean_party_str(getattr(source.billing_address, attr, None))
            if val:
                addr[attr] = val
        if addr:
            payload["billing_address"] = addr

    return payload


@router.post("/create-customer", dependencies=[Depends(require_permission("zoho.push"))])
async def create_customer(
    body: CreatePartyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    zoho_body = _build_zoho_party_payload(body)
    result = await zoho_connector.create_customer(user["uid"], zoho_body, org)
    return {"customer": result}


@router.post("/create-vendor", dependencies=[Depends(require_permission("zoho.push"))])
async def create_vendor(
    body: CreatePartyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    zoho_body = _build_zoho_party_payload(body)
    result = await zoho_connector.create_vendor(user["uid"], zoho_body, org)
    return {"vendor": result}


@router.post("/create-account", dependencies=[Depends(require_permission("zoho.push"))])
async def create_account(
    body: CreateAccountRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    zoho_body = {"account_name": body.name, "account_type": body.account_type}
    result = await zoho_connector.create_account(user["uid"], zoho_body, org)
    return {"account": result}


@router.post("/create-item", dependencies=[Depends(require_permission("zoho.push"))])
async def create_item_endpoint(
    body: CreateItemRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a Zoho product/service item."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    zoho_body: Dict = {"name": body.name}
    if body.rate is not None:
        zoho_body["rate"] = body.rate
    if body.description:
        zoho_body["description"] = body.description
    result = await zoho_connector.create_item(user["uid"], zoho_body, org)
    return {"item": result}


@router.post("/alias", dependencies=[Depends(require_permission("zoho.push"))])
async def save_alias_endpoint(
    body: AliasSaveRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Persist a confirmed alias so future extractions of the same variant text
    auto-resolve to the same Zoho entity (Tier 4)."""
    user = _verify(credentials)
    if body.entity_type not in ALIAS_TYPES:
        raise HTTPException(status_code=400, detail=f"entity_type must be one of {ALIAS_TYPES}")
    org = body.organization_id or ""
    ok = alias_save(
        user_id=user["uid"],
        organization_id=org,
        entity_type=body.entity_type,
        alias_text=body.alias_text,
        entity_id=body.entity_id,
        entity_name=body.entity_name,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="failed to save alias")
    return {"saved": True}


@router.post("/create-batch", dependencies=[Depends(require_permission("zoho.push"))])
async def create_batch_endpoint(
    body: CreateBatchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Bulk-create master entities (Tier 5). Each item in `items` declares its
    own entity_type and gets routed to the correct create endpoint. Returns a
    per-item result so the frontend can patch IDs and show per-row status
    instead of failing the whole batch on one bad row."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    results: List[Dict] = []

    for item in body.items:
        try:
            if item.entity_type == "customer":
                zoho_body = {"contact_name": item.name}
                if item.company_name:
                    zoho_body["company_name"] = item.company_name
                if item.email:
                    zoho_body["email"] = item.email
                created = await zoho_connector.create_customer(user["uid"], zoho_body, org)
                results.append({
                    "ok": True,
                    "entity_type": "customer",
                    "name": item.name,
                    "entity_id": str(created.get("contact_id")) if created.get("contact_id") is not None else None,
                    "entity_name": created.get("contact_name") or item.name,
                    "zoho_entity_id": str(created.get("contact_id")) if created.get("contact_id") is not None else None,
                    "zoho_entity_name": created.get("contact_name") or item.name,
                })
            elif item.entity_type == "vendor":
                zoho_body = {"contact_name": item.name}
                if item.company_name:
                    zoho_body["company_name"] = item.company_name
                if item.email:
                    zoho_body["email"] = item.email
                created = await zoho_connector.create_vendor(user["uid"], zoho_body, org)
                results.append({
                    "ok": True,
                    "entity_type": "vendor",
                    "name": item.name,
                    "entity_id": str(created.get("contact_id")) if created.get("contact_id") is not None else None,
                    "entity_name": created.get("contact_name") or item.name,
                    "zoho_entity_id": str(created.get("contact_id")) if created.get("contact_id") is not None else None,
                    "zoho_entity_name": created.get("contact_name") or item.name,
                })
            elif item.entity_type == "account":
                if not item.account_type:
                    raise ValueError("account_type is required for account creation")
                zoho_body = {"account_name": item.name, "account_type": item.account_type}
                created = await zoho_connector.create_account(user["uid"], zoho_body, org)
                results.append({
                    "ok": True,
                    "entity_type": "account",
                    "name": item.name,
                    "entity_id": str(created.get("account_id")) if created.get("account_id") is not None else None,
                    "entity_name": created.get("account_name") or item.name,
                    "zoho_entity_id": str(created.get("account_id")) if created.get("account_id") is not None else None,
                    "zoho_entity_name": created.get("account_name") or item.name,
                })
            elif item.entity_type == "item":
                zoho_body = {"name": item.name}
                if item.rate is not None:
                    zoho_body["rate"] = item.rate
                created = await zoho_connector.create_item(user["uid"], zoho_body, org)
                results.append({
                    "ok": True,
                    "entity_type": "item",
                    "name": item.name,
                    "entity_id": str(created.get("item_id")) if created.get("item_id") is not None else None,
                    "entity_name": created.get("name") or item.name,
                    "zoho_entity_id": str(created.get("item_id")) if created.get("item_id") is not None else None,
                    "zoho_entity_name": created.get("name") or item.name,
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


@router.post("/preview-create-account", dependencies=[Depends(require_permission("zoho.read"))])
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
    org = await _resolve_org(user["uid"], body.organization_id)

    accounts = await zoho_connector.list_accounts(user["uid"], org)
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
# Document extraction (PDF -> structured rows + zoho_payload)
# ---------------------------------------------------------------------------

# ── Mapping rules / feedback / draft-preview request models ────────────────

class MappingRuleBody(BaseModel):
    organization_id: Optional[str] = None
    rule_name: Optional[str] = None
    rule_type: str = "keyword"
    document_type: Optional[str] = None      # zoho_sales | zoho_purchase | zoho_bank
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
    organization_id: Optional[str] = None
    rule: Dict[str, Any]
    transactions: List[Dict[str, Any]] = []


class SuggestionFeedbackBody(BaseModel):
    organization_id: Optional[str] = None
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


def _upload_doc_to_r2(user_id: str, flow: str, filename: str, file_bytes: bytes) -> Dict[str, Any]:
    """Best-effort upload of the source document to R2 so a reopened draft can
    preview it (Phase 6 — Zoho analog of qb_controller._upload_doc_to_r2). Returns
    {file_url, r2_key, file_name} or {} when R2 is unconfigured / fails — never
    raises, never blocks extraction."""
    try:
        import uuid as _uuid
        name = filename or "document.pdf"
        key = r2_service.build_r2_key(user_id, f"zoho_{flow}", _uuid.uuid4().hex, name)
        url = r2_service.upload_to_r2(file_bytes, key)
        return {"file_url": url, "r2_key": key, "file_name": name}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Zoho doc R2 upload skipped: %s", exc)
        return {}


@router.post("/extract-invoice", dependencies=[Depends(require_permission("zoho.push"))])
async def extract_invoice_endpoint(
    file: UploadFile = File(...),
    organization_id: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upload a sales-invoice PDF, extract fields, match to a Zoho customer +
    items, and return a payload ready for /push-invoice."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty PDF")
    result = await process_sales_pdf(pdf_bytes, user_id=user["uid"], organization_id=org)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    result.update(_upload_doc_to_r2(user["uid"], "sales", file.filename, pdf_bytes))
    return result


@router.post("/extract-bill", dependencies=[Depends(require_permission("zoho.push"))])
async def extract_bill_endpoint(
    file: UploadFile = File(...),
    organization_id: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upload a purchase-bill PDF, extract fields, match to a Zoho vendor +
    expense accounts, and return a payload ready for /push-bill."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty PDF")
    result = await process_purchase_pdf(pdf_bytes, user_id=user["uid"], organization_id=org)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    result.update(_upload_doc_to_r2(user["uid"], "purchase", file.filename, pdf_bytes))
    return result


# ---------------------------------------------------------------------------
# Mapping rules (Phase 3) — Zoho analog of qb_controller mapping-rules CRUD
# ---------------------------------------------------------------------------

@router.get("/mapping-rules", dependencies=[Depends(require_permission("zoho.read"))])
async def list_mapping_rules_endpoint(
    organization_id: Optional[str] = None,
    document_type: Optional[str] = None,
    account_slot: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    rules = zoho_mapping_service.list_rules(
        user["uid"], organization_id or "",
        document_type=document_type, account_slot=account_slot, active_only=False,
    )
    return {"rules": rules}


@router.post("/mapping-rules", dependencies=[Depends(require_permission("zoho.push"))])
async def create_mapping_rule_endpoint(
    body: MappingRuleBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    created = zoho_mapping_service.create_rule(
        user["uid"], body.organization_id or "", body.dict(exclude={"organization_id"}),
    )
    if not created:
        raise HTTPException(status_code=500, detail="failed to create rule")
    return {"rule": created}


@router.put("/mapping-rules/{rule_id}", dependencies=[Depends(require_permission("zoho.push"))])
async def update_mapping_rule_endpoint(
    rule_id: int,
    body: MappingRuleBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = zoho_mapping_service.update_rule(
        user["uid"], rule_id, body.dict(exclude={"organization_id"}, exclude_unset=True),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"updated": True}


@router.delete("/mapping-rules/{rule_id}", dependencies=[Depends(require_permission("zoho.push"))])
async def delete_mapping_rule_endpoint(
    rule_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = zoho_mapping_service.delete_rule(user["uid"], rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"deleted": True}


@router.post("/mapping-rules/preview", dependencies=[Depends(require_permission("zoho.read"))])
async def preview_mapping_rule_endpoint(
    body: MappingRulePreviewBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    matched = zoho_mapping_service.preview_rule(
        user["uid"], body.organization_id or "", body.rule, body.transactions,
    )
    return {"matched_indices": matched, "matched_count": len(matched)}


# ---------------------------------------------------------------------------
# Suggestion feedback (Phase 4)
# ---------------------------------------------------------------------------

@router.post("/suggest-account/feedback", dependencies=[Depends(require_permission("zoho.read"))])
async def suggestion_feedback_endpoint(
    body: SuggestionFeedbackBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Record that the user accepted/overrode an AI account/party suggestion.
    Feeds future rule-mining + scoring. Never fails the caller's flow."""
    user = _verify(credentials)
    record_suggestion_feedback(
        user_id=user["uid"], organization_id=body.organization_id or "", slot=body.slot,
        context_text=body.context_text, vendor_name=body.vendor_name, amount=body.amount,
        suggested_id=body.suggested_id, suggested_name=body.suggested_name,
        suggested_confidence=body.suggested_confidence, suggested_method=body.suggested_method,
        picked_id=body.picked_id, picked_name=body.picked_name,
    )
    return {"recorded": True}


# ---------------------------------------------------------------------------
# Draft source-document preview (Phase 6)
# ---------------------------------------------------------------------------

@router.post("/draft-file-url", dependencies=[Depends(require_permission("zoho.read"))])
async def draft_file_url_endpoint(
    body: DraftFileUrlBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return a short-lived presigned URL to preview a draft's source document
    (Phase 6). Ownership is enforced by requiring the key to be under the caller's
    own R2 prefix."""
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

def _validate_invoice_payload(payload: Dict) -> None:
    """Zoho invoices need a customer_id and at least one line with an item_id /
    rate. Catch the common 'no customer matched' case before hitting Zoho."""
    if not payload.get("customer_id"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_customer",
                "message": "No Zoho customer is selected for this invoice. Match or create the customer first.",
            },
        )
    if not payload.get("line_items"):
        raise HTTPException(status_code=400, detail="invoice has no line items")


def _validate_bill_payload(payload: Dict) -> None:
    """Zoho bills need a vendor_id and account-based lines."""
    if not payload.get("vendor_id"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_vendor",
                "message": "No Zoho vendor is selected for this bill. Match or create the vendor first.",
            },
        )
    if not payload.get("line_items"):
        raise HTTPException(status_code=400, detail="bill has no line items")
    missing = [i for i, l in enumerate(payload.get("line_items") or []) if not l.get("account_id")]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_account",
                "message": f"{len(missing)} bill line(s) have no expense account assigned. Pick an account first.",
                "line_indices": missing,
            },
        )


@router.post("/push-invoice", dependencies=[Depends(require_permission("zoho.push"))])
async def push_invoice(
    body: PushZohoRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a sales invoice. body.zoho_payload = Zoho Invoice JSON (flat ids +
    inline customer_name)."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    payload = body.zoho_payload
    if not payload:
        raise HTTPException(status_code=400, detail="zoho_payload required for invoice push")
    _validate_invoice_payload(payload)

    if not body.skip_duplicate_check:
        dup = await zoho_duplicate_check.check_invoice(
            user_id=user["uid"], organization_id=org,
            doc_date=payload.get("date"),
            total_amount=body.extracted_total or payload.get("total"),
            doc_number=payload.get("invoice_number"),
            customer_name=payload.get("customer_name"),
        )
        if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.7:
            raise HTTPException(status_code=409, detail={
                "message": "This invoice appears to be a duplicate and was already pushed.",
                "duplicate_check": dup,
            })

    created = await zoho_connector.create_invoice(user["uid"], payload, org)
    # Prefer the extractor's gross total (what `check_invoice` queried) over the
    # Zoho-returned total so the next extract's dedupe lookup uses the same key.
    record_amount = (
        body.extracted_total
        if body.extracted_total is not None
        else (created or {}).get("total") or payload.get("total")
    )
    zoho_duplicate_check.record_pushed(
        user_id=user["uid"], organization_id=org,
        doc_type=zoho_duplicate_check.DOC_SALES,
        date_iso=payload.get("date") or (created or {}).get("date"),
        amount=record_amount,
        doc_number=payload.get("invoice_number") or (created or {}).get("invoice_number"),
        party_name=payload.get("customer_name"),
        zoho_entity_id=str((created or {}).get("invoice_id")) if (created or {}).get("invoice_id") is not None else None,
    )
    return {"result": created}


@router.post("/push-bill", dependencies=[Depends(require_permission("zoho.push"))])
async def push_bill(
    body: PushZohoRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a purchase bill. body.zoho_payload = Zoho Bill JSON (flat ids +
    inline vendor_name / line account_name)."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    payload = body.zoho_payload
    if not payload:
        raise HTTPException(status_code=400, detail="zoho_payload required for bill push")
    _validate_bill_payload(payload)

    if not body.skip_duplicate_check:
        dup = await zoho_duplicate_check.check_bill(
            user_id=user["uid"], organization_id=org,
            doc_date=payload.get("date"),
            total_amount=body.extracted_total or sum(
                float((l.get("rate") or 0)) * float((l.get("quantity") or 1))
                for l in payload.get("line_items") or []
            ),
            doc_number=payload.get("bill_number"),
            vendor_name=payload.get("vendor_name"),
        )
        if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.7:
            raise HTTPException(status_code=409, detail={
                "message": "This bill appears to be a duplicate and was already pushed.",
                "duplicate_check": dup,
            })

    created = await zoho_connector.create_bill(user["uid"], payload, org)
    # Record vendor->account + item->account history from the un-stripped payload
    # (display names ride inline; the connector strips them before POSTing).
    record_from_bill_payload(
        user_id=user["uid"], organization_id=org, payload=payload,
    )
    # Use the extractor's gross total (what `check_bill` queried) when available;
    # fall back to the Zoho-returned total or sum of lines.
    total = body.extracted_total
    if total is None:
        total = (created or {}).get("total")
    if total is None:
        try:
            total = sum(
                float((l.get("rate") or 0)) * float((l.get("quantity") or 1))
                for l in payload.get("line_items") or []
            )
        except Exception:
            total = None
    zoho_duplicate_check.record_pushed(
        user_id=user["uid"], organization_id=org,
        doc_type=zoho_duplicate_check.DOC_PURCHASE,
        date_iso=payload.get("date") or (created or {}).get("date"),
        amount=total,
        doc_number=payload.get("bill_number") or (created or {}).get("bill_number"),
        party_name=payload.get("vendor_name"),
        zoho_entity_id=str((created or {}).get("bill_id")) if (created or {}).get("bill_id") is not None else None,
    )
    return {"result": created}


# ---------------------------------------------------------------------------
# Bank reconciliation
# ---------------------------------------------------------------------------

class ManualMatchRequest(BaseModel):
    zoho_txn: dict
    statement_row: dict
    # Optional: if the frontend sends the current unmatched lists + closing
    # balances, we recompute the summary on the same call and return it. Older
    # callers that omit these still get a wrapped match dict (no summary).
    unmatched_zoho: Optional[List[dict]] = None
    unmatched_statement: Optional[List[dict]] = None
    zoho_closing_balance: Optional[float] = None
    bank_closing_balance: Optional[float] = None


class ResolvePendingRequest(BaseModel):
    """Mark or post leftover unmatched bank-recon rows.

    mode = "mark_pending":   no Zoho writes. The frontend already moved the rows
        into its own pending list; the backend just recomputes the summary from
        the remaining unmatched lists (which exclude them).

    mode = "post_to_suspense": create a Zoho bank transaction (deposit for
        unmatched bank credits, expense for unmatched bank debits) per row,
        booked to the chosen suspense account. Returns per-row status + the
        updated summary.
    """
    mode: str  # "mark_pending" | "post_to_suspense"
    unmatched_zoho: List[dict] = []
    unmatched_statement: List[dict] = []
    zoho_closing_balance: Optional[float] = None
    bank_closing_balance: Optional[float] = None

    # Post-to-suspense only:
    organization_id: Optional[str] = None
    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    suspense_account_id: Optional[str] = None
    suspense_account_name: Optional[str] = None
    # Indices into unmatched_statement to post. Defaults to all.
    statement_row_indices: Optional[List[int]] = None


class BankEntryPushRequest(BaseModel):
    organization_id: Optional[str] = None
    entry_type: str  # "deposit" | "purchase" | "payment"
    payload: dict
    extracted_total: Optional[float] = None


class PushZohoBankVouchersRequest(BaseModel):
    """Push a batch of NORMALIZED bank vouchers to Zoho — the Zoho analog of QBO's
    /bank-entries/push-batch (and Tally's /push-vouchers-batch). The frontend
    sends the ERP-agnostic contract; the backend builds every Zoho payload via
    build_zoho_bank_payload."""
    organization_id: Optional[str] = None
    bank_account_id: str
    bank_account_name: Optional[str] = None
    vouchers: List[Dict[str, Any]]
    auto_create_masters: bool = False


class ValidateZohoBankVouchersRequest(BaseModel):
    """Pre-push validation for normalized bank vouchers — Zoho analog of QBO's
    /bank-entries/validate."""
    organization_id: Optional[str] = None
    bank_account_id: str
    bank_account_name: Optional[str] = None
    vouchers: List[Dict[str, Any]]


class ZohoChatRequest(BaseModel):
    message: str
    organization_id: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None
    thread_id: Optional[str] = None


class SaveDraftRequest(BaseModel):
    """Persist in-progress purchase / sales / bank-entries work as a draft."""
    flow: str  # 'purchase' | 'sales' | 'bank_entries'
    payload: Dict
    title: Optional[str] = None
    draft_id: Optional[str] = None   # if set, updates the existing draft
    organization_id: Optional[str] = None
    # Source-document pointers (populated by the extract endpoint via
    # _upload_doc_to_r2). Stored in dedicated columns so the draft picker can
    # show file metadata without parsing the JSONB payload; on reopen the
    # frontend uses r2_key to mint a signed URL via /draft-file-url.
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    r2_key: Optional[str] = None


class SaveReconReportRequest(BaseModel):
    organization_id: Optional[str] = None
    account_id: str
    account_name: Optional[str] = None
    period_start: str
    period_end: str
    statement: dict
    zoho_txns: List[dict]
    matches: List[dict]
    summary: dict
    zoho_closing_balance: Optional[float] = None
    needs_review: bool = False
    status: str = "draft"
    # Rows the user flagged "pending review" — persisted inside summary_json so
    # no schema migration is needed. Each entry: {side: "zoho"|"statement", row}.
    pending_review: Optional[List[dict]] = None


@router.get("/bank/list-accounts", dependencies=[Depends(require_permission("zoho.read"))])
async def bank_list_accounts(
    organization_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return Bank + Credit-card accounts the user can reconcile against."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    try:
        accounts = await list_bank_accounts(user["uid"], org)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"accounts": accounts}


@router.post("/bank/extract-and-match", dependencies=[Depends(require_permission("zoho.push"))])
async def bank_extract_and_match(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    organization_id: Optional[str] = Form(None),
    period_start: Optional[str] = Form(None),
    period_end: Optional[str] = Form(None),
    zoho_closing_balance: Optional[float] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """One-shot: parse statement, fetch Zoho txns for the chosen account in the
    same window, run the auto-matcher, return both sides + pairs."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")

    result = await process_bank_statement(
        file_content=file_bytes,
        file_name=file.filename or "statement.pdf",
        user_id=user["uid"],
        organization_id=org,
        account_id=account_id,
        period_start=period_start,
        period_end=period_end,
        zoho_closing_balance=zoho_closing_balance,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/bank/manual-match", dependencies=[Depends(require_permission("zoho.push"))])
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
        zoho_txn=payload.zoho_txn,
        statement_row=payload.statement_row,
    )
    response: Dict = {"match": match}
    # Older callers send only zoho_txn / statement_row and expect the bare match
    # dict back. Keep that working by spreading the match fields too.
    response.update(match)

    if payload.unmatched_zoho is not None and payload.unmatched_statement is not None:
        response["summary"] = bank_recompute_summary(
            unmatched_zoho=payload.unmatched_zoho,
            unmatched_statement=payload.unmatched_statement,
            zoho_closing_balance=payload.zoho_closing_balance,
            bank_closing_balance=payload.bank_closing_balance,
        )
    return response


@router.post("/bank/resolve-pending", dependencies=[Depends(require_permission("zoho.push"))])
async def bank_resolve_pending_endpoint(
    payload: ResolvePendingRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Resolve leftover unmatched rows in one of two ways:

    * mode="mark_pending"      — recompute summary from the trimmed unmatched
                                  lists (frontend has already moved the rows into
                                  its pending bucket). No Zoho writes.
    * mode="post_to_suspense"  — create one Zoho bank transaction (deposit for a
                                  bank credit row, expense for a bank debit row)
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
        org = await _resolve_org(user["uid"], payload.organization_id)
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
            entry_type = "deposit" if credit and not debit else "expense"
            amount = float(credit if entry_type == "deposit" else debit) or 0.0
            desc = row.get("description") or "Suspense entry"
            txn_date = row.get("date")

            if entry_type == "deposit":
                zoho_payload = {
                    "transaction_type": "deposit",
                    "from_account_id": payload.suspense_account_id,
                    "from_account_name": payload.suspense_account_name or "",
                    "to_account_id": payload.bank_account_id,
                    "amount": amount,
                    "description": desc,
                }
            else:
                zoho_payload = {
                    "account_id": payload.suspense_account_id,
                    "account_name": payload.suspense_account_name or "",
                    "paid_through_account_id": payload.bank_account_id,
                    "amount": amount,
                    "description": desc,
                }
            if txn_date:
                zoho_payload["date"] = txn_date

            try:
                if entry_type == "deposit":
                    created = await zoho_connector.create_bank_transaction(user["uid"], zoho_payload, org)
                    zoho_id = (created or {}).get("transaction_id") or (created or {}).get("banktransaction_id")
                else:
                    created = await zoho_connector.create_expense(user["uid"], zoho_payload, org)
                    zoho_id = (created or {}).get("expense_id")
                posted.append({
                    "ok": True,
                    "statement_index": idx,
                    "entry_type": entry_type,
                    "amount": amount,
                    "zoho_id": zoho_id,
                })
                # Drop the row from remaining so the recomputed summary excludes it.
                remaining_statement.pop(idx)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"suspense post failed for statement row {idx}")
                posted.append({
                    "ok": False,
                    "statement_index": idx,
                    "entry_type": entry_type,
                    "amount": amount,
                    "error": str(exc),
                })

    summary = bank_recompute_summary(
        unmatched_zoho=payload.unmatched_zoho or [],
        unmatched_statement=remaining_statement,
        zoho_closing_balance=payload.zoho_closing_balance,
        bank_closing_balance=payload.bank_closing_balance,
    )

    return {
        "mode": payload.mode,
        "posted": posted,
        "remaining_statement": remaining_statement,
        "summary": summary,
    }


@router.post("/bank/save", dependencies=[Depends(require_permission("zoho.push"))])
async def bank_save_report(
    payload: SaveReconReportRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Persist the current working state as a draft (or completed) report."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], payload.organization_id)
    # Embed pending_review inside summary_json to avoid a schema migration.
    summary_to_store = dict(payload.summary or {})
    if payload.pending_review is not None:
        summary_to_store["pending_review"] = payload.pending_review
        summary_to_store["pending_count"] = len(payload.pending_review)
    report_id = recon_save_report(
        user_id=user["uid"],
        organization_id=org,
        account_id=payload.account_id,
        account_name=payload.account_name,
        period_start=payload.period_start,
        period_end=payload.period_end,
        statement=payload.statement,
        zoho_txns=payload.zoho_txns,
        matches=payload.matches,
        summary=summary_to_store,
        zoho_closing_balance=payload.zoho_closing_balance,
        needs_review=payload.needs_review,
        status=payload.status,
    )
    if not report_id:
        raise HTTPException(status_code=500, detail="Could not persist report")
    return {"report_id": report_id}


@router.get("/bank/reports", dependencies=[Depends(require_permission("zoho.read"))])
async def bank_list_reports(
    organization_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    reports = recon_list_reports(user["uid"], organization_id)
    return {"reports": reports}


@router.get("/bank/reports/{report_id}", dependencies=[Depends(require_permission("zoho.read"))])
async def bank_get_report(
    report_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    report = recon_get_report(report_id, user["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.delete("/bank/reports/{report_id}", dependencies=[Depends(require_permission("zoho.push"))])
async def bank_delete_report(
    report_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if not recon_delete_report(report_id, user["uid"]):
        raise HTTPException(status_code=404, detail="Report not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Bank Entries (statement -> create Zoho txns)
# ---------------------------------------------------------------------------

@router.post("/bank-entries/extract", dependencies=[Depends(require_permission("zoho.push"))])
async def bank_entries_extract(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    account_name: Optional[str] = Form(None),
    organization_id: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Parse statement, classify rows, match counterparty + account, return draft
    Zoho payloads ready for user review before pushing."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    result = await process_bank_statement_for_entries(
        file_content=content,
        file_name=file.filename or "statement.pdf",
        user_id=user["uid"],
        organization_id=org,
        bank_account_id=account_id,
        bank_account_name=account_name,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/bank-entries/push-one", dependencies=[Depends(require_permission("zoho.push"))])
async def bank_entries_push_one_endpoint(
    body: BankEntryPushRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a single entry (deposit, purchase, or payment) to Zoho."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    return await bank_entries_push_one(
        user_id=user["uid"],
        organization_id=org,
        entry_type=body.entry_type,
        payload=body.payload,
        extracted_total=body.extracted_total,
    )


@router.post("/bank-entries/validate", dependencies=[Depends(require_permission("zoho.push"))])
async def bank_entries_validate_endpoint(
    body: ValidateZohoBankVouchersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Pre-push validation for normalized bank vouchers (Zoho analog of QBO's
    /bank-entries/validate). Returns errors, missing_masters, and duplicates."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    return await bank_entries_validate(
        user_id=user["uid"],
        organization_id=org,
        bank_account_id=body.bank_account_id,
        bank_account_name=body.bank_account_name,
        vouchers=body.vouchers,
    )


@router.post("/bank-entries/push-batch", dependencies=[Depends(require_permission("zoho.push"))])
async def bank_entries_push_batch_endpoint(
    body: PushZohoBankVouchersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Build and push a batch of normalized bank vouchers to Zoho (Zoho analog of
    QBO's /bank-entries/push-batch). The backend owns all payload construction."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], body.organization_id)
    return await bank_entries_push_batch(
        user_id=user["uid"],
        organization_id=org,
        bank_account_id=body.bank_account_id,
        bank_account_name=body.bank_account_name,
        vouchers=body.vouchers,
        auto_create_masters=body.auto_create_masters,
    )


@router.get("/bank-entries/open-bills", dependencies=[Depends(require_permission("zoho.read"))])
async def bank_entries_open_bills(
    organization_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Open vendor bills available to be paid via a vendor payment. Used by the
    Payment-type picker in bank entries."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for status in ("open", "partially_paid", "overdue"):
        for r in await zoho_connector.list_bills(user["uid"], org, params={"status": status}):
            key = r.get("bill_id")
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            rows.append(r)
    out = []
    for r in rows:
        balance = float(r.get("balance") or 0)
        if balance <= 0:
            continue
        out.append({
            "id": str(r.get("bill_id")),
            "doc_number": r.get("bill_number"),
            "txn_date": r.get("date"),
            "due_date": r.get("due_date"),
            "total": float(r.get("total") or 0),
            "balance": balance,
            "vendor_id": r.get("vendor_id"),
            "vendor_name": r.get("vendor_name"),
        })
    return {"bills": out}


@router.get("/bank-entries/open-invoices", dependencies=[Depends(require_permission("zoho.read"))])
async def bank_entries_open_invoices(
    organization_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Open customer invoices available to receive a payment against. Used by the
    Payment-type picker in bank entries when the row is a credit."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for status in ("unpaid", "partially_paid", "overdue"):
        for r in await zoho_connector.list_invoices(user["uid"], org, params={"status": status}):
            key = r.get("invoice_id")
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            rows.append(r)
    out = []
    for r in rows:
        balance = float(r.get("balance") or 0)
        if balance <= 0:
            continue
        out.append({
            "id": str(r.get("invoice_id")),
            "doc_number": r.get("invoice_number"),
            "txn_date": r.get("date"),
            "due_date": r.get("due_date"),
            "total": float(r.get("total") or 0),
            "balance": balance,
            "customer_id": r.get("customer_id"),
            "customer_name": r.get("customer_name"),
        })
    return {"invoices": out}


# ---------------------------------------------------------------------------
# Drafts (purchase / sales / bank_entries)
# ---------------------------------------------------------------------------

@router.post("/drafts", dependencies=[Depends(require_permission("zoho.push"))])
async def zoho_drafts_save(
    body: SaveDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upsert a draft. If `draft_id` is set we update; otherwise we insert."""
    user = _verify(credentials)
    new_id = zoho_drafts_store.upsert(
        user_id=user["uid"],
        organization_id=body.organization_id or "",
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


@router.get("/drafts", dependencies=[Depends(require_permission("zoho.read"))])
async def zoho_drafts_list(
    flow: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="CSV filter, e.g. draft,pushed"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    rows = zoho_drafts_store.list_drafts(
        user_id=user["uid"], organization_id=organization_id, flow=flow, status=status
    )
    return {"drafts": rows}


@router.get("/drafts/{draft_id}", dependencies=[Depends(require_permission("zoho.read"))])
async def zoho_drafts_get(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    row = zoho_drafts_store.get_draft(user_id=user["uid"], draft_id=draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    return row


@router.delete("/drafts/{draft_id}", dependencies=[Depends(require_permission("zoho.push"))])
async def zoho_drafts_delete(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = zoho_drafts_store.delete_draft(user_id=user["uid"], draft_id=draft_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"deleted": True}


class UpdateDraftRequest(BaseModel):
    """Save edits to an existing draft's payload."""
    payload: Dict


class ValidateDraftRequest(BaseModel):
    draft_id: str


@router.put("/drafts/{draft_id}", dependencies=[Depends(require_permission("zoho.push"))])
async def zoho_drafts_update(
    draft_id: str,
    body: UpdateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Update the payload of an existing draft (save user edits). This is the
    explicit user-driven save path, so we promote the row to 'draft'."""
    user = _verify(credentials)
    ok = zoho_drafts_store.update_payload(
        draft_id=draft_id,
        user_id=user["uid"],
        payload=body.payload,
        set_status="draft",
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.patch("/drafts/{draft_id}/push", dependencies=[Depends(require_permission("zoho.push"))])
async def zoho_drafts_mark_pushed(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Mark a draft as pushed to Zoho Books (lifecycle bookkeeping for the
    document library / bulk-push view)."""
    user = _verify(credentials)
    ok = zoho_drafts_store.mark_pushed(draft_id=draft_id, user_id=user["uid"])
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.post("/validate-draft-pushable", dependencies=[Depends(require_permission("zoho.read"))])
async def zoho_validate_draft_pushable(
    body: ValidateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Pre-push validation for the bulk-push flow: is Zoho still connected and
    does the draft's organization still resolve? Surfaces fixable issues before
    we attempt a push. Mirrors Tally's /validate-draft-pushable, adapted for the
    cloud OAuth model — connectivity + org presence rather than ledger existence."""
    user = _verify(credentials)
    uid = user["uid"]

    draft = zoho_drafts_store.get_draft(user_id=uid, draft_id=body.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    issues: List[str] = []
    connected = zoho_is_user_connected(uid)
    if not connected:
        issues.append("Zoho is not connected. Reconnect to push.")

    company_id = (draft.get("organization_id") or "").strip()
    company_ok = True
    if connected and company_id:
        try:
            orgs = await zoho_connector.list_organizations(uid) or []
            org_ids = {str(o.get("organization_id")) for o in orgs if o.get("organization_id")}
            company_ok = company_id in org_ids
            if not company_ok:
                issues.append("This draft's Zoho organization is no longer connected.")
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
# Zoho Chat (NDJSON streaming, mirrors /ca/qb/chat shape)
# ---------------------------------------------------------------------------

@router.post("/chat", dependencies=[Depends(require_permission("chat.use"))])
async def zoho_chat_endpoint(
    body: ZohoChatRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Stream NDJSON events for Zoho chat. Persists user + assistant messages so
    history survives page reloads. Reuses the latest thread for (user, org)
    unless the frontend pins an explicit `thread_id`.

    Stream event types: status / tool_call / tool_result / answer_delta / error /
    done. The first event is always `meta` carrying the thread_id so the frontend
    can store it for future turns / reloads."""
    user = _verify(credentials)

    thread_id = body.thread_id or zoho_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], organization_id=body.organization_id,
    )

    # Hydrate history from the DB when the frontend didn't pass one. This is what
    # makes a fresh page load keep prior turns in context.
    history = body.history
    if not history:
        rows = zoho_chat_store.load_thread(
            user_id=user["uid"], organization_id=body.organization_id, thread_id=thread_id,
        )
        history = [{"role": r["role"], "content": r["content"]} for r in rows if r.get("content")]

    # Record the new user turn before streaming starts so it survives even if the
    # assistant blows up mid-answer.
    zoho_chat_store.append_message(
        user_id=user["uid"], organization_id=body.organization_id, thread_id=thread_id,
        role="user", content=body.message,
    )

    async def event_generator():
        final_answer_parts: list[str] = []
        tool_trace: list[dict] = []
        try:
            yield json.dumps({"type": "meta", "thread_id": thread_id}) + "\n\n"
            async for event in run_zoho_chat(
                user_id=user["uid"],
                organization_id=body.organization_id,
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
            logger.exception("zoho chat agent error")
            yield json.dumps({"type": "error", "content": str(exc)}) + "\n\n"
            yield json.dumps({"type": "done"}) + "\n\n"
        finally:
            answer = "".join(final_answer_parts).strip()
            if answer:
                zoho_chat_store.append_message(
                    user_id=user["uid"], organization_id=body.organization_id, thread_id=thread_id,
                    role="assistant", content=answer,
                    metadata={"tools": tool_trace} if tool_trace else None,
                )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def zoho_chat_history(
    organization_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the chat history for (user, org, thread). If thread_id is omitted,
    returns the latest thread for the user/org."""
    user = _verify(credentials)
    tid = thread_id or zoho_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], organization_id=organization_id,
    )
    messages = zoho_chat_store.load_thread(
        user_id=user["uid"], organization_id=organization_id, thread_id=tid,
    )
    return {"thread_id": tid, "messages": messages}


@router.delete("/chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def zoho_chat_clear(
    organization_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Clear messages for (user, org, thread). If no thread_id is given, clears
    the latest one — frontend treats this as 'start a fresh chat'."""
    user = _verify(credentials)
    tid = thread_id or zoho_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], organization_id=organization_id,
    )
    zoho_chat_store.clear_thread(
        user_id=user["uid"], organization_id=organization_id, thread_id=tid,
    )
    return {"ok": True, "thread_id": tid}


# ---------------------------------------------------------------------------
# Company brief (cached Zoho data summary for the chat agent)
# ---------------------------------------------------------------------------

@router.post("/brief/refresh", dependencies=[Depends(require_permission("zoho.push"))])
async def zoho_brief_refresh(
    organization_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Rebuild the company brief from live Zoho data and cache it."""
    user = _verify(credentials)
    org = await _resolve_org(user["uid"], organization_id)
    brief = await zoho_refresh_brief(user["uid"], org)
    return {"ok": True, "organization_id": org, "brief_length": len(brief)}


@router.get("/brief", dependencies=[Depends(require_permission("zoho.read"))])
async def zoho_brief_get(
    organization_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the cached company brief (if any)."""
    user = _verify(credentials)
    org = organization_id
    if not org:
        orgs = await zoho_connector.list_organizations(user["uid"])
        if not orgs:
            return {"brief": None}
        org = str(orgs[0].get("organization_id"))
    brief = zoho_load_brief(user["uid"], org)
    return {"organization_id": org, "brief": brief}
