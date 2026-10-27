"""
QuickBooks Desktop controller — mirrors tally_controller for the QB ERP path.

Two transport modes (mirroring Tally):
  1. Direct  — backend talks QBXML over HTTP directly to mock_qb (dev only,
               or any HTTP-fronted QB bridge). Uses qb_connector.
  2. Bridge  — production. Backend sends typed messages over the existing
               Electron WebSocket; the desktop app speaks to QB Desktop via
               QB Web Connector / qbXMLRP2 on the user's machine.

Routes are namespaced /ca/qb/* (registered with /ca prefix in app/__init__.py).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from applicationca.agents.qbo.bank_entries import (
    process_bank_statement_for_entries,
    push_one_entry as bank_entries_push_one,
    push_bank_vouchers_batch as bank_entries_push_batch,
    validate_bank_vouchers as bank_entries_validate,
)
from applicationca.agents.qbo.chat import run_qb_chat
from applicationca.agents.qbo.bank_recon import manual_match as bank_manual_match
from applicationca.agents.qbo.bank_recon import process_bank_statement, recompute_summary as bank_recompute_summary
from applicationca.agents.qbo.purchase import process_purchase_pdf
from applicationca.agents.qbo.sales import process_sales_pdf
from applicationca.contoller.websocket_controller import manager
from applicationca.services.qb_connector import qb_connector
from applicationca.services.qb_recon_store import (
    delete_report as recon_delete_report,
    get_report as recon_get_report,
    list_reports as recon_list_reports,
    save_report as recon_save_report,
)
from applicationca.services.qbo_aliases import save_alias as alias_save, ENTITY_TYPES as ALIAS_TYPES
from applicationca.services.qbo_bank import list_bank_accounts
from applicationca.services.qbo_connector import (
    is_user_connected as qbo_is_user_connected,
    list_user_realms as qbo_list_realms,
    qbo_connector,
)
from applicationca.services.qbo_suggestion_service import record_from_bill_payload, record_suggestion_feedback
from applicationca.services import qbo_drafts_store
from applicationca.services import qbo_duplicate_check
from applicationca.services import qbo_mapping_service
from applicationca.services import r2_service
from applicationca.services import qb_chat_store
from applicationca.services.qbo_brief import refresh_brief as qbo_refresh_brief, load_brief as qbo_load_brief
from applicationca.services.ledger_matcher import normalize_name
from rapidfuzz import fuzz
from config.settings import settings
from utils.auth_utils import verify_jwt
from applicationca.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/qb", tags=["QuickBooks Desktop"])
security = HTTPBearer()


def _verify(credentials: HTTPAuthorizationCredentials):
    user = verify_jwt(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


# ---------------------------------------------------------------------------
# Pydantic payloads
# ---------------------------------------------------------------------------

class PartyAddress(BaseModel):
    """Subset of QBO's PhysicalAddress that the create-vendor / create-customer
    modal exposes. `state` maps to CountrySubDivisionCode (QBO's term for
    state/province) on the way out."""
    line1: Optional[str] = None
    line2: Optional[str] = None
    line3: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None  # → CountrySubDivisionCode
    postal_code: Optional[str] = None
    country: Optional[str] = None


class CreatePartyRequest(BaseModel):
    # `name` stays the required handle (it becomes DisplayName) so existing
    # minimal callers keep working. Everything below is optional — the modal
    # surfaces the full QBO Vendor schema for the cases where the CA wants to
    # capture address, tax id, name parts, etc. at create time.
    name: str
    company_name: Optional[str] = None
    email: Optional[str] = None
    # Name parts (Title/GivenName/MiddleName/FamilyName/Suffix). QBO accepts
    # either DisplayName *or* any one of these on create; we always send
    # DisplayName so these are purely informational on QBO's side.
    title: Optional[str] = None
    given_name: Optional[str] = None
    middle_name: Optional[str] = None
    family_name: Optional[str] = None
    suffix: Optional[str] = None
    # Contact channels
    phone: Optional[str] = None        # → PrimaryPhone.FreeFormNumber
    mobile: Optional[str] = None       # → Mobile.FreeFormNumber
    fax: Optional[str] = None          # → Fax.FreeFormNumber
    website: Optional[str] = None      # → WebAddr.URI
    # Business identifiers
    tax_identifier: Optional[str] = None     # GSTIN / PAN / EIN
    acct_num: Optional[str] = None           # vendor account number in the CA's books
    print_on_check_name: Optional[str] = None
    bill_addr: Optional[PartyAddress] = None
    qb_company: Optional[str] = None  # Which QB company file to write to


class CreateAccountRequest(BaseModel):
    name: str
    account_type: str  # Bank, AccountsReceivable, Income, Expense, etc.
    qb_company: Optional[str] = None


class CreateItemRequest(BaseModel):
    """Create a QBO Service item with sensible defaults.
    income_account_id is optional — if absent the controller picks the user's
    first Income-type account in the realm. Override only when the CA wants a
    specific revenue bucket."""
    name: str
    item_type: str = "Service"  # Service | NonInventory | Inventory
    income_account_id: Optional[str] = None
    expense_account_id: Optional[str] = None  # required for NonInventory / Inventory
    qb_company: Optional[str] = None


class AliasSaveRequest(BaseModel):
    """Persist a user-confirmed (alias text) -> (qbo entity id) mapping so the
    matcher resolves the same variant text instantly on future runs."""
    entity_type: str  # customer | vendor | item | account
    alias_text: str
    qbo_entity_id: str
    qbo_entity_name: str
    qb_company: Optional[str] = None


class CreateBatchItem(BaseModel):
    entity_type: str  # customer | vendor | item | account
    name: str
    # Optional fields per type — controller picks the right shape per entity_type
    company_name: Optional[str] = None
    email: Optional[str] = None
    account_type: Optional[str] = None  # for account
    detail_type: Optional[str] = None   # for account (QBO DetailType)
    income_account_id: Optional[str] = None  # for item
    # Rich party fields (vendor/customer). Mirrors CreatePartyRequest so the
    # bulk-create path supports the same shape as the single-create modal.
    title: Optional[str] = None
    given_name: Optional[str] = None
    middle_name: Optional[str] = None
    family_name: Optional[str] = None
    suffix: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    website: Optional[str] = None
    tax_identifier: Optional[str] = None
    acct_num: Optional[str] = None
    print_on_check_name: Optional[str] = None
    bill_addr: Optional[PartyAddress] = None


class CreateBatchRequest(BaseModel):
    """Fan-out create across multiple master-data types in one call."""
    items: List[CreateBatchItem]


class PreviewCreateAccountRequest(BaseModel):
    proposed_name: str
    account_type: Optional[str] = None  # if set, only match against same-type accounts
    qb_company: Optional[str] = None


class PushQBXMLRequest(BaseModel):
    """For QB_MODE=desktop expect `qbxml`. For QB_MODE=qbo expect `payload`
    (a QBO Invoice / Bill JSON resource).

    `extracted_total` is what the extractor computed (gross, includes tax) and
    is what `check_bill`/`check_invoice` queried against. We thread it through
    so `record_pushed` stores the same number — otherwise QBO-derived
    sum-of-lines (net of tax) and the extracted gross don't match on the next
    extract and duplicates slip through.
    """
    qbxml: Optional[str] = None
    payload: Optional[dict] = None
    qb_company: Optional[str] = None  # Desktop: company file; QBO: realm_id
    extracted_total: Optional[float] = None
    skip_duplicate_check: bool = False


class PushBankVouchersRequest(BaseModel):
    """Push a batch of NORMALIZED bank vouchers to QBO — the QB analog of Tally's
    /push-vouchers-batch. The frontend sends the ERP-agnostic contract; the
    backend builds every QBO payload via build_qbo_bank_payload."""
    qb_company: Optional[str] = None
    bank_account_id: str
    bank_account_name: Optional[str] = None
    vouchers: List[Dict[str, Any]]
    auto_create_masters: bool = False


class ValidateBankVouchersRequest(BaseModel):
    """Pre-push validation for normalized bank vouchers — QB analog of Tally's
    /validate-pre-push."""
    qb_company: Optional[str] = None
    bank_account_id: str
    bank_account_name: Optional[str] = None
    vouchers: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# QBO payload builders
# ---------------------------------------------------------------------------

# QBO field length caps from the Vendor/Customer schema docs. We clamp here
# rather than reject so a CA who paste-types a slightly-too-long company name
# still gets a successful create (with the trailing chars trimmed) instead of
# a hard 400. The lengths matter most for the name parts (Title/Suffix are
# only 16 chars) where overflow is genuinely surprising.
_QBO_PARTY_MAX_LENGTHS: Dict[str, int] = {
    "DisplayName": 500,
    "Title": 16,
    "GivenName": 100,
    "MiddleName": 100,
    "FamilyName": 100,
    "Suffix": 16,
    "CompanyName": 1024,
    "PrintOnCheckName": 110,
    "TaxIdentifier": 20,
    "AcctNum": 20,
    # Phones/emails/URI have generous caps; we use loose defaults below.
}

_QBO_ADDRESS_MAX_LENGTHS: Dict[str, int] = {
    "Line1": 500,
    "Line2": 500,
    "Line3": 500,
    "City": 255,
    "CountrySubDivisionCode": 255,
    "PostalCode": 30,
    "Country": 255,
}


def _clean_str(value: Any, max_len: Optional[int] = None) -> Optional[str]:
    """Strip and clamp; return None for blank/whitespace so empty strings
    don't end up as empty fields in the QBO payload (which Intuit treats as
    'set to empty', not 'leave unset')."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if max_len and len(s) > max_len:
        s = s[:max_len]
    return s


def _build_qbo_party_payload(source: Any, *, party_type: str = "vendor") -> Dict[str, Any]:
    """Translate our CreatePartyRequest / CreateBatchItem into the JSON body
    QBO expects on POST /vendor (and POST /customer — same shape).

    `source` can be a Pydantic model or any object with the documented
    attributes. Fields that come through blank are simply omitted from the
    payload so QBO uses its own defaults. `party_type` only affects the
    error label; the shape is identical.
    """
    name = _clean_str(getattr(source, "name", None), _QBO_PARTY_MAX_LENGTHS["DisplayName"])
    if not name:
        raise HTTPException(status_code=400, detail=f"{party_type} name is required")

    payload: Dict[str, Any] = {"DisplayName": name}

    # Simple string passthroughs — (attr_name_on_source, qbo_key).
    simple_fields = [
        ("title", "Title"),
        ("given_name", "GivenName"),
        ("middle_name", "MiddleName"),
        ("family_name", "FamilyName"),
        ("suffix", "Suffix"),
        ("company_name", "CompanyName"),
        ("print_on_check_name", "PrintOnCheckName"),
        ("tax_identifier", "TaxIdentifier"),
        ("acct_num", "AcctNum"),
    ]
    for attr, qbo_key in simple_fields:
        val = _clean_str(getattr(source, attr, None), _QBO_PARTY_MAX_LENGTHS.get(qbo_key))
        if val:
            payload[qbo_key] = val

    # Nested wrappers — QBO uses {Address: "..."} / {FreeFormNumber: "..."} /
    # {URI: "..."} so we always wrap a single string in the right key.
    email = _clean_str(getattr(source, "email", None), 100)
    if email:
        payload["PrimaryEmailAddr"] = {"Address": email}

    phone = _clean_str(getattr(source, "phone", None), 21)
    if phone:
        payload["PrimaryPhone"] = {"FreeFormNumber": phone}

    mobile = _clean_str(getattr(source, "mobile", None), 21)
    if mobile:
        payload["Mobile"] = {"FreeFormNumber": mobile}

    fax = _clean_str(getattr(source, "fax", None), 21)
    if fax:
        payload["Fax"] = {"FreeFormNumber": fax}

    website = _clean_str(getattr(source, "website", None), 1000)
    if website:
        payload["WebAddr"] = {"URI": website}

    # Billing address — only emit BillAddr when at least one sub-field is set.
    bill_addr = getattr(source, "bill_addr", None)
    if bill_addr:
        addr_pairs = [
            ("line1", "Line1"),
            ("line2", "Line2"),
            ("line3", "Line3"),
            ("city", "City"),
            ("state", "CountrySubDivisionCode"),
            ("postal_code", "PostalCode"),
            ("country", "Country"),
        ]
        addr: Dict[str, str] = {}
        for attr, qbo_key in addr_pairs:
            val = _clean_str(getattr(bill_addr, attr, None), _QBO_ADDRESS_MAX_LENGTHS.get(qbo_key))
            if val:
                addr[qbo_key] = val
        if addr:
            payload["BillAddr"] = addr

    return payload


# ---------------------------------------------------------------------------
# QBO -> frontend card-shape adapters
# ---------------------------------------------------------------------------

def _qbo_customer_card(c: dict) -> dict:
    email = (c.get("PrimaryEmailAddr") or {}).get("Address")
    return {
        "list_id": c.get("Id"),
        "name": c.get("DisplayName") or c.get("FullyQualifiedName"),
        "company": c.get("CompanyName"),
        "email": email,
        "balance": str(c.get("Balance", "0.00")),
    }


def _qbo_vendor_card(v: dict) -> dict:
    email = (v.get("PrimaryEmailAddr") or {}).get("Address")
    return {
        "list_id": v.get("Id"),
        "name": v.get("DisplayName"),
        "company": v.get("CompanyName"),
        "email": email,
        "balance": str(v.get("Balance", "0.00")),
    }


def _qbo_account_card(a: dict) -> dict:
    return {
        "list_id": a.get("Id"),
        "name": a.get("Name"),
        "full_name": a.get("FullyQualifiedName"),
        "account_type": a.get("AccountType"),
        "balance": str(a.get("CurrentBalance", "0.00")),
    }


def _qbo_item_card(i: dict) -> dict:
    return {
        "list_id": i.get("Id"),
        "name": i.get("Name"),
        "type": i.get("Type"),
        "rate": str(i.get("UnitPrice", "0.00")),
    }


def _is_qbo() -> bool:
    return settings.QB_MODE == "qbo"


# ---------------------------------------------------------------------------
# Connection / company
# ---------------------------------------------------------------------------

@router.get("/check-connection", dependencies=[Depends(require_permission("quickbooks.read"))])
async def check_connection(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Connection check. In QBO mode this means: does the user have stored
    OAuth tokens? In Desktop mode: is the Electron bridge reachable?"""
    user = _verify(credentials)
    if _is_qbo():
        connected = qbo_is_user_connected(user["uid"])
        return {
            "connected": connected,
            "mode": "qbo",
            "message": "QuickBooks Online connected" if connected else "QuickBooks Online not connected — start the OAuth flow",
        }
    connected = await manager.check_qb_connection(user["uid"])
    return {
        "connected": connected,
        "mode": "desktop",
        "message": "QuickBooks Desktop is connected" if connected else "QuickBooks Desktop is not connected",
    }


@router.get("/status", dependencies=[Depends(require_permission("quickbooks.read"))])
async def direct_status():
    """Dev-only direct check. In Desktop mode this hits mock_qb / QB_URL.
    In QBO mode it just reports the configured environment (no auth context
    here, so we can't resolve a specific user's connection state)."""
    if _is_qbo():
        return {
            "mode": "qbo",
            "qbo_env": settings.QBO_ENV,
            "api_base": settings.QBO_API_BASE,
            "client_id_configured": bool(settings.QBO_CLIENT_ID),
        }
    connected = await qb_connector.check_connection()
    return {"connected": connected, "mode": "desktop", "url": qb_connector.base_url}


@router.get("/get-companies", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_companies(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = _verify(credentials)
    try:
        if _is_qbo():
            realms = qbo_list_realms(user["uid"])
            companies = []
            for r in realms:
                try:
                    info = await qbo_connector.get_company_info(user["uid"], r["realm_id"])
                    companies.append({
                        "name": info.get("CompanyName") or info.get("LegalName") or r["realm_id"],
                        "realm_id": r["realm_id"],
                        "country": info.get("Country"),
                        "qbo_env": r["qbo_env"],
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"CompanyInfo failed for realm {r['realm_id']}: {exc}")
                    companies.append({"name": r["realm_id"], "realm_id": r["realm_id"], "qbo_env": r["qbo_env"]})
            return {"companies": companies, "mode": "qbo"}

        companies = await manager.get_qb_companies(user["uid"])
        return {"companies": companies, "mode": "desktop"}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"get_qb_companies failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------

@router.get("/get-accounts", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_accounts(
    company: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if _is_qbo():
        rows = await qbo_connector.list_accounts(user["uid"], company)
        return {"accounts": [_qbo_account_card(a) for a in rows]}
    return {"accounts": await manager.get_qb_accounts(user["uid"], company)}


@router.get("/get-customers", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_customers(
    company: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if _is_qbo():
        rows = await qbo_connector.list_customers(user["uid"], company)
        return {"customers": [_qbo_customer_card(c) for c in rows]}
    return {"customers": await manager.get_qb_customers(user["uid"], company)}


@router.get("/get-vendors", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_vendors(
    company: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if _is_qbo():
        rows = await qbo_connector.list_vendors(user["uid"], company)
        return {"vendors": [_qbo_vendor_card(v) for v in rows]}
    return {"vendors": await manager.get_qb_vendors(user["uid"], company)}


@router.get("/get-items", dependencies=[Depends(require_permission("quickbooks.read"))])
async def get_items(
    company: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if _is_qbo():
        rows = await qbo_connector.list_items(user["uid"], company)
        return {"items": [_qbo_item_card(i) for i in rows]}
    return {"items": await manager.get_qb_items(user["uid"], company)}


# ---------------------------------------------------------------------------
# Master writes
# ---------------------------------------------------------------------------

@router.post("/create-customer", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_customer(
    body: CreatePartyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if _is_qbo():
        qbo_body = _build_qbo_party_payload(body, party_type="customer")
        result = await qbo_connector.create_customer(user["uid"], qbo_body, body.qb_company)
        return {"customer": result.get("Customer", result)}
    return {"customer": await manager.create_qb_customer(user["uid"], body.dict())}


@router.post("/create-vendor", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_vendor(
    body: CreatePartyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if _is_qbo():
        qbo_body = _build_qbo_party_payload(body, party_type="vendor")
        result = await qbo_connector.create_vendor(user["uid"], qbo_body, body.qb_company)
        return {"vendor": result.get("Vendor", result)}
    # Desktop bridge still uses the legacy minimal payload; the rich-fields
    # form is QBO-only for now.
    return {"vendor": await manager.create_qb_vendor(user["uid"], body.dict())}


@router.post("/create-account", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_account(
    body: CreateAccountRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    payload = body.dict()
    if _is_qbo():
        qbo_body = {"Name": body.name, "AccountType": body.account_type}
        result = await qbo_connector.create_account(user["uid"], qbo_body, body.qb_company)
        return {"account": result.get("Account", result)}
    return {"account": await manager.create_qb_account(user["uid"], payload)}


async def _resolve_income_account_id(user_id: str, realm_id: Optional[str], override: Optional[str]) -> Optional[str]:
    """Find an income account in the realm: override wins, else first Income."""
    if override:
        return override
    accts = await qbo_connector.list_accounts(user_id, realm_id)
    for a in accts:
        if a.get("AccountType") in ("Income", "OtherIncome") and a.get("Id"):
            return str(a["Id"])
    return None


@router.post("/create-item", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_item_endpoint(
    body: CreateItemRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a QBO product/service item. QBO requires every item to reference
    an income account (and an expense account for NonInventory/Inventory).
    We auto-pick the first available Income-type account if the caller did
    not specify one, which is the common 'Service' case."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="create-item currently supports QBO mode only")
    user = _verify(credentials)
    income_id = await _resolve_income_account_id(user["uid"], body.qb_company, body.income_account_id)
    if not income_id:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "no_income_account",
                "message": "No Income account exists in this QuickBooks company. Create one (e.g. 'Sales' or 'Services') before adding items.",
            },
        )
    qbo_body: Dict = {
        "Name": body.name,
        "Type": body.item_type,
        "IncomeAccountRef": {"value": income_id},
    }
    if body.item_type in ("NonInventory", "Inventory") and body.expense_account_id:
        qbo_body["ExpenseAccountRef"] = {"value": body.expense_account_id}
    result = await qbo_connector.create_item(user["uid"], qbo_body, body.qb_company)
    return {"item": result.get("Item", result)}


@router.post("/alias", dependencies=[Depends(require_permission("quickbooks.push"))])
async def save_alias_endpoint(
    body: AliasSaveRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Persist a confirmed alias so future extractions of the same variant
    text auto-resolve to the same QBO entity (Tier 4)."""
    user = _verify(credentials)
    if body.entity_type not in ALIAS_TYPES:
        raise HTTPException(status_code=400, detail=f"entity_type must be one of {ALIAS_TYPES}")
    realm = body.qb_company or ""
    ok = alias_save(
        user_id=user["uid"],
        realm_id=realm,
        entity_type=body.entity_type,
        alias_text=body.alias_text,
        qbo_entity_id=body.qbo_entity_id,
        qbo_entity_name=body.qbo_entity_name,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="failed to save alias")
    return {"saved": True}


# ── Mapping rules (Phase 3) ────────────────────────────────────────────────

class MappingRuleBody(BaseModel):
    qb_company: Optional[str] = None
    rule_name: Optional[str] = None
    rule_type: str = "keyword"
    document_type: Optional[str] = None      # qb_sales | qb_purchase | qb_bank
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
    qb_company: Optional[str] = None
    rule: Dict[str, Any]
    transactions: List[Dict[str, Any]] = []


@router.get("/mapping-rules", dependencies=[Depends(require_permission("quickbooks.read"))])
async def list_mapping_rules_endpoint(
    qb_company: Optional[str] = None,
    document_type: Optional[str] = None,
    account_slot: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    rules = qbo_mapping_service.list_rules(
        user["uid"], qb_company or "",
        document_type=document_type, account_slot=account_slot, active_only=False,
    )
    return {"rules": rules}


@router.post("/mapping-rules", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_mapping_rule_endpoint(
    body: MappingRuleBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    created = qbo_mapping_service.create_rule(user["uid"], body.qb_company or "", body.dict(exclude={"qb_company"}))
    if not created:
        raise HTTPException(status_code=500, detail="failed to create rule")
    return {"rule": created}


@router.put("/mapping-rules/{rule_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def update_mapping_rule_endpoint(
    rule_id: int,
    body: MappingRuleBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = qbo_mapping_service.update_rule(user["uid"], rule_id, body.dict(exclude={"qb_company"}, exclude_unset=True))
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"updated": True}


@router.delete("/mapping-rules/{rule_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def delete_mapping_rule_endpoint(
    rule_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = qbo_mapping_service.delete_rule(user["uid"], rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"deleted": True}


@router.post("/mapping-rules/preview", dependencies=[Depends(require_permission("quickbooks.read"))])
async def preview_mapping_rule_endpoint(
    body: MappingRulePreviewBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    matched = qbo_mapping_service.preview_rule(user["uid"], body.qb_company or "", body.rule, body.transactions)
    return {"matched_indices": matched, "matched_count": len(matched)}


# ── Suggestion feedback (Phase 4) ──────────────────────────────────────────

class SuggestionFeedbackBody(BaseModel):
    qb_company: Optional[str] = None
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


@router.post("/suggest-account/feedback", dependencies=[Depends(require_permission("quickbooks.read"))])
async def suggestion_feedback_endpoint(
    body: SuggestionFeedbackBody,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Record that the user accepted/overrode an AI account/party suggestion.
    Feeds future rule-mining + scoring. Never fails the caller's flow."""
    user = _verify(credentials)
    record_suggestion_feedback(
        user_id=user["uid"], realm_id=body.qb_company or "", slot=body.slot,
        context_text=body.context_text, vendor_name=body.vendor_name, amount=body.amount,
        suggested_id=body.suggested_id, suggested_name=body.suggested_name,
        suggested_confidence=body.suggested_confidence, suggested_method=body.suggested_method,
        picked_id=body.picked_id, picked_name=body.picked_name,
    )
    return {"recorded": True}


@router.post("/create-batch", dependencies=[Depends(require_permission("quickbooks.push"))])
async def create_batch_endpoint(
    body: CreateBatchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Bulk-create master entities (Tier 5). Each item in `items` declares its
    own entity_type and gets routed to the correct create endpoint. Returns a
    per-item result so the frontend can patch IDs and show per-row status
    instead of failing the whole batch on one bad row."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="create-batch currently supports QBO mode only")
    user = _verify(credentials)
    realm = body.qb_company
    results: List[Dict] = []

    income_account_id: Optional[str] = None  # lazy fetch once for items
    for item in body.items:
        try:
            if item.entity_type == "customer":
                qbo_body = _build_qbo_party_payload(item, party_type="customer")
                res = await qbo_connector.create_customer(user["uid"], qbo_body, realm)
                created = res.get("Customer", res)
                results.append({
                    "ok": True,
                    "entity_type": "customer",
                    "name": item.name,
                    "qbo_entity_id": str(created.get("Id")) if created.get("Id") is not None else None,
                    "qbo_entity_name": created.get("DisplayName") or item.name,
                })
            elif item.entity_type == "vendor":
                qbo_body = _build_qbo_party_payload(item, party_type="vendor")
                res = await qbo_connector.create_vendor(user["uid"], qbo_body, realm)
                created = res.get("Vendor", res)
                results.append({
                    "ok": True,
                    "entity_type": "vendor",
                    "name": item.name,
                    "qbo_entity_id": str(created.get("Id")) if created.get("Id") is not None else None,
                    "qbo_entity_name": created.get("DisplayName") or item.name,
                })
            elif item.entity_type == "account":
                if not item.account_type:
                    raise ValueError("account_type is required for account creation")
                qbo_body = {"Name": item.name, "AccountType": item.account_type}
                if item.detail_type:
                    qbo_body["AccountSubType"] = item.detail_type
                res = await qbo_connector.create_account(user["uid"], qbo_body, realm)
                created = res.get("Account", res)
                results.append({
                    "ok": True,
                    "entity_type": "account",
                    "name": item.name,
                    "qbo_entity_id": str(created.get("Id")) if created.get("Id") is not None else None,
                    "qbo_entity_name": created.get("Name") or item.name,
                })
            elif item.entity_type == "item":
                if income_account_id is None:
                    income_account_id = await _resolve_income_account_id(
                        user["uid"], realm, item.income_account_id
                    ) or ""  # cache the lookup result; "" means we tried and failed
                if not income_account_id:
                    raise ValueError("no Income account exists in this company; create one first")
                qbo_body = {
                    "Name": item.name,
                    "Type": "Service",
                    "IncomeAccountRef": {"value": income_account_id},
                }
                res = await qbo_connector.create_item(user["uid"], qbo_body, realm)
                created = res.get("Item", res)
                results.append({
                    "ok": True,
                    "entity_type": "item",
                    "name": item.name,
                    "qbo_entity_id": str(created.get("Id")) if created.get("Id") is not None else None,
                    "qbo_entity_name": created.get("Name") or item.name,
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


@router.post("/preview-create-account", dependencies=[Depends(require_permission("quickbooks.read"))])
async def preview_create_account(
    body: PreviewCreateAccountRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Look for likely-duplicate accounts before the user actually creates a
    new one. Returns the top fuzzy matches (similarity ≥ 0.80) so the UI can
    suggest 'did you mean Stationery?' instead of letting a near-duplicate
    name slip in. Idempotent: nothing is created here."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="preview-create-account supports QBO mode only")
    proposed = (body.proposed_name or "").strip()
    if not proposed:
        return {"closest_matches": []}
    user = _verify(credentials)

    raw_accounts = await qbo_connector.list_accounts(user["uid"], body.qb_company)
    target_type = (body.account_type or "").strip() or None
    target_norm = normalize_name(proposed)

    scored: List[Dict[str, object]] = []
    for a in raw_accounts:
        name = a.get("Name") or a.get("FullyQualifiedName") or ""
        if not name:
            continue
        if target_type and a.get("AccountType") and a.get("AccountType") != target_type:
            continue
        cand_norm = normalize_name(name)
        if not cand_norm:
            continue
        similarity = fuzz.token_set_ratio(target_norm, cand_norm) / 100.0
        if similarity >= _PREVIEW_SIMILARITY_FLOOR:
            scored.append({
                "list_id": a.get("Id"),
                "name": name,
                "account_type": a.get("AccountType"),
                "similarity": round(similarity, 3),
            })

    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return {"closest_matches": scored[:3]}


# ---------------------------------------------------------------------------
# Document push
# ---------------------------------------------------------------------------

async def _validate_qbo_refs(
    payload: Dict,
    user_id: str,
    realm_id: Optional[str],
    *,
    kind: str,
) -> List[Dict]:
    """Verify every QBO ref in the payload resolves to a real entity in the realm.

    Catches two failure modes that otherwise surface as opaque
    `NumberFormatException: null` errors from Intuit:
      * missing — ref has no `value` (matcher couldn't find the entity)
      * stale  — ref has a `value` that no longer exists in the realm
                 (e.g. user wiped the sandbox between extract and push)

    Returns a list of issues; empty list means the payload is safe to push.
    """
    issues: List[Dict] = []

    def _record(issue_kind: str, ref_name: str, ref_obj: Dict) -> None:
        issues.append({
            "kind": issue_kind,
            "ref": ref_name,
            "name": ref_obj.get("name") or "",
            "value": ref_obj.get("value"),
        })

    if kind == "invoice":
        cust_ref = payload.get("CustomerRef") or {}
        if not cust_ref.get("value"):
            _record("missing", "CustomerRef", cust_ref)

        valid_item_ids: Optional[set] = None
        for idx, line in enumerate(payload.get("Line", [])):
            detail = line.get("SalesItemLineDetail") or {}
            item_ref = detail.get("ItemRef") or {}
            val = item_ref.get("value")
            if not val:
                _record("missing", f"Line[{idx}].ItemRef", item_ref)
                continue
            if valid_item_ids is None:
                items = await qbo_connector.list_items(user_id, realm_id)
                valid_item_ids = {str(i.get("Id")) for i in items if i.get("Id") is not None}
            if str(val) not in valid_item_ids:
                _record("stale", f"Line[{idx}].ItemRef", item_ref)

        if cust_ref.get("value"):
            customers = await qbo_connector.list_customers(user_id, realm_id)
            valid_cust_ids = {str(c.get("Id")) for c in customers if c.get("Id") is not None}
            if str(cust_ref["value"]) not in valid_cust_ids:
                _record("stale", "CustomerRef", cust_ref)

    elif kind == "bill":
        vendor_ref = payload.get("VendorRef") or {}
        if not vendor_ref.get("value"):
            _record("missing", "VendorRef", vendor_ref)

        valid_acct_ids: Optional[set] = None
        for idx, line in enumerate(payload.get("Line", [])):
            detail = line.get("AccountBasedExpenseLineDetail") or {}
            acct_ref = detail.get("AccountRef") or {}
            val = acct_ref.get("value")
            if not val:
                _record("missing", f"Line[{idx}].AccountRef", acct_ref)
                continue
            if valid_acct_ids is None:
                accounts = await qbo_connector.list_accounts(user_id, realm_id)
                valid_acct_ids = {str(a.get("Id")) for a in accounts if a.get("Id") is not None}
            if str(val) not in valid_acct_ids:
                _record("stale", f"Line[{idx}].AccountRef", acct_ref)

        if vendor_ref.get("value"):
            vendors = await qbo_connector.list_vendors(user_id, realm_id)
            valid_vendor_ids = {str(v.get("Id")) for v in vendors if v.get("Id") is not None}
            if str(vendor_ref["value"]) not in valid_vendor_ids:
                _record("stale", "VendorRef", vendor_ref)

    return issues


def _ref_issue_http_error(issues: List[Dict]) -> HTTPException:
    missing = [i for i in issues if i["kind"] == "missing"]
    stale = [i for i in issues if i["kind"] == "stale"]
    hint_parts = []
    if missing:
        names = ", ".join(i["name"] or i["ref"] for i in missing) or "—"
        hint_parts.append(f"Create these in QuickBooks first: {names}.")
    if stale:
        hint_parts.append(
            "Some references point at IDs that no longer exist in QuickBooks "
            "(the sandbox or company data may have changed). Re-extract the "
            "document to refresh the IDs."
        )
    return HTTPException(
        status_code=422,
        detail={
            "error": "invalid_refs",
            "message": " ".join(hint_parts) or "Payload contains invalid QuickBooks references.",
            "issues": issues,
        },
    )


@router.post("/push-invoice", dependencies=[Depends(require_permission("quickbooks.push"))])
async def push_invoice(
    body: PushQBXMLRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a sales invoice. QBO mode: body.payload = QBO Invoice JSON.
    Desktop mode: body.qbxml = <InvoiceAddRq> fragment or QBXML envelope."""
    user = _verify(credentials)
    if _is_qbo():
        if not body.payload:
            raise HTTPException(status_code=400, detail="payload required for QBO invoice push")
        issues = await _validate_qbo_refs(body.payload, user["uid"], body.qb_company, kind="invoice")
        if issues:
            raise _ref_issue_http_error(issues)
        if not body.skip_duplicate_check:
            dup = await qbo_duplicate_check.check_invoice(
                user_id=user["uid"], realm_id=body.qb_company,
                doc_date=(body.payload or {}).get("TxnDate"),
                total_amount=body.extracted_total or (body.payload or {}).get("TotalAmt"),
                doc_number=(body.payload or {}).get("DocNumber"),
                customer_name=((body.payload or {}).get("CustomerRef") or {}).get("name"),
            )
            if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.7:
                raise HTTPException(status_code=409, detail={
                    "message": "This invoice appears to be a duplicate and was already pushed.",
                    "duplicate_check": dup,
                })
        result = await qbo_connector.create_invoice(user["uid"], body.payload, body.qb_company)
        created = result.get("Invoice", result) if isinstance(result, dict) else result
        # Record so the same invoice flags as a duplicate on next extract.
        # Prefer the extractor's gross total (what `check_invoice` queried)
        # over the QBO-returned TotalAmt so the next extract's dedupe lookup
        # uses the same key.
        record_amount = (
            body.extracted_total
            if body.extracted_total is not None
            else (created or {}).get("TotalAmt") or (body.payload or {}).get("TotalAmt")
        )
        qbo_duplicate_check.record_pushed(
            user_id=user["uid"], realm_id=body.qb_company,
            doc_type=qbo_duplicate_check.DOC_SALES,
            date_iso=(body.payload or {}).get("TxnDate") or (created or {}).get("TxnDate"),
            amount=record_amount,
            doc_number=(body.payload or {}).get("DocNumber") or (created or {}).get("DocNumber"),
            party_name=((body.payload or {}).get("CustomerRef") or {}).get("name"),
            qbo_entity_id=str((created or {}).get("Id")) if (created or {}).get("Id") is not None else None,
        )
        return {"result": created}
    if not body.qbxml:
        raise HTTPException(status_code=400, detail="qbxml required for Desktop invoice push")
    return {"result": await manager.push_qb_invoice(user["uid"], body.qbxml, body.qb_company)}


@router.post("/push-bill", dependencies=[Depends(require_permission("quickbooks.push"))])
async def push_bill(
    body: PushQBXMLRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a purchase bill. QBO mode: body.payload = QBO Bill JSON.
    Desktop mode: body.qbxml = <BillAddRq> fragment or QBXML envelope."""
    user = _verify(credentials)
    if _is_qbo():
        if not body.payload:
            raise HTTPException(status_code=400, detail="payload required for QBO bill push")
        issues = await _validate_qbo_refs(body.payload, user["uid"], body.qb_company, kind="bill")
        if issues:
            raise _ref_issue_http_error(issues)
        if not body.skip_duplicate_check:
            dup = await qbo_duplicate_check.check_bill(
                user_id=user["uid"], realm_id=body.qb_company,
                doc_date=(body.payload or {}).get("TxnDate"),
                total_amount=body.extracted_total or sum(
                    float(l.get("Amount") or 0)
                    for l in (body.payload or {}).get("Line") or []
                ),
                doc_number=(body.payload or {}).get("DocNumber"),
                vendor_name=((body.payload or {}).get("VendorRef") or {}).get("name"),
            )
            if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.7:
                raise HTTPException(status_code=409, detail={
                    "message": "This bill appears to be a duplicate and was already pushed.",
                    "duplicate_check": dup,
                })
        result = await qbo_connector.create_bill(user["uid"], body.payload, body.qb_company)
        record_from_bill_payload(
            user_id=user["uid"], realm_id=body.qb_company, payload=body.payload,
        )
        created = result.get("Bill", result) if isinstance(result, dict) else result
        # Use the extractor's gross total (what `check_bill` queried) when
        # available. QBO returns TotalAmt = sum(lines), which excludes tax
        # because Bill payloads here don't carry TxnTaxDetail — that mismatch
        # was the prior duplicate-push regression.
        total = body.extracted_total
        if total is None:
            total = (created or {}).get("TotalAmt")
        if total is None:
            try:
                total = sum(float(l.get("Amount") or 0) for l in (body.payload or {}).get("Line") or [])
            except Exception:
                total = None
        qbo_duplicate_check.record_pushed(
            user_id=user["uid"], realm_id=body.qb_company,
            doc_type=qbo_duplicate_check.DOC_PURCHASE,
            date_iso=(body.payload or {}).get("TxnDate") or (created or {}).get("TxnDate"),
            amount=total,
            doc_number=(body.payload or {}).get("DocNumber") or (created or {}).get("DocNumber"),
            party_name=((body.payload or {}).get("VendorRef") or {}).get("name"),
            qbo_entity_id=str((created or {}).get("Id")) if (created or {}).get("Id") is not None else None,
        )
        return {"result": created}
    if not body.qbxml:
        raise HTTPException(status_code=400, detail="qbxml required for Desktop bill push")
    return {"result": await manager.push_qb_bill(user["uid"], body.qbxml, body.qb_company)}


# ---------------------------------------------------------------------------
# Document extraction (PDF -> structured rows + QBO payload)
# ---------------------------------------------------------------------------
# Currently QBO-only. Desktop mode would route through tally-style extraction
# which is out of scope for the Phase 2 ship.

def _upload_doc_to_r2(user_id: str, flow: str, filename: str, file_bytes: bytes) -> Dict[str, Any]:
    """Best-effort upload of the source document to R2 so a reopened draft can
    preview it (Phase 6). Returns {file_url, r2_key, file_name} or {} when R2 is
    unconfigured / fails — never raises, never blocks extraction."""
    try:
        import uuid as _uuid
        name = filename or "document.pdf"
        key = r2_service.build_r2_key(user_id, f"qbo_{flow}", _uuid.uuid4().hex, name)
        url = r2_service.upload_to_r2(file_bytes, key)
        return {"file_url": url, "r2_key": key, "file_name": name}
    except Exception as exc:  # noqa: BLE001
        logger.warning("QB doc R2 upload skipped: %s", exc)
        return {}


@router.post("/extract-invoice", dependencies=[Depends(require_permission("quickbooks.push"))])
async def extract_invoice_endpoint(
    file: UploadFile = File(...),
    qb_company: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upload a sales-invoice PDF, extract fields, match to QBO customer +
    items, and return a payload ready for /push-invoice."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="extract-invoice currently supports QBO mode only")
    user = _verify(credentials)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty PDF")
    result = await process_sales_pdf(pdf_bytes, user_id=user["uid"], realm_id=qb_company)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    result.update(_upload_doc_to_r2(user["uid"], "sales", file.filename, pdf_bytes))
    return result


@router.post("/extract-bill", dependencies=[Depends(require_permission("quickbooks.push"))])
async def extract_bill_endpoint(
    file: UploadFile = File(...),
    qb_company: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upload a purchase-bill PDF, extract fields, match to QBO vendor +
    expense accounts, and return a payload ready for /push-bill."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="extract-bill currently supports QBO mode only")
    user = _verify(credentials)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty PDF")
    result = await process_purchase_pdf(pdf_bytes, user_id=user["uid"], realm_id=qb_company)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    result.update(_upload_doc_to_r2(user["uid"], "purchase", file.filename, pdf_bytes))
    return result


class DraftFileUrlBody(BaseModel):
    r2_key: str


@router.post("/draft-file-url", dependencies=[Depends(require_permission("quickbooks.read"))])
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
# Bank reconciliation (QBO only — Desktop bank-recon out of scope for v1)
# ---------------------------------------------------------------------------


class ManualMatchRequest(BaseModel):
    qbo_txn: dict
    statement_row: dict
    # Optional: if the frontend sends the current unmatched lists + closing
    # balances, we recompute the summary on the same call and return it. Older
    # callers that omit these still get a wrapped match dict (no summary).
    unmatched_qbo: Optional[List[dict]] = None
    unmatched_statement: Optional[List[dict]] = None
    qbo_closing_balance: Optional[float] = None
    bank_closing_balance: Optional[float] = None


class ResolvePendingRequest(BaseModel):
    """Mark or post leftover unmatched bank-recon rows.

    mode = "mark_pending":   no QBO writes. The frontend already moved the
        rows into its own pending list; the backend just recomputes the
        summary from the remaining unmatched lists (which exclude them).

    mode = "post_to_suspense": create a QBO Deposit (for unmatched bank
        credits) or Purchase (for unmatched bank debits) per row, booked to
        the chosen suspense account. Returns per-row status + the updated
        summary.
    """
    mode: str  # "mark_pending" | "post_to_suspense"
    unmatched_qbo: List[dict] = []
    unmatched_statement: List[dict] = []
    qbo_closing_balance: Optional[float] = None
    bank_closing_balance: Optional[float] = None

    # Post-to-suspense only:
    qb_company: Optional[str] = None
    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    suspense_account_id: Optional[str] = None
    suspense_account_name: Optional[str] = None
    # Indices into unmatched_statement to post. Defaults to all.
    statement_row_indices: Optional[List[int]] = None


class BankEntryPushRequest(BaseModel):
    qb_company: Optional[str] = None
    entry_type: str  # "deposit" | "purchase" | "payment"
    payload: dict
    extracted_total: Optional[float] = None


class QBChatRequest(BaseModel):
    message: str
    qb_company: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None
    thread_id: Optional[str] = None


class SaveDraftRequest(BaseModel):
    """Persist in-progress purchase / sales / bank-entries work as a draft."""
    flow: str  # 'purchase' | 'sales' | 'bank_entries'
    payload: Dict
    title: Optional[str] = None
    draft_id: Optional[str] = None   # if set, updates the existing draft
    qb_company: Optional[str] = None
    # Source-document pointers (populated by the extract endpoint via
    # _upload_doc_to_r2). Stored in dedicated columns so the draft picker can
    # show file metadata without parsing the JSONB payload; on reopen the
    # frontend uses r2_key to mint a signed URL via /draft-file-url.
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    r2_key: Optional[str] = None


class SaveReconReportRequest(BaseModel):
    qb_company: Optional[str] = None
    account_id: str
    account_name: Optional[str] = None
    period_start: str
    period_end: str
    statement: dict
    qbo_txns: List[dict]
    matches: List[dict]
    summary: dict
    qbo_closing_balance: Optional[float] = None
    needs_review: bool = False
    status: str = "draft"
    # Rows the user flagged "pending review" — persisted inside summary_json
    # so no schema migration is needed. Each entry: {side: "qbo"|"statement", row}.
    pending_review: Optional[List[dict]] = None


@router.get("/bank/list-accounts", dependencies=[Depends(require_permission("quickbooks.read"))])
async def bank_list_accounts(
    qb_company: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return Bank + CreditCard accounts the user can reconcile against."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="bank reconciliation supports QBO mode only")
    user = _verify(credentials)
    try:
        accounts = await list_bank_accounts(user["uid"], qb_company)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"accounts": accounts}


@router.post("/bank/extract-and-match", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_extract_and_match(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    qb_company: Optional[str] = Form(None),
    period_start: Optional[str] = Form(None),
    period_end: Optional[str] = Form(None),
    qbo_closing_balance: Optional[float] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """One-shot: parse statement, fetch QBO txns for the chosen account in
    the same window, run the auto-matcher, return both sides + pairs."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="bank reconciliation supports QBO mode only")
    user = _verify(credentials)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")

    result = await process_bank_statement(
        file_content=file_bytes,
        file_name=file.filename or "statement.pdf",
        user_id=user["uid"],
        realm_id=qb_company,
        account_id=account_id,
        period_start=period_start,
        period_end=period_end,
        qbo_closing_balance=qbo_closing_balance,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/bank/manual-match", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_manual_match_endpoint(
    payload: ManualMatchRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Wrap a user-driven pair as a manual match. Frontend keeps the working
    set; /bank/save persists once done. If the caller sends the current
    unmatched lists + closing balances we also return a fresh summary so the
    UI numbers stay in sync (rather than freezing at extract time)."""
    _verify(credentials)
    match = await bank_manual_match(
        qbo_txn=payload.qbo_txn,
        statement_row=payload.statement_row,
    )
    response: Dict = {"match": match}
    # Older callers send only qbo_txn / statement_row and expect the bare
    # match dict back. Keep that working by spreading the match fields too.
    response.update(match)

    if payload.unmatched_qbo is not None and payload.unmatched_statement is not None:
        response["summary"] = bank_recompute_summary(
            unmatched_qbo=payload.unmatched_qbo,
            unmatched_statement=payload.unmatched_statement,
            qbo_closing_balance=payload.qbo_closing_balance,
            bank_closing_balance=payload.bank_closing_balance,
        )
    return response


@router.post("/bank/resolve-pending", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_resolve_pending_endpoint(
    payload: ResolvePendingRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Resolve leftover unmatched rows in one of two ways:

    * mode="mark_pending"      — recompute summary from the trimmed unmatched
                                  lists (frontend has already moved the rows
                                  into its pending bucket). No QBO writes.
    * mode="post_to_suspense"  — create one QBO Deposit (bank credit row) or
                                  Purchase (bank debit row) per selected
                                  statement row, booked to the chosen
                                  suspense account. Posts run sequentially;
                                  partial-failure is surfaced per row.
    """
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="bank reconciliation supports QBO mode only")
    if payload.mode not in ("mark_pending", "post_to_suspense"):
        raise HTTPException(status_code=400, detail="mode must be 'mark_pending' or 'post_to_suspense'")
    user = _verify(credentials)

    posted: List[Dict] = []
    remaining_statement = list(payload.unmatched_statement or [])

    if payload.mode == "post_to_suspense":
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
            entry_type = "deposit" if credit and not debit else "purchase"
            amount = float(credit if entry_type == "deposit" else debit) or 0.0
            desc = row.get("description") or "Suspense entry"
            txn_date = row.get("date")

            line = {
                "Amount": amount,
                "Description": desc,
            }
            if entry_type == "deposit":
                line["DetailType"] = "DepositLineDetail"
                line["DepositLineDetail"] = {
                    "AccountRef": {"value": payload.suspense_account_id, "name": payload.suspense_account_name or ""},
                }
                qbo_payload = {
                    "DepositToAccountRef": {"value": payload.bank_account_id, "name": payload.bank_account_name or ""},
                    "Line": [line],
                }
                if txn_date:
                    qbo_payload["TxnDate"] = txn_date
                creator = qbo_connector.create_deposit
                result_key = "Deposit"
            else:
                line["DetailType"] = "AccountBasedExpenseLineDetail"
                line["AccountBasedExpenseLineDetail"] = {
                    "AccountRef": {"value": payload.suspense_account_id, "name": payload.suspense_account_name or ""},
                }
                qbo_payload = {
                    "AccountRef": {"value": payload.bank_account_id, "name": payload.bank_account_name or ""},
                    "PaymentType": "Cash",
                    "Line": [line],
                }
                if txn_date:
                    qbo_payload["TxnDate"] = txn_date
                creator = qbo_connector.create_purchase
                result_key = "Purchase"

            try:
                res = await creator(user["uid"], qbo_payload, payload.qb_company)
                created = res.get(result_key, res) if isinstance(res, dict) else res
                posted.append({
                    "ok": True,
                    "statement_index": idx,
                    "entry_type": entry_type,
                    "amount": amount,
                    "qbo_id": (created or {}).get("Id"),
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
        unmatched_qbo=payload.unmatched_qbo or [],
        unmatched_statement=remaining_statement,
        qbo_closing_balance=payload.qbo_closing_balance,
        bank_closing_balance=payload.bank_closing_balance,
    )

    return {
        "mode": payload.mode,
        "posted": posted,
        "remaining_statement": remaining_statement,
        "summary": summary,
    }


@router.post("/bank/save", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_save_report(
    payload: SaveReconReportRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Persist the current working state as a draft (or completed) report."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="bank reconciliation supports QBO mode only")
    user = _verify(credentials)
    # Embed pending_review inside summary_json to avoid a schema migration.
    summary_to_store = dict(payload.summary or {})
    if payload.pending_review is not None:
        summary_to_store["pending_review"] = payload.pending_review
        summary_to_store["pending_count"] = len(payload.pending_review)
    report_id = recon_save_report(
        user_id=user["uid"],
        realm_id=payload.qb_company or "",
        account_id=payload.account_id,
        account_name=payload.account_name,
        period_start=payload.period_start,
        period_end=payload.period_end,
        statement=payload.statement,
        qbo_txns=payload.qbo_txns,
        matches=payload.matches,
        summary=summary_to_store,
        qbo_closing_balance=payload.qbo_closing_balance,
        needs_review=payload.needs_review,
        status=payload.status,
    )
    if not report_id:
        raise HTTPException(status_code=500, detail="Could not persist report")
    return {"report_id": report_id}


@router.get("/bank/reports", dependencies=[Depends(require_permission("quickbooks.read"))])
async def bank_list_reports(
    qb_company: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    reports = recon_list_reports(user["uid"], qb_company)
    return {"reports": reports}


@router.get("/bank/reports/{report_id}", dependencies=[Depends(require_permission("quickbooks.read"))])
async def bank_get_report(
    report_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    report = recon_get_report(report_id, user["uid"])
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.delete("/bank/reports/{report_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_delete_report(
    report_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    if not recon_delete_report(report_id, user["uid"]):
        raise HTTPException(status_code=404, detail="Report not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Bank Entries (statement -> create QBO txns)
# ---------------------------------------------------------------------------

@router.post("/bank-entries/extract", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_entries_extract(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    account_name: Optional[str] = Form(None),
    qb_company: Optional[str] = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Parse statement, classify rows, match counterparty + account, return
    draft QBO payloads ready for user review before pushing."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="bank entries supports QBO mode only")
    user = _verify(credentials)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    result = await process_bank_statement_for_entries(
        file_content=content,
        file_name=file.filename or "statement.pdf",
        user_id=user["uid"],
        realm_id=qb_company,
        bank_account_id=account_id,
        bank_account_name=account_name,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/bank-entries/push-one", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_entries_push_one_endpoint(
    body: BankEntryPushRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Push a single entry (deposit, purchase, or payment) to QBO."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="bank entries supports QBO mode only")
    user = _verify(credentials)
    return await bank_entries_push_one(
        user_id=user["uid"],
        realm_id=body.qb_company,
        entry_type=body.entry_type,
        payload=body.payload,
        extracted_total=body.extracted_total,
    )


@router.post("/bank-entries/validate", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_entries_validate_endpoint(
    body: ValidateBankVouchersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Pre-push validation for normalized bank vouchers (QB analog of Tally's
    /validate-pre-push). Returns errors, missing_masters, and duplicates."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="bank entries supports QBO mode only")
    user = _verify(credentials)
    return await bank_entries_validate(
        user_id=user["uid"],
        realm_id=body.qb_company,
        bank_account_id=body.bank_account_id,
        bank_account_name=body.bank_account_name,
        vouchers=body.vouchers,
    )


@router.post("/bank-entries/push-batch", dependencies=[Depends(require_permission("quickbooks.push"))])
async def bank_entries_push_batch_endpoint(
    body: PushBankVouchersRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Build and push a batch of normalized bank vouchers to QBO (QB analog of
    Tally's /push-vouchers-batch). The backend owns all payload construction."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="bank entries supports QBO mode only")
    user = _verify(credentials)
    return await bank_entries_push_batch(
        user_id=user["uid"],
        realm_id=body.qb_company,
        bank_account_id=body.bank_account_id,
        bank_account_name=body.bank_account_name,
        vouchers=body.vouchers,
        auto_create_masters=body.auto_create_masters,
    )


@router.get("/bank-entries/open-bills", dependencies=[Depends(require_permission("quickbooks.read"))])
async def bank_entries_open_bills(
    qb_company: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Open vendor bills available to be paid via a BillPayment. Used by the
    Payment-type picker in bank entries."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="QBO mode only")
    user = _verify(credentials)
    sql = (
        "SELECT Id, DocNumber, TxnDate, DueDate, TotalAmt, Balance, VendorRef "
        "FROM Bill WHERE Balance > '0' ORDER BY TxnDate DESC MAXRESULTS 200"
    )
    data = await qbo_connector.query(user["uid"], sql, qb_company)
    rows = ((data or {}).get("QueryResponse") or {}).get("Bill") or []
    out = [{
        "id": str(r.get("Id")),
        "doc_number": r.get("DocNumber"),
        "txn_date": r.get("TxnDate"),
        "due_date": r.get("DueDate"),
        "total": float(r.get("TotalAmt") or 0),
        "balance": float(r.get("Balance") or 0),
        "vendor_id": (r.get("VendorRef") or {}).get("value"),
        "vendor_name": (r.get("VendorRef") or {}).get("name"),
    } for r in rows]
    return {"bills": out}


@router.get("/bank-entries/open-invoices", dependencies=[Depends(require_permission("quickbooks.read"))])
async def bank_entries_open_invoices(
    qb_company: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Open customer invoices available to receive a Payment against. Used by
    the Payment-type picker in bank entries when the row is a credit."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="QBO mode only")
    user = _verify(credentials)
    sql = (
        "SELECT Id, DocNumber, TxnDate, DueDate, TotalAmt, Balance, CustomerRef "
        "FROM Invoice WHERE Balance > '0' ORDER BY TxnDate DESC MAXRESULTS 200"
    )
    data = await qbo_connector.query(user["uid"], sql, qb_company)
    rows = ((data or {}).get("QueryResponse") or {}).get("Invoice") or []
    out = [{
        "id": str(r.get("Id")),
        "doc_number": r.get("DocNumber"),
        "txn_date": r.get("TxnDate"),
        "due_date": r.get("DueDate"),
        "total": float(r.get("TotalAmt") or 0),
        "balance": float(r.get("Balance") or 0),
        "customer_id": (r.get("CustomerRef") or {}).get("value"),
        "customer_name": (r.get("CustomerRef") or {}).get("name"),
    } for r in rows]
    return {"invoices": out}


# ---------------------------------------------------------------------------
# Drafts (purchase / sales / bank_entries)
# ---------------------------------------------------------------------------

@router.post("/drafts", dependencies=[Depends(require_permission("quickbooks.push"))])
async def qbo_drafts_save(
    body: SaveDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Upsert a draft. If `draft_id` is set we update; otherwise we insert."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="drafts support QBO mode only")
    user = _verify(credentials)
    new_id = qbo_drafts_store.upsert(
        user_id=user["uid"],
        realm_id=body.qb_company or "",
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


@router.get("/drafts", dependencies=[Depends(require_permission("quickbooks.read"))])
async def qbo_drafts_list(
    flow: Optional[str] = Query(None),
    qb_company: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="CSV filter, e.g. draft,pushed"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    rows = qbo_drafts_store.list_drafts(
        user_id=user["uid"], realm_id=qb_company, flow=flow, status=status
    )
    return {"drafts": rows}


@router.get("/drafts/{draft_id}", dependencies=[Depends(require_permission("quickbooks.read"))])
async def qbo_drafts_get(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    row = qbo_drafts_store.get_draft(user_id=user["uid"], draft_id=draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    return row


@router.delete("/drafts/{draft_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def qbo_drafts_delete(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = _verify(credentials)
    ok = qbo_drafts_store.delete_draft(user_id=user["uid"], draft_id=draft_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"deleted": True}


class UpdateDraftRequest(BaseModel):
    """Save edits to an existing draft's payload."""
    payload: Dict


class ValidateDraftRequest(BaseModel):
    draft_id: str


@router.put("/drafts/{draft_id}", dependencies=[Depends(require_permission("quickbooks.push"))])
async def qbo_drafts_update(
    draft_id: str,
    body: UpdateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Update the payload of an existing draft (save user edits). This is the
    explicit user-driven save path, so we promote the row to 'draft'."""
    user = _verify(credentials)
    ok = qbo_drafts_store.update_payload(
        draft_id=draft_id,
        user_id=user["uid"],
        payload=body.payload,
        set_status="draft",
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.patch("/drafts/{draft_id}/push", dependencies=[Depends(require_permission("quickbooks.push"))])
async def qbo_drafts_mark_pushed(
    draft_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Mark a draft as pushed to QuickBooks (lifecycle bookkeeping for the
    document library / bulk-push view)."""
    user = _verify(credentials)
    ok = qbo_drafts_store.mark_pushed(draft_id=draft_id, user_id=user["uid"])
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True}


@router.post("/validate-draft-pushable", dependencies=[Depends(require_permission("quickbooks.read"))])
async def qbo_validate_draft_pushable(
    body: ValidateDraftRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Pre-push validation for the bulk-push flow: is QuickBooks still connected
    and does the draft's company (realm) still resolve? Surfaces fixable issues
    before we attempt a push. Mirrors Tally's /validate-draft-pushable, adapted
    for the cloud OAuth model — there is no local company/ledger bridge to probe,
    so we validate connectivity + realm presence rather than ledger existence."""
    user = _verify(credentials)
    uid = user["uid"]

    draft = qbo_drafts_store.get_draft(user_id=uid, draft_id=body.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    issues: List[str] = []
    connected = qbo_is_user_connected(uid)
    if not connected:
        issues.append("QuickBooks is not connected. Reconnect to push.")

    company_id = (draft.get("realm_id") or "").strip()
    company_ok = True
    if connected and company_id:
        try:
            realms = qbo_list_realms(uid) or []
            realm_ids = {str(r.get("realm_id")) for r in realms if r.get("realm_id")}
            company_ok = company_id in realm_ids
            if not company_ok:
                issues.append("This draft's QuickBooks company is no longer connected.")
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
# QB Chat (NDJSON streaming, mirrors /ca/tally-chat shape)
# ---------------------------------------------------------------------------

@router.post("/chat", dependencies=[Depends(require_permission("chat.use"))])
async def qb_chat_endpoint(
    body: QBChatRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Stream NDJSON events for QB chat. Persists user + assistant messages so
    history survives page reloads. Reuses the latest thread for (user, realm)
    unless the frontend pins an explicit `thread_id`.

    Stream event types: status / tool_call / tool_result / answer_delta /
    error / done. The first event is always `meta` carrying the thread_id so
    the frontend can store it for future turns / reloads."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="QB chat supports QBO mode only")
    user = _verify(credentials)

    thread_id = body.thread_id or qb_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], realm_id=body.qb_company,
    )

    # Hydrate history from the DB when the frontend didn't pass one. This is
    # what makes a fresh page load keep prior turns in context.
    history = body.history
    if not history:
        rows = qb_chat_store.load_thread(
            user_id=user["uid"], realm_id=body.qb_company, thread_id=thread_id,
        )
        history = [{"role": r["role"], "content": r["content"]} for r in rows if r.get("content")]

    # Record the new user turn before streaming starts so it survives even if
    # the assistant blows up mid-answer.
    qb_chat_store.append_message(
        user_id=user["uid"], realm_id=body.qb_company, thread_id=thread_id,
        role="user", content=body.message,
    )

    async def event_generator():
        final_answer_parts: list[str] = []
        tool_trace: list[dict] = []
        try:
            yield json.dumps({"type": "meta", "thread_id": thread_id}) + "\n\n"
            async for event in run_qb_chat(
                user_id=user["uid"],
                realm_id=body.qb_company,
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
            logger.exception("qb chat agent error")
            yield json.dumps({"type": "error", "content": str(exc)}) + "\n\n"
            yield json.dumps({"type": "done"}) + "\n\n"
        finally:
            answer = "".join(final_answer_parts).strip()
            if answer:
                qb_chat_store.append_message(
                    user_id=user["uid"], realm_id=body.qb_company, thread_id=thread_id,
                    role="assistant", content=answer,
                    metadata={"tools": tool_trace} if tool_trace else None,
                )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def qb_chat_history(
    qb_company: Optional[str] = None,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the chat history for (user, realm, thread). If thread_id is
    omitted, returns the latest thread for the user/realm."""
    user = _verify(credentials)
    tid = thread_id or qb_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], realm_id=qb_company,
    )
    messages = qb_chat_store.load_thread(
        user_id=user["uid"], realm_id=qb_company, thread_id=tid,
    )
    return {"thread_id": tid, "messages": messages}


@router.delete("/chat/history", dependencies=[Depends(require_permission("chat.use"))])
async def qb_chat_clear(
    qb_company: Optional[str] = None,
    thread_id: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Clear messages for (user, realm, thread). If no thread_id is given,
    clears the latest one — frontend treats this as 'start a fresh chat'."""
    user = _verify(credentials)
    tid = thread_id or qb_chat_store.get_or_create_latest_thread(
        user_id=user["uid"], realm_id=qb_company,
    )
    qb_chat_store.clear_thread(
        user_id=user["uid"], realm_id=qb_company, thread_id=tid,
    )
    return {"ok": True, "thread_id": tid}


# ---------------------------------------------------------------------------
# Company brief (cached QBO data summary for the chat agent)
# ---------------------------------------------------------------------------

@router.post("/brief/refresh", dependencies=[Depends(require_permission("quickbooks.push"))])
async def qb_brief_refresh(
    qb_company: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Rebuild the company brief from live QBO data and cache it."""
    if not _is_qbo():
        raise HTTPException(status_code=400, detail="Brief supports QBO mode only")
    user = _verify(credentials)
    realm = qb_company
    if not realm:
        realms = qbo_list_realms(user["uid"])
        if not realms:
            raise HTTPException(status_code=400, detail="No QBO company connected")
        realm = realms[0]["realm_id"]
    brief = await qbo_refresh_brief(user["uid"], realm)
    return {"ok": True, "realm_id": realm, "brief_length": len(brief)}


@router.get("/brief", dependencies=[Depends(require_permission("quickbooks.read"))])
async def qb_brief_get(
    qb_company: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the cached company brief (if any)."""
    user = _verify(credentials)
    realm = qb_company
    if not realm:
        realms = qbo_list_realms(user["uid"])
        if not realms:
            return {"brief": None}
        realm = realms[0]["realm_id"]
    brief = qbo_load_brief(user["uid"], realm)
    return {"realm_id": realm, "brief": brief}
