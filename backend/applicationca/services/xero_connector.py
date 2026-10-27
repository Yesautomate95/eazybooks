"""
Xero connector — OAuth2 + REST (Accounting API).

Mirrors the shape of zoho_connector but for Xero. Key differences:

  * OAuth client auth uses an HTTP Basic header (base64(client_id:client_secret))
    with the grant params in the form body.
  * Refresh tokens ROTATE — every refresh returns a NEW refresh_token that MUST
    be persisted (Xero invalidates the old one). Access tokens last 30 min;
    refresh tokens expire after 60 days of inactivity.
  * Tokens are TENANT-AGNOSTIC: one token set covers every Xero organisation the
    user authorized. The list of tenants comes from GET /connections (live), and
    the active tenant is sent as the ``Xero-tenant-id`` header on every call.
  * JSON is PascalCase and collections are wrapped: {"Contacts":[...]},
    {"Invoices":[...]}, {"Accounts":[...]}, etc. ``Accept: application/json`` is
    required (otherwise Xero returns XML).
  * CRITICAL: invoice / bill / bank-transaction LINE ITEMS reference accounts and
    items by CODE (``AccountCode`` / ``ItemCode``), NOT by GUID. Only the Contact
    is referenced by ``ContactID``. So our normalized account/item cards carry a
    ``code`` field, and the pipelines build line items from the matched card's
    code.

Auth:  https://login.xero.com/identity/connect/authorize
       https://identity.xero.com/connect/token
       https://identity.xero.com/connect/revocation
API:   https://api.xero.com/api.xro/2.0/...   (tenant via Xero-tenant-id header)
Conn:  https://api.xero.com/connections

Token storage: postgres ``xero_tokens`` (one row per user_id).
"""

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from config.settings import settings

logger = logging.getLogger(__name__)


AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
REVOKE_URL = "https://identity.xero.com/connect/revocation"
CONNECTIONS_URL = "https://api.xero.com/connections"
API_BASE = "https://api.xero.com/api.xro/2.0"

REFRESH_LEEWAY_SECONDS = 60

# Display-only keys our pipelines attach to create payloads (stripped before
# POSTing — Xero rejects unknown fields). We strip any underscore-prefixed key
# plus this explicit denylist, on a copy (callers keep the full payload for
# history/dedup recording).
_DISPLAY_TOP_KEYS = ("customer_name", "vendor_name", "bank_description")


def _strip_display(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _strip_display(v)
            for k, v in obj.items()
            if not (isinstance(k, str) and (k.startswith("_") or k in _DISPLAY_TOP_KEYS))
        }
    if isinstance(obj, list):
        return [_strip_display(v) for v in obj]
    return obj


def _basic_auth_header() -> str:
    raw = f"{settings.XERO_CLIENT_ID}:{settings.XERO_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


# ======================================================================
# Token storage (postgres xero_tokens) — keyed by user_id only.
# ======================================================================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_tokens(user_id: str, access_token: str, refresh_token: str, expires_in: int) -> None:
    """Upsert tokens for a user. Xero rotates the refresh_token on every refresh,
    so we always overwrite it."""
    expires_at = _utcnow() + timedelta(seconds=int(expires_in or 1800))
    conn = get_connection()
    if conn is None:
        logger.error("xero_tokens save skipped — DB unavailable")
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO xero_tokens (user_id, access_token, refresh_token, expires_at, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at    = EXCLUDED.expires_at,
                updated_at    = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, expires_at),
        )
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)


def load_tokens(user_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM xero_tokens WHERE user_id=%s", (user_id,))
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
        cur.execute("DELETE FROM xero_tokens WHERE user_id=%s", (user_id,))
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)


def is_user_connected(user_id: str) -> bool:
    return load_tokens(user_id) is not None


# ======================================================================
# OAuth2 flow primitives
# ======================================================================

def build_authorize_url(state: str, scope: Optional[str] = None) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.XERO_CLIENT_ID,
        "redirect_uri": settings.XERO_REDIRECT_URI,
        "scope": scope or settings.XERO_SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.XERO_REDIRECT_URI,
            },
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def revoke_token(refresh_token: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            REVOKE_URL,
            data={"token": refresh_token},
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )


async def ensure_access_token(user_id: str) -> str:
    """Return a valid access token, refreshing (and persisting the rotated
    refresh_token) when within REFRESH_LEEWAY_SECONDS of expiry."""
    tokens = load_tokens(user_id)
    if not tokens:
        raise RuntimeError("Xero is not connected for this user")

    expires_at = tokens["expires_at"]
    if expires_at and (expires_at - _utcnow()).total_seconds() < REFRESH_LEEWAY_SECONDS:
        logger.info(f"Refreshing Xero access token for user {user_id}")
        refreshed = await refresh_access_token(tokens["refresh_token"])
        # Xero ROTATES the refresh token — persist the new one or the next
        # refresh fails.
        save_tokens(
            user_id=user_id,
            access_token=refreshed["access_token"],
            refresh_token=refreshed["refresh_token"],
            expires_in=refreshed.get("expires_in", 1800),
        )
        return refreshed["access_token"]

    return tokens["access_token"]


async def list_connections(user_id: str) -> List[Dict[str, Any]]:
    """GET /connections — the tenants (organisations) this user authorized."""
    access_token = await ensure_access_token(user_id)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            CONNECTIONS_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json() or []


# ======================================================================
# REST client
# ======================================================================

class XeroConnector:
    """Async REST client for the Xero Accounting API.

    All public methods take a `user_id` and a `tenant_id` (the active Xero org;
    sent as the Xero-tenant-id header). List methods return normalized cards
    (with a `code` for accounts/items) so downstream matcher + pipeline code is
    the same shape as the Zoho/QBO paths.
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    async def _request(
        self,
        user_id: str,
        method: str,
        path: str,
        *,
        tenant_id: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        access_token = await ensure_access_token(user_id)
        url = f"{API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Xero-tenant-id": tenant_id,
            "Accept": "application/json",
        }
        body = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = _strip_display(json_body)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(method, url, params=params, json=body, headers=headers)
            if resp.status_code >= 400:
                logger.error(f"Xero {method} {path} -> {resp.status_code}: {resp.text}")
                detail: Any
                try:
                    err = resp.json()
                    detail = {
                        "xero_error": err.get("Message") or err.get("Detail") or err.get("error"),
                        "xero_detail": err.get("Elements") or err.get("ErrorNumber"),
                    }
                except Exception:
                    detail = resp.text or f"Xero {method} {path} failed"
                raise HTTPException(status_code=resp.status_code, detail=detail)
            if not resp.headers.get("content-type", "").startswith("application/json"):
                return {"raw": resp.text}
            return resp.json()

    async def _paged_list(
        self,
        user_id: str,
        path: str,
        collection_key: str,
        *,
        tenant_id: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """Walk Xero ?page=N pagination (100 records/page) and return the full
        collection. Endpoints that don't paginate just return page 1."""
        out: List[Dict[str, Any]] = []
        page = 1
        base_params = dict(params or {})
        paginated = "page" in base_params or path in ("/Contacts", "/Invoices", "/BankTransactions")
        while page <= max_pages:
            merged = dict(base_params)
            if paginated:
                merged["page"] = page
            data = await self._request(user_id, "GET", path, tenant_id=tenant_id, params=merged)
            rows = data.get(collection_key, []) or []
            out.extend(rows)
            if not paginated or len(rows) < 100:
                break
            page += 1
        return out

    # --- organisation ---

    async def get_company_info(self, user_id: str, tenant_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "GET", "/Organisation", tenant_id=tenant_id)
        orgs = data.get("Organisations") or []
        return orgs[0] if orgs else data

    # --- normalized cards ---

    @staticmethod
    def _contact_card(c: Dict[str, Any]) -> Dict[str, Any]:
        bal = (c.get("Balances") or {})
        ar = ((bal.get("AccountsReceivable") or {}).get("Outstanding"))
        ap = ((bal.get("AccountsPayable") or {}).get("Outstanding"))
        return {
            "list_id": c.get("ContactID"),
            "name": c.get("Name"),
            "company": c.get("Name"),
            "email": c.get("EmailAddress"),
            "balance": ar or ap or 0,
            "is_customer": c.get("IsCustomer"),
            "is_supplier": c.get("IsSupplier"),
            "raw": c,
        }

    @staticmethod
    def _account_card(a: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "list_id": a.get("AccountID"),
            "code": a.get("Code"),                 # line items reference this
            "name": a.get("Name"),
            "account_type": a.get("Type"),
            "account_class": a.get("Class"),
            "balance": 0,
            "raw": a,
        }

    @staticmethod
    def _item_card(i: Dict[str, Any]) -> Dict[str, Any]:
        sales = i.get("SalesDetails") or {}
        return {
            "list_id": i.get("ItemID"),
            "code": i.get("Code"),                 # line items reference this
            "name": i.get("Name"),
            "rate": sales.get("UnitPrice"),
            "raw": i,
        }

    # --- master reads ---

    async def list_contacts(self, user_id: str, tenant_id: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        rows = await self._paged_list(user_id, "/Contacts", "Contacts", tenant_id=tenant_id, params=params)
        return [self._contact_card(c) for c in rows]

    async def list_customers(self, user_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        # Return all contacts — IsCustomer/IsSupplier flags are only set once a
        # contact has been used in a transaction, so filtering would hide freshly
        # created contacts. Name matching works across the full set.
        return await self.list_contacts(user_id, tenant_id)

    async def list_vendors(self, user_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        return await self.list_contacts(user_id, tenant_id)

    async def list_accounts(self, user_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        data = await self._request(user_id, "GET", "/Accounts", tenant_id=tenant_id)
        return [self._account_card(a) for a in (data.get("Accounts") or [])]

    async def list_items(self, user_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        data = await self._request(user_id, "GET", "/Items", tenant_id=tenant_id)
        return [self._item_card(i) for i in (data.get("Items") or [])]

    async def list_bank_accounts(self, user_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        cards = await self.list_accounts(user_id, tenant_id)
        return [c for c in cards if (c.get("account_type") or "").upper() == "BANK"]

    # --- master writes ---

    async def create_customer(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "POST", "/Contacts", tenant_id=tenant_id, json_body={"Contacts": [body]})
        out = data.get("Contacts") or []
        return out[0] if out else data

    create_vendor = create_customer  # Xero contacts are unified

    async def create_account(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        # Xero uses PUT to create an account.
        data = await self._request(user_id, "PUT", "/Accounts", tenant_id=tenant_id, json_body={"Accounts": [body]})
        out = data.get("Accounts") or []
        return out[0] if out else data

    async def create_item(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "POST", "/Items", tenant_id=tenant_id, json_body={"Items": [body]})
        out = data.get("Items") or []
        return out[0] if out else data

    # --- transactions ---

    async def create_invoice(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        """Create a sales invoice (Type=ACCREC) or bill (Type=ACCPAY)."""
        data = await self._request(user_id, "POST", "/Invoices", tenant_id=tenant_id, json_body={"Invoices": [body]})
        out = data.get("Invoices") or []
        return out[0] if out else data

    async def create_bill(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        body = {**body, "Type": "ACCPAY"}
        return await self.create_invoice(user_id, body, tenant_id)

    async def create_bank_transaction(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "POST", "/BankTransactions", tenant_id=tenant_id, json_body={"BankTransactions": [body]})
        out = data.get("BankTransactions") or []
        return out[0] if out else data

    async def create_payment(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "PUT", "/Payments", tenant_id=tenant_id, json_body={"Payments": [body]})
        out = data.get("Payments") or []
        return out[0] if out else data

    async def create_bank_transfer(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        """Move money between two bank accounts (the Xero analog of a Tally
        Contra / QBO Transfer). Body needs FromBankAccount.AccountID,
        ToBankAccount.AccountID and Amount."""
        data = await self._request(user_id, "PUT", "/BankTransfers", tenant_id=tenant_id, json_body={"BankTransfers": [body]})
        out = data.get("BankTransfers") or []
        return out[0] if out else data

    async def create_manual_journal(self, user_id: str, body: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        """Post a manual journal (the Xero analog of a Tally Journal / QBO
        JournalEntry). Body needs a JournalLines list of balanced lines whose
        LineAmount is positive (Debit) or negative (Credit), each referencing an
        AccountCode."""
        data = await self._request(user_id, "POST", "/ManualJournals", tenant_id=tenant_id, json_body={"ManualJournals": [body]})
        out = data.get("ManualJournals") or []
        return out[0] if out else data

    # --- queries used by chat / duplicate-check ---

    async def list_invoices(self, user_id: str, tenant_id: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return await self._paged_list(user_id, "/Invoices", "Invoices", tenant_id=tenant_id, params=params, max_pages=5)

    async def list_bank_transactions(self, user_id: str, tenant_id: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return await self._paged_list(user_id, "/BankTransactions", "BankTransactions", tenant_id=tenant_id, params=params, max_pages=10)

    async def get_report(self, user_id: str, report_name: str, tenant_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Fetch a Xero report (ProfitAndLoss, BalanceSheet, TrialBalance, etc.)."""
        return await self._request(user_id, "GET", f"/Reports/{report_name}", tenant_id=tenant_id, params=params)

    async def list_tax_rates(self, user_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        """List the org's tax rates (GET /TaxRates). Each carries a ``TaxType``
        string that line items reference, plus CanApplyToRevenue /
        CanApplyToExpenses flags and a Status."""
        data = await self._request(user_id, "GET", "/TaxRates", tenant_id=tenant_id)
        return data.get("TaxRates") or []


# ======================================================================
# Singleton
# ======================================================================

xero_connector = XeroConnector()
