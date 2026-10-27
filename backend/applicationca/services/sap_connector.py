"""
SAP S/4HANA Cloud connector — OAuth2 + OData (read/write) + SOAP A2X (posting).

Mirrors the shape of zoho_connector / xero_connector but adapted to the realities
of S/4HANA Cloud Public Edition (researched against api.sap.com / help.sap.com):

  AUTH
    OAuth2 against an SAP BTP / S/4HANA token endpoint, two modes via
    settings.SAP_AUTH_MODE:
      - client_credentials (default, B2B): one technical Communication User per
        client tenant. No consent, no refresh token; we mint a fresh bearer when
        the cached one nears expiry.
      - authorization_code: per-user consent (mirrors Zoho/Xero), rotating refresh.

  READS — OData V2 (no CSRF on GET)
    * GL chart of accounts:  API_GLACCOUNTINCHARTOFACCOUNTS_SRV  (name is in the
      separate A_GLAccountText entity → $expand=to_Text)
    * Business partners:     API_BUSINESS_PARTNER  (roles on A_BusinessPartnerRole
      sub-entity → $expand=to_BusinessPartnerRole, filter client-side)
    * Open A/P & A/R items:  API_OPLACCTGDOCITEMCUBE_SRV  (filter IsCleared eq false,
      FinancialAccountType K=supplier / D=customer)
    * Trial balance:         C_TRIALBALANCE_CDS  (parameterized P_From/ToPostingDate)

  WRITES — OData V2 (CSRF + /Date(ms)/ dates)
    * Business partner create:  POST A_BusinessPartner (deep-insert roles)
    * Supplier (A/P) invoice:   POST A_SupplierInvoice  (API_SUPPLIERINVOICE_PROCESS_SRV)

  WRITES — SOAP A2X (NO CSRF; auth via comm arrangement)
    * Journal entry / customer (A/R) invoice posting:
      JournalEntryCreateRequestConfirmation_In  (synchronous, SAP_COM_0002).
      THIS IS THE KEY FINDING: S/4HANA Cloud Public Edition has NO OData service
      that posts a journal entry — posting is SOAP-only. Endpoint comes from the
      communication arrangement → settings.SAP_SOAP_JOURNAL_ENTRY_URL.

  COMPANY KEY
    The tenant is the API host (settings.SAP_API_BASE). Company Code selects the
    posting org per request (our organization_id/tenant_id analogue) and is
    MANDATORY on every accounting document.

VERIFY flags below mark items that research could only confirm to medium/low
confidence (exact field names, type codes, SOAP namespace/operation) — check
against your tenant's $metadata / WSDL before go-live.

Token storage: postgres `sap_tokens` (one row per user_id).
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from uuid import uuid4
from xml.sax.saxutils import escape as _xml_escape

import httpx
from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from config.settings import settings

logger = logging.getLogger(__name__)

REFRESH_LEEWAY_SECONDS = 60

# ----------------------------------------------------------------------
# OData service paths (relative to SAP_API_BASE).
# ----------------------------------------------------------------------
SVC_GL_ACCOUNT = "/API_GLACCOUNTINCHARTOFACCOUNTS_SRV"   # G/L account master (read-only)
SVC_BUSINESS_PARTNER = "/API_BUSINESS_PARTNER"           # customers & suppliers (read/write)
SVC_SUPPLIER_INVOICE = "/API_SUPPLIERINVOICE_PROCESS_SRV"  # A/P invoice post (read/write)
SVC_OPEN_ITEMS = "/API_OPLACCTGDOCITEMCUBE_SRV"          # accounting line-item cube (read)
SVC_TRIAL_BALANCE = "/C_TRIALBALANCE_CDS"                # trial balance (read)

# Business Partner role prefixes (FLCUxx customer, FLVNxx supplier).
BP_ROLE_CUSTOMER_PREFIX = "FLCU"
BP_ROLE_SUPPLIER_PREFIX = "FLVN"

# FinancialAccountType codes on the open-item cube (VERIFY against value help).
FIN_ACCT_TYPE_CUSTOMER = "D"   # A/R
FIN_ACCT_TYPE_SUPPLIER = "K"   # A/P
FIN_ACCT_TYPE_GL = "S"

# Standard FI defaults for generic Journal Entry posting (high confidence).
JE_BUSINESS_TXN_TYPE = "RFBU"
JE_REF_DOC_TYPE = "BKPFF"
JE_DOC_TYPE_GENERAL = "SA"          # generic GL document
JE_DOC_TYPE_CUSTOMER_INVOICE = "DR"  # customer (A/R) invoice

# SOAP namespace for the Journal Entry A2X message. VERIFY against the WSDL — the
# A2X SAPGlobal20 namespace is the common case but the exact URI can differ.
SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
JE_MSG_NS = "http://sap.com/xi/SAPGlobal20/Global"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _odata_v2_date(d: Any) -> Optional[str]:
    """Encode a date as the OData V2 JSON `/Date(epochMillis)/` form (UTC midnight)."""
    if d is None:
        return None
    if isinstance(d, str):
        try:
            dt = datetime.strptime(d[:10], "%Y-%m-%d")
        except ValueError:
            return d
    elif isinstance(d, datetime):
        dt = d
    else:
        return str(d)
    ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return f"/Date({ms})/"


# ======================================================================
# Token storage (postgres sap_tokens) — keyed by user_id only.
# ======================================================================

def save_tokens(
    user_id: str,
    access_token: str,
    *,
    refresh_token: Optional[str] = None,
    expires_in: int = 3600,
    api_base: Optional[str] = None,
    company_code: Optional[str] = None,
    auth_mode: Optional[str] = None,
) -> None:
    """Upsert tokens for a user. authorization_code refresh tokens rotate (we
    overwrite); client_credentials has none (COALESCE keeps any prior value)."""
    expires_at = _utcnow() + timedelta(seconds=int(expires_in or 3600))
    api_base = api_base or settings.SAP_API_BASE
    auth_mode = auth_mode or settings.SAP_AUTH_MODE

    conn = get_connection()
    if conn is None:
        logger.error("sap_tokens save skipped — DB unavailable")
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO sap_tokens
                (user_id, access_token, refresh_token, expires_at, api_base,
                 company_code, auth_mode, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = COALESCE(EXCLUDED.refresh_token, sap_tokens.refresh_token),
                expires_at    = EXCLUDED.expires_at,
                api_base      = EXCLUDED.api_base,
                company_code  = COALESCE(EXCLUDED.company_code, sap_tokens.company_code),
                auth_mode     = EXCLUDED.auth_mode,
                updated_at    = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, expires_at, api_base, company_code, auth_mode),
        )
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)


def load_tokens(user_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        logger.warning("sap_tokens lookup skipped — DB unavailable")
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM sap_tokens WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    finally:
        return_connection(conn)


def delete_tokens(user_id: str) -> None:
    conn = get_connection()
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM sap_tokens WHERE user_id=%s", (user_id,))
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)


# ======================================================================
# OAuth2 flow primitives
# ======================================================================

def build_authorize_url(state: str, scope: Optional[str] = None) -> str:
    """Build the SAP consent URL (authorization_code mode only)."""
    params = {
        "response_type": "code",
        "client_id": settings.SAP_CLIENT_ID,
        "redirect_uri": settings.SAP_REDIRECT_URI,
        "state": state,
    }
    scope = scope or settings.SAP_SCOPES
    if scope:
        params["scope"] = scope
    return f"{settings.SAP_AUTHORIZE_URL}?{urlencode(params)}"


async def fetch_client_credentials_token() -> Dict[str, Any]:
    """Mint a bearer token via the client_credentials grant (Basic auth header)."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            settings.SAP_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(settings.SAP_CLIENT_ID, settings.SAP_CLIENT_SECRET),
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise HTTPException(status_code=400, detail=f"SAP token error: {data.get('error')}")
        return data


async def exchange_code(code: str) -> Dict[str, Any]:
    """Exchange an auth code for tokens (authorization_code mode)."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            settings.SAP_TOKEN_URL,
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": settings.SAP_REDIRECT_URI},
            auth=(settings.SAP_CLIENT_ID, settings.SAP_CLIENT_SECRET),
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise HTTPException(status_code=400, detail=f"SAP token error: {data.get('error')}")
        return data


async def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            settings.SAP_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(settings.SAP_CLIENT_ID, settings.SAP_CLIENT_SECRET),
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise HTTPException(status_code=400, detail=f"SAP refresh error: {data.get('error')}")
        return data


async def ensure_access_token(user_id: str) -> Tuple[str, str]:
    """Return (access_token, api_base). Mints (client_credentials) or refreshes
    (authorization_code) when within REFRESH_LEEWAY_SECONDS of expiry."""
    mode = settings.SAP_AUTH_MODE
    tokens = load_tokens(user_id)

    def _fresh(t: Optional[Dict[str, Any]]) -> bool:
        return bool(t and t.get("expires_at")
                    and (t["expires_at"] - _utcnow()).total_seconds() > REFRESH_LEEWAY_SECONDS)

    if mode == "client_credentials":
        if _fresh(tokens):
            return tokens["access_token"], (tokens.get("api_base") or settings.SAP_API_BASE)
        if not (settings.SAP_CLIENT_ID and settings.SAP_CLIENT_SECRET and settings.SAP_TOKEN_URL):
            raise RuntimeError("SAP client_credentials not configured (SAP_CLIENT_ID/SECRET/TOKEN_URL)")
        data = await fetch_client_credentials_token()
        save_tokens(user_id, data["access_token"], refresh_token=None,
                    expires_in=data.get("expires_in", 3600),
                    api_base=settings.SAP_API_BASE, auth_mode="client_credentials")
        return data["access_token"], settings.SAP_API_BASE

    # authorization_code
    if not tokens:
        raise RuntimeError("SAP is not connected for this user")
    if not _fresh(tokens):
        if not tokens.get("refresh_token"):
            raise RuntimeError("SAP access token expired and no refresh token — reconnect")
        refreshed = await refresh_access_token(tokens["refresh_token"])
        save_tokens(user_id, refreshed["access_token"],
                    refresh_token=refreshed.get("refresh_token") or tokens["refresh_token"],
                    expires_in=refreshed.get("expires_in", 3600),
                    api_base=tokens.get("api_base"), auth_mode="authorization_code")
        return refreshed["access_token"], (tokens.get("api_base") or settings.SAP_API_BASE)
    return tokens["access_token"], (tokens.get("api_base") or settings.SAP_API_BASE)


def is_user_connected(user_id: str) -> bool:
    """client_credentials → server is configured. authorization_code → token row exists."""
    if settings.SAP_AUTH_MODE == "client_credentials":
        return bool(settings.SAP_CLIENT_ID and settings.SAP_CLIENT_SECRET and settings.SAP_TOKEN_URL)
    return load_tokens(user_id) is not None


# ======================================================================
# REST / OData client
# ======================================================================

def _service_root(path: str) -> str:
    """First path segment = the OData service root (used for the CSRF fetch)."""
    parts = path.strip("/").split("/")
    return "/" + parts[0] if parts and parts[0] else path


# Display-only keys our pipelines attach to payloads (stripped before sending).
_DISPLAY_TOP_KEYS = ("customer_name", "vendor_name", "account_name", "_meta")


class SAPConnector:
    """Async OData + SOAP client for SAP S/4HANA Cloud.

    All public methods take a `user_id` (Firebase UID). `company_code` selects the
    posting org and is required on every accounting write. List methods return
    normalized "cards" (list_id / name / ...) so downstream matcher + controller
    code is identical in shape to the Zoho/Xero paths.
    """

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    # ---------------- low-level OData transport ----------------

    async def _request(
        self,
        user_id: str,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        access_token, api_base = await ensure_access_token(user_id)
        url = f"{api_base}{path}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if method.upper() != "GET":
                # OData V2 CSRF handshake: fetch token + session cookies from the
                # service root, then replay them (cookie jar shared by `client`).
                csrf_resp = await client.get(
                    f"{api_base}{_service_root(path)}",
                    headers={**headers, "X-CSRF-Token": "Fetch"},
                )
                token = csrf_resp.headers.get("x-csrf-token")
                if token:
                    headers["X-CSRF-Token"] = token
                if json_body is not None:
                    headers["Content-Type"] = "application/json"

            resp = await client.request(method, url, params=params, json=json_body, headers=headers)
            if resp.status_code >= 400:
                logger.error(f"SAP {method} {path} -> {resp.status_code}: {resp.text[:500]}")
                raise HTTPException(status_code=resp.status_code, detail=self._parse_odata_error(resp))
            if not resp.headers.get("content-type", "").startswith("application/json"):
                return {"raw": resp.text}
            return resp.json()

    @staticmethod
    def _parse_odata_error(resp: httpx.Response) -> Any:
        """OData V2 error body: {"error":{"code":..,"message":{"value":..}}}."""
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, dict):
                    msg = msg.get("value")
                return {"sap_error": msg, "sap_code": err.get("code")}
        except Exception:  # noqa: BLE001
            pass
        return resp.text or "SAP request failed"

    @staticmethod
    def _rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize an OData collection response to a list (V2 {d:{results}} or V4 {value})."""
        if not isinstance(data, dict):
            return []
        if "value" in data:                       # OData V4
            return data.get("value") or []
        d = data.get("d")                          # OData V2
        if isinstance(d, dict):
            return d.get("results") or []
        if isinstance(d, list):
            return d
        return []

    @staticmethod
    def _entity(data: Dict[str, Any]) -> Dict[str, Any]:
        """Unwrap a single OData V2 entity ({"d": {...}})."""
        if isinstance(data, dict) and isinstance(data.get("d"), dict):
            return data["d"]
        return data

    @staticmethod
    def _nav_rows(value: Any) -> List[Dict[str, Any]]:
        """Normalize an expanded nav property ({"results":[...]} | list | dict)."""
        if isinstance(value, dict):
            if "results" in value:
                return value.get("results") or []
            return [value]
        if isinstance(value, list):
            return value
        return []

    @staticmethod
    def _clean_body(body: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(body, dict):
            return body
        return {k: v for k, v in body.items() if k not in _DISPLAY_TOP_KEYS}

    # ---------------- normalized cards ----------------

    def _account_card(self, a: Dict[str, Any]) -> Dict[str, Any]:
        # Name lives in the expanded to_Text nav (A_GLAccountText). Prefer the
        # configured language, else the first text row.
        name = None
        texts = self._nav_rows(a.get("to_Text"))
        if texts:
            chosen = next((t for t in texts if t.get("Language") == settings.SAP_LANGUAGE), texts[0])
            name = chosen.get("GLAccountName") or chosen.get("GLAccountLongName")
        gl_type = a.get("GLAccountType")
        return {
            "list_id": a.get("GLAccount"),
            "name": name or a.get("GLAccount"),
            "account_type": gl_type,
            "is_balance_sheet": a.get("IsBalanceSheetAccount"),
            "account_group": a.get("GLAccountGroup"),
            "raw": a,
        }

    @staticmethod
    def _bp_card(b: Dict[str, Any]) -> Dict[str, Any]:
        name = (b.get("BusinessPartnerFullName") or b.get("OrganizationBPName1")
                or " ".join(x for x in (b.get("FirstName"), b.get("LastName")) if x).strip())
        return {
            "list_id": b.get("BusinessPartner"),
            "name": name or b.get("BusinessPartner"),
            "raw": b,
        }

    # ---------------- master reads ----------------

    async def list_accounts(self, user_id: str, company_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """Chart-of-accounts GL accounts (with names via $expand=to_Text)."""
        data = await self._request(
            user_id, "GET", f"{SVC_GL_ACCOUNT}/A_GLAccountInChartOfAccounts",
            params={
                "$expand": "to_Text",
                "$filter": f"ChartOfAccounts eq '{settings.SAP_CHART_OF_ACCOUNTS}'",
                "$top": 2000,
                "$format": "json",
            },
        )
        return [self._account_card(r) for r in self._rows(data)]

    async def _list_bp_by_role_prefix(self, user_id: str, role_prefix: str, top: int = 1000) -> List[Dict[str, Any]]:
        data = await self._request(
            user_id, "GET", f"{SVC_BUSINESS_PARTNER}/A_BusinessPartner",
            params={"$expand": "to_BusinessPartnerRole", "$top": top, "$format": "json"},
        )
        out: List[Dict[str, Any]] = []
        for b in self._rows(data):
            roles = self._nav_rows(b.get("to_BusinessPartnerRole"))
            if any((r.get("BusinessPartnerRole") or "").startswith(role_prefix) for r in roles):
                out.append(self._bp_card(b))
        return out

    async def list_customers(self, user_id: str, company_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self._list_bp_by_role_prefix(user_id, BP_ROLE_CUSTOMER_PREFIX)

    async def list_vendors(self, user_id: str, company_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self._list_bp_by_role_prefix(user_id, BP_ROLE_SUPPLIER_PREFIX)

    async def list_items(self, user_id: str, company_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """SAP FI postings reference GL accounts, not an item/product master, so
        there is no Zoho-style "items" list in the journal-entry flow. Returns []
        so the shared pipeline (which treats line items as GL-account matches)
        degrades cleanly. (To expose product masters, wire API_PRODUCT_SRV here.)"""
        return []

    async def list_bank_accounts(self, user_id: str, company_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """Bank/cash GL accounts. There is no dedicated bank flag on the COA entity;
        we surface balance-sheet accounts and tag likely bank/cash ones by
        GLAccountType. VERIFY the exact 'Cash Account' GLAccountType code/group
        against your tenant and tighten the filter as needed."""
        accounts = await self.list_accounts(user_id, company_code)
        bank = [a for a in accounts if (a.get("account_type") or "").upper() in ("CASH", "D")
                or "bank" in (a.get("name") or "").lower()
                or "cash" in (a.get("name") or "").lower()]
        return bank or accounts

    # ---------------- transactional reads (open items, reports) ----------------

    async def list_open_items(
        self, user_id: str, company_code: str, *, financial_account_type: str, top: int = 500,
    ) -> List[Dict[str, Any]]:
        """Open A/P (K) or A/R (D) items for bank matching / reconciliation."""
        flt = (f"CompanyCode eq '{company_code}' and IsCleared eq false "
               f"and FinancialAccountType eq '{financial_account_type}'")
        data = await self._request(
            user_id, "GET", f"{SVC_OPEN_ITEMS}/A_OperationalAcctgDocItemCube",
            params={"$filter": flt, "$top": top, "$format": "json"},
        )
        return self._rows(data)

    async def list_gl_line_items(
        self, user_id: str, company_code: str, *, gl_account: Optional[str] = None,
        date_from: Optional[str] = None, date_to: Optional[str] = None, top: int = 1000,
    ) -> List[Dict[str, Any]]:
        """GL (FinancialAccountType 'S') line items from the operational cube, for
        bank-account statement views. Unlike list_open_items this is NOT gated on
        IsCleared, so cleared bank movements are included. VERIFY the PostingDate
        $filter literal + field names against your tenant's $metadata."""
        clauses = [f"CompanyCode eq '{company_code}'", f"FinancialAccountType eq '{FIN_ACCT_TYPE_GL}'"]
        if gl_account:
            clauses.append(f"GLAccount eq '{gl_account}'")
        if date_from:
            clauses.append(f"PostingDate ge datetime'{date_from[:10]}T00:00:00'")
        if date_to:
            clauses.append(f"PostingDate le datetime'{date_to[:10]}T00:00:00'")
        data = await self._request(
            user_id, "GET", f"{SVC_OPEN_ITEMS}/A_OperationalAcctgDocItemCube",
            params={"$filter": " and ".join(clauses), "$top": top, "$format": "json"},
        )
        return self._rows(data)

    async def list_bills(self, user_id: str, company_code: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Open supplier (A/P) items — analogue of zoho list_bills."""
        return await self.list_open_items(user_id, company_code, financial_account_type=FIN_ACCT_TYPE_SUPPLIER)

    async def list_invoices(self, user_id: str, company_code: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Open customer (A/R) items — analogue of zoho list_invoices."""
        return await self.list_open_items(user_id, company_code, financial_account_type=FIN_ACCT_TYPE_CUSTOMER)

    async def get_trial_balance(
        self, user_id: str, company_code: str, from_date: str, to_date: str,
    ) -> List[Dict[str, Any]]:
        """Period balances per GL account from the C_TRIALBALANCE parameterized CDS."""
        fd = from_date[:10]
        td = to_date[:10]
        path = (f"{SVC_TRIAL_BALANCE}/C_TRIALBALANCE("
                f"P_FromPostingDate=datetime'{fd}T00:00:00',"
                f"P_ToPostingDate=datetime'{td}T00:00:00')/Results")
        data = await self._request(
            user_id, "GET", path,
            params={
                "$filter": f"Ledger eq '{settings.SAP_LEDGER}' and CompanyCode eq '{company_code}'",
                "$format": "json",
            },
        )
        return self._rows(data)

    async def list_companies(self, user_id: str) -> List[Dict[str, Any]]:
        """Company codes available to write to. There is no simple released master
        read here; we surface the configured default (and, if connected, the stored
        one). Extend via the company-code CDS if a picker is needed."""
        codes = []
        if settings.SAP_DEFAULT_COMPANY_CODE:
            codes.append(settings.SAP_DEFAULT_COMPANY_CODE)
        tok = load_tokens(user_id)
        if tok and tok.get("company_code") and tok["company_code"] not in codes:
            codes.append(tok["company_code"])
        return [{"company_code": c, "name": c} for c in codes]

    async def ping(self, user_id: str) -> bool:
        """Cheap GET to validate token + API reachability (check-connection)."""
        await self._request(
            user_id, "GET", f"{SVC_GL_ACCOUNT}/A_GLAccountInChartOfAccounts",
            params={"$top": 1, "$format": "json"},
        )
        return True

    # ---------------- master writes (OData, CSRF) ----------------

    async def create_customer(self, user_id: str, body: Dict[str, Any], company_code: Optional[str] = None) -> Dict[str, Any]:
        """Create a Business Partner with customer roles. VERIFY: a posting-ready
        customer also needs the company-code facet (to_Customer/to_CustomerCompany);
        depending on tenant config that may require a follow-up deep insert."""
        name = body.get("name") or body.get("OrganizationBPName1") or ""
        payload = {
            "BusinessPartnerCategory": body.get("category", "2"),
            "OrganizationBPName1": name,
            "SearchTerm1": (name or "")[:20],
            "CorrespondenceLanguage": settings.SAP_LANGUAGE,
            "to_BusinessPartnerRole": [{"BusinessPartnerRole": "FLCU00"}, {"BusinessPartnerRole": "FLCU01"}],
        }
        data = await self._request(user_id, "POST", f"{SVC_BUSINESS_PARTNER}/A_BusinessPartner",
                                   json_body=self._clean_body(payload))
        return self._entity(data)

    async def create_vendor(self, user_id: str, body: Dict[str, Any], company_code: Optional[str] = None) -> Dict[str, Any]:
        """Create a Business Partner with supplier roles. VERIFY: company-code facet
        (to_Supplier/to_SupplierCompany) may be required for posting-ready vendors."""
        name = body.get("name") or body.get("OrganizationBPName1") or ""
        payload = {
            "BusinessPartnerCategory": body.get("category", "2"),
            "OrganizationBPName1": name,
            "SearchTerm1": (name or "")[:20],
            "CorrespondenceLanguage": settings.SAP_LANGUAGE,
            "to_BusinessPartnerRole": [{"BusinessPartnerRole": "FLVN00"}, {"BusinessPartnerRole": "FLVN01"}],
        }
        data = await self._request(user_id, "POST", f"{SVC_BUSINESS_PARTNER}/A_BusinessPartner",
                                   json_body=self._clean_body(payload))
        return self._entity(data)

    async def create_account(self, user_id: str, body: Dict[str, Any], company_code: Optional[str] = None) -> Dict[str, Any]:
        """Not supported: API_GLACCOUNTINCHARTOFACCOUNTS_SRV is read-only and
        S/4HANA Cloud Public Edition has no anonymous GL-master *create* API.
        Create GL accounts in the Manage G/L Account Master Data Fiori app."""
        raise HTTPException(
            status_code=501,
            detail="GL account creation is not available via API in S/4HANA Cloud Public — create it in Fiori (Manage G/L Account Master Data).",
        )

    async def create_item(self, user_id: str, body: Dict[str, Any], company_code: Optional[str] = None) -> Dict[str, Any]:
        """Not supported in the FI journal-entry flow (no item master is used)."""
        raise HTTPException(status_code=501, detail="SAP item/product creation is not part of the FI posting flow.")

    # ---------------- supplier invoice (A/P) — OData write ----------------

    async def post_supplier_invoice(self, user_id: str, doc: Dict[str, Any], company_code: str) -> Dict[str, Any]:
        """Post an A/P supplier invoice via API_SUPPLIERINVOICE_PROCESS_SRV.

        `doc` shape (built by agents/sap/purchase.py):
          {
            "invoice_id": "INV-123", "invoicing_party": "<supplier BP id>",
            "document_date": "YYYY-MM-DD", "posting_date": "YYYY-MM-DD",
            "currency": "INR", "gross_amount": 119.00,
            "items": [{"gl_account": "..", "amount": 100.00, "dc": "S",
                       "tax_code": "..", "text": ".."}],
            "tax_items": [{"item": 1, "tax_code": "..", "amount": 19.00}]  # optional
          }
        InvoiceGrossAmount must equal net + tax or SAP rejects the post.
        """
        doc = self._clean_body(doc)
        # Fail fast with a clean 400 instead of a KeyError at body-build time.
        for k in ("invoicing_party", "document_date", "posting_date", "gross_amount"):
            if doc.get(k) in (None, ""):
                raise HTTPException(status_code=400, detail=f"supplier invoice missing required field: {k}")
        items = doc.get("items") or []
        if not items:
            raise HTTPException(status_code=400, detail="supplier invoice has no line items")
        cur = doc.get("currency", "INR")
        gl_items = []
        for i, it in enumerate(items):
            if not it.get("gl_account"):
                raise HTTPException(status_code=400, detail=f"supplier invoice line {i + 1} has no GL account")
            gl_items.append({
                "SupplierInvoiceItem": str(i + 1),
                "CompanyCode": company_code,
                "GLAccount": it["gl_account"],
                "DocumentCurrency": cur,
                "SupplierInvoiceItemAmount": str(it["amount"]),
                "DebitCreditCode": it.get("dc", "S"),
                **({"TaxCode": it["tax_code"]} if it.get("tax_code") else {}),
                **({"DocumentItemText": it["text"]} if it.get("text") else {}),
            })
        body: Dict[str, Any] = {
            "CompanyCode": company_code,
            "DocumentDate": _odata_v2_date(doc["document_date"]),
            "PostingDate": _odata_v2_date(doc["posting_date"]),
            "SupplierInvoiceIDByInvcgParty": doc.get("invoice_id"),
            "InvoicingParty": doc["invoicing_party"],
            "DocumentCurrency": cur,
            "InvoiceGrossAmount": str(doc["gross_amount"]),
            "to_SupplierInvoiceItemGLAcct": {"results": gl_items},
        }
        if doc.get("tax_items"):
            tax_rows = []
            for t in doc["tax_items"]:
                if not t.get("tax_code"):
                    # SAP rejects a tax line with a null TaxCode — surface it clearly
                    # (the purchase pipeline flags this for review before posting).
                    raise HTTPException(status_code=400, detail="supplier invoice tax line missing tax_code")
                tax_rows.append({"SupplierInvoiceItem": str(t.get("item", 1)), "TaxCode": t["tax_code"], "TaxAmount": str(t["amount"])})
            body["to_SupplierInvoiceTax"] = {"results": tax_rows}
        data = await self._request(user_id, "POST", f"{SVC_SUPPLIER_INVOICE}/A_SupplierInvoice", json_body=body)
        return self._entity(data)

    # ---------------- journal entry / customer invoice (SOAP A2X) ----------------

    async def post_journal_entry(self, user_id: str, doc: Dict[str, Any], company_code: str) -> Dict[str, Any]:
        """Post a GL journal entry (and A/R customer invoices) via the SOAP A2X
        'Journal Entry - Post' synchronous service. OData cannot post JEs in
        S/4HANA Cloud Public Edition.

        `doc` shape (built by agents/sap/sales.py & bank_entries.py):
          {
            "document_date": "YYYY-MM-DD", "posting_date": "YYYY-MM-DD",
            "doc_type": "SA"|"DR", "currency": "INR",
            "header_text": "...", "reference": "EXT-INV-0001",
            "gl_items":      [{"gl_account": "..", "amount": 1000.00, "dc": "S"|"H", "text": ".."}],
            "debtor_items":  [{"customer": "<BP id>", "amount": 1000.00, "dc": "S", "text": ".."}],
            "creditor_items":[{"supplier": "<BP id>", "amount": 1000.00, "dc": "H", "text": ".."}]
          }
        Debits must equal credits; DebitCreditCode S=debit/H=credit with positive amounts.
        """
        if not settings.SAP_SOAP_JOURNAL_ENTRY_URL:
            raise HTTPException(
                status_code=501,
                detail="SAP_SOAP_JOURNAL_ENTRY_URL not configured — journal-entry posting is SOAP A2X "
                       "(JournalEntryCreateRequestConfirmation_In, comm scenario SAP_COM_0002).",
            )
        self._validate_journal_entry(doc, company_code)
        xml = self._build_je_soap(doc, company_code)
        return await self._soap_post(user_id, settings.SAP_SOAP_JOURNAL_ENTRY_URL, xml)

    def _validate_journal_entry(self, doc: Dict[str, Any], company_code: str) -> None:
        """Fail fast with a clean 400 (not a TypeError/KeyError at XML-build time)
        and guarantee the document balances before posting."""
        if not company_code:
            raise HTTPException(status_code=400, detail="journal entry missing company_code")
        if not doc.get("document_date") or not doc.get("posting_date"):
            raise HTTPException(status_code=400, detail="journal entry missing document_date / posting_date")
        gl = doc.get("gl_items") or []
        deb = doc.get("debtor_items") or []
        cre = doc.get("creditor_items") or []
        if not (gl or deb or cre):
            raise HTTPException(status_code=400, detail="journal entry has no line items")
        for it in gl:
            if not it.get("gl_account"):
                raise HTTPException(status_code=400, detail="journal entry GL line missing gl_account")
        for it in deb:
            if not it.get("customer"):
                raise HTTPException(status_code=400, detail="journal entry debtor line missing customer")
        for it in cre:
            if not it.get("supplier"):
                raise HTTPException(status_code=400, detail="journal entry creditor line missing supplier")
        debit = credit = 0.0
        for it in (*gl, *deb, *cre):
            try:
                amount = abs(float(it.get("amount") or 0))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"journal entry line has non-numeric amount: {it.get('amount')!r}")
            if (it.get("dc") or "S").upper() == "H":
                credit += amount
            else:
                debit += amount
        if abs(debit - credit) > 0.01:
            raise HTTPException(
                status_code=400,
                detail=f"journal entry does not balance (debit {debit:.2f} != credit {credit:.2f})",
            )

    def _build_je_soap(self, doc: Dict[str, Any], company_code: str) -> str:
        """Build the JournalEntryBulkCreateRequest SOAP envelope. VERIFY element
        namespace/order against the live WSDL — A2X message shapes vary by release."""
        msg_id = uuid4().hex[:30].upper()
        now_iso = _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = doc.get("currency", "INR")
        doc_type = doc.get("doc_type", JE_DOC_TYPE_GENERAL)

        def amt(v: Any) -> str:
            return _xml_escape(str(v))

        items_xml: List[str] = []
        idx = 0
        for it in doc.get("gl_items", []):
            idx += 1
            items_xml.append(
                f"<Item><ReferenceDocumentItem>{idx}</ReferenceDocumentItem>"
                f"<GLAccount>{_xml_escape(str(it['gl_account']))}</GLAccount>"
                f"<AmountInTransactionCurrency currencyCode=\"{_xml_escape(cur)}\">{amt(it['amount'])}</AmountInTransactionCurrency>"
                f"<DebitCreditCode>{_xml_escape(it.get('dc', 'S'))}</DebitCreditCode>"
                + (f"<DocumentItemText>{_xml_escape(it['text'])}</DocumentItemText>" if it.get("text") else "")
                + "</Item>"
            )
        for it in doc.get("debtor_items", []):
            idx += 1
            items_xml.append(
                f"<DebtorItem><ReferenceDocumentItem>{idx}</ReferenceDocumentItem>"
                f"<Customer>{_xml_escape(str(it['customer']))}</Customer>"
                f"<AmountInTransactionCurrency currencyCode=\"{_xml_escape(cur)}\">{amt(it['amount'])}</AmountInTransactionCurrency>"
                f"<DebitCreditCode>{_xml_escape(it.get('dc', 'S'))}</DebitCreditCode>"
                + (f"<DocumentItemText>{_xml_escape(it['text'])}</DocumentItemText>" if it.get("text") else "")
                + "</DebtorItem>"
            )
        for it in doc.get("creditor_items", []):
            idx += 1
            items_xml.append(
                f"<CreditorItem><ReferenceDocumentItem>{idx}</ReferenceDocumentItem>"
                f"<Supplier>{_xml_escape(str(it['supplier']))}</Supplier>"
                f"<AmountInTransactionCurrency currencyCode=\"{_xml_escape(cur)}\">{amt(it['amount'])}</AmountInTransactionCurrency>"
                f"<DebitCreditCode>{_xml_escape(it.get('dc', 'H'))}</DebitCreditCode>"
                + (f"<DocumentItemText>{_xml_escape(it['text'])}</DocumentItemText>" if it.get("text") else "")
                + "</CreditorItem>"
            )

        header_text = doc.get("header_text", "")
        reference = doc.get("reference", msg_id)
        return (
            f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}" xmlns:glob="{JE_MSG_NS}">'
            "<soapenv:Header/><soapenv:Body>"
            "<glob:JournalEntryBulkCreateRequest>"
            f"<MessageHeader><ID>{msg_id}</ID><CreationDateTime>{now_iso}</CreationDateTime></MessageHeader>"
            "<JournalEntryCreateRequest>"
            f"<MessageHeader><ID>{msg_id}</ID><CreationDateTime>{now_iso}</CreationDateTime></MessageHeader>"
            "<JournalEntry>"
            f"<OriginalReferenceDocumentType>{JE_REF_DOC_TYPE}</OriginalReferenceDocumentType>"
            f"<OriginalReferenceDocument>{_xml_escape(str(reference))}</OriginalReferenceDocument>"
            f"<BusinessTransactionType>{JE_BUSINESS_TXN_TYPE}</BusinessTransactionType>"
            f"<AccountingDocumentType>{_xml_escape(doc_type)}</AccountingDocumentType>"
            f"<CompanyCode>{_xml_escape(company_code)}</CompanyCode>"
            f"<DocumentDate>{_xml_escape((doc.get('document_date') or '')[:10])}</DocumentDate>"
            f"<PostingDate>{_xml_escape((doc.get('posting_date') or '')[:10])}</PostingDate>"
            + (f"<DocumentHeaderText>{_xml_escape(header_text)}</DocumentHeaderText>" if header_text else "")
            + "".join(items_xml)
            + "</JournalEntry></JournalEntryCreateRequest>"
            "</glob:JournalEntryBulkCreateRequest>"
            "</soapenv:Body></soapenv:Envelope>"
        )

    async def _soap_post(self, user_id: str, url: str, xml: str, soap_action: str = "") -> Dict[str, Any]:
        """POST a SOAP envelope. Auth: Basic if SAP_SOAP_USER is set, else reuse the
        OData OAuth bearer (works when the comm arrangement is OAuth-based)."""
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": soap_action}
        auth = None
        if settings.SAP_SOAP_USER:
            auth = (settings.SAP_SOAP_USER, settings.SAP_SOAP_PASSWORD)
        else:
            token, _ = await ensure_access_token(user_id)
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, content=xml.encode("utf-8"), headers=headers, auth=auth)
            if resp.status_code >= 400:
                logger.error(f"SAP SOAP {url} -> {resp.status_code}: {resp.text[:800]}")
                raise HTTPException(status_code=resp.status_code, detail={"sap_soap_error": resp.text[:1000]})
            return self._parse_je_confirmation(resp.text)

    @staticmethod
    def _parse_je_confirmation(text: str) -> Dict[str, Any]:
        """Pull the created document number + any log messages from the SOAP
        confirmation. Namespace-agnostic (matches by local element name)."""
        result: Dict[str, Any] = {"raw_excerpt": text[:600]}
        try:
            root = ET.fromstring(text)
        except Exception:  # noqa: BLE001
            return result

        def local(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        messages: List[str] = []
        for el in root.iter():
            ln = local(el.tag)
            val = (el.text or "").strip()
            if not val:
                continue
            if ln == "AccountingDocument":
                result["accounting_document"] = val
            elif ln == "FiscalYear":
                result["fiscal_year"] = val
            elif ln == "CompanyCode":
                result.setdefault("company_code", val)
            elif ln in ("SeverityCode", "Note", "faultstring", "TypeID"):
                messages.append(f"{ln}={val}")
        if messages:
            result["messages"] = messages
        result["posted"] = "accounting_document" in result
        return result


# ======================================================================
# Singleton
# ======================================================================

sap_connector = SAPConnector()
