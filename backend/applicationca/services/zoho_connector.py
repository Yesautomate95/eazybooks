"""
Zoho Books connector — OAuth2 + REST.

Mirrors the shape of qbo_connector but for Zoho Books. Key differences from QBO:

  * Auth header is ``Authorization: Zoho-oauthtoken <token>`` (NOT ``Bearer``).
  * The token endpoint takes client_id/client_secret in the request BODY
    (not HTTP Basic auth).
  * Zoho is data-center specific. The token response carries an ``api_domain``
    (e.g. https://www.zohoapis.com) which authoritatively decides the API host;
    we persist it per user.
  * Tokens are ORG-AGNOSTIC: one Zoho token works for every organization the
    user owns. So tokens are keyed by user_id only, and the active company is
    selected per request via the ``organization_id`` query param.
  * Refresh tokens are long-lived and do NOT rotate; access tokens last ~1h.
  * Every response is wrapped as ``{"code": 0, "message": "success", <resource>: ...}``
    — a non-zero ``code`` is an API error.

Auth:  https://accounts.zoho.{dc}/oauth/v2/{auth,token,token/revoke}
API:   {api_domain}/books/v3/...        (api_domain from the token response)

Token storage: postgres ``zoho_tokens`` (one row per user_id).
"""

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


# ----------------------------------------------------------------------
# Data-center hosts (verified against the Zoho Books v3 OAuth docs).
# api_domain from the token response overrides _DC_API at runtime, so these are
# only the fallback / the seed for the initial authorize redirect.
# ----------------------------------------------------------------------

_DC_ACCOUNTS = {
    "com": "https://accounts.zoho.com",
    "in": "https://accounts.zoho.in",
    "eu": "https://accounts.zoho.eu",
    "com.au": "https://accounts.zoho.com.au",
    "jp": "https://accounts.zoho.jp",
    "ca": "https://accounts.zoho.ca",
    "sa": "https://accounts.zoho.sa",
}

_DC_API = {
    "com": "https://www.zohoapis.com",
    "in": "https://www.zohoapis.in",
    "eu": "https://www.zohoapis.eu",
    "com.au": "https://www.zohoapis.com.au",
    "jp": "https://www.zohoapis.jp",
    "ca": "https://www.zohoapis.ca",
    "sa": "https://www.zohoapis.sa",
}

REFRESH_LEEWAY_SECONDS = 60

# Display-only keys our pipelines attach to create payloads (so the UI can show
# names and history can record them). They are stripped before POSTing to Zoho,
# which expects flat ids only. Stripping happens on a copy — callers keep the
# full payload for history/dedup recording.
_DISPLAY_TOP_KEYS = (
    "customer_name", "vendor_name", "account_name", "bank_description", "_meta",
    "from_account_name", "to_account_name", "paid_through_account_name",
)
_DISPLAY_LINE_KEYS = ("account_name", "item_name", "tax_name")


def _clean_txn_body(body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        return body
    out = {k: v for k, v in body.items() if k not in _DISPLAY_TOP_KEYS}
    if isinstance(out.get("line_items"), list):
        out["line_items"] = [
            {k: v for k, v in li.items() if k not in _DISPLAY_LINE_KEYS} if isinstance(li, dict) else li
            for li in out["line_items"]
        ]
    return out


def _accounts_host(dc: Optional[str]) -> str:
    return _DC_ACCOUNTS.get((dc or "com").lower(), _DC_ACCOUNTS["com"])


def _api_host_for_dc(dc: Optional[str]) -> str:
    return _DC_API.get((dc or "com").lower(), _DC_API["com"])


# ======================================================================
# Token storage (postgres zoho_tokens) — keyed by user_id only.
# ======================================================================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_tokens(
    user_id: str,
    access_token: str,
    refresh_token: Optional[str],
    expires_in: int,
    api_domain: Optional[str] = None,
    dc: Optional[str] = None,
) -> None:
    """Upsert tokens for a user. Zoho refresh tokens do not rotate, so on a
    refresh `refresh_token` may be None — we keep the existing one via COALESCE.
    """
    expires_at = _utcnow() + timedelta(seconds=int(expires_in or 3600))
    api_domain = api_domain or _api_host_for_dc(dc or settings.ZOHO_DC)
    dc = dc or settings.ZOHO_DC

    conn = get_connection()
    if conn is None:
        logger.error("zoho_tokens save skipped — DB unavailable")
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO zoho_tokens
                (user_id, access_token, refresh_token, expires_at, api_domain, dc, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = COALESCE(EXCLUDED.refresh_token, zoho_tokens.refresh_token),
                expires_at    = EXCLUDED.expires_at,
                api_domain    = EXCLUDED.api_domain,
                dc            = EXCLUDED.dc,
                updated_at    = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, expires_at, api_domain, dc),
        )
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)


def load_tokens(user_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        logger.warning("zoho_tokens lookup skipped — DB unavailable")
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM zoho_tokens WHERE user_id=%s", (user_id,))
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
        cur.execute("DELETE FROM zoho_tokens WHERE user_id=%s", (user_id,))
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)


# ======================================================================
# OAuth2 flow primitives
# ======================================================================

def build_authorize_url(state: str, scope: Optional[str] = None, dc: Optional[str] = None) -> str:
    """Build the Zoho consent URL the browser is redirected to.

    access_type=offline + prompt=consent are required to receive a refresh
    token on the first authorization.
    """
    params = {
        "scope": scope or settings.ZOHO_SCOPES,
        "client_id": settings.ZOHO_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.ZOHO_REDIRECT_URI,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_accounts_host(dc or settings.ZOHO_DC)}/oauth/v2/auth?{urlencode(params)}"


async def exchange_code(code: str, dc: Optional[str] = None, accounts_server: Optional[str] = None) -> Dict[str, Any]:
    """Exchange an auth code for access + refresh tokens.

    `accounts_server` (when present in the callback) overrides the configured DC
    host so we hit the correct regional token endpoint. Returns Zoho's token
    response: {access_token, refresh_token, api_domain, token_type, expires_in}.
    """
    host = accounts_server or _accounts_host(dc or settings.ZOHO_DC)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{host}/oauth/v2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.ZOHO_CLIENT_ID,
                "client_secret": settings.ZOHO_CLIENT_SECRET,
                "redirect_uri": settings.ZOHO_REDIRECT_URI,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise HTTPException(status_code=400, detail=f"Zoho token error: {data.get('error')}")
        return data


async def refresh_access_token(refresh_token: str, dc: Optional[str] = None) -> Dict[str, Any]:
    host = _accounts_host(dc or settings.ZOHO_DC)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{host}/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.ZOHO_CLIENT_ID,
                "client_secret": settings.ZOHO_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise HTTPException(status_code=400, detail=f"Zoho refresh error: {data.get('error')}")
        return data


async def revoke_token(refresh_token: str, dc: Optional[str] = None) -> None:
    """Revoke the refresh token at Zoho."""
    host = _accounts_host(dc or settings.ZOHO_DC)
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            f"{host}/oauth/v2/token/revoke",
            params={"token": refresh_token},
            headers={"Accept": "application/json"},
        )


async def ensure_access_token(user_id: str) -> Tuple[str, str]:
    """Return (access_token, api_domain). Refreshes proactively when the access
    token is within REFRESH_LEEWAY_SECONDS of expiry. Raises RuntimeError if the
    user is not connected."""
    tokens = load_tokens(user_id)
    if not tokens:
        raise RuntimeError("Zoho Books is not connected for this user")

    expires_at = tokens["expires_at"]
    if expires_at and (expires_at - _utcnow()).total_seconds() < REFRESH_LEEWAY_SECONDS:
        logger.info(f"Refreshing Zoho access token for user {user_id}")
        refreshed = await refresh_access_token(tokens["refresh_token"], tokens.get("dc"))
        # Zoho does not return a new refresh_token on refresh.
        save_tokens(
            user_id=user_id,
            access_token=refreshed["access_token"],
            refresh_token=refreshed.get("refresh_token"),  # None -> keep existing
            expires_in=refreshed.get("expires_in", 3600),
            api_domain=refreshed.get("api_domain") or tokens.get("api_domain"),
            dc=tokens.get("dc"),
        )
        return refreshed["access_token"], (refreshed.get("api_domain") or tokens["api_domain"])

    return tokens["access_token"], tokens["api_domain"]


def is_user_connected(user_id: str) -> bool:
    return load_tokens(user_id) is not None


# ======================================================================
# REST client
# ======================================================================

class ZohoConnector:
    """Async REST client for the Zoho Books API.

    All public methods take a `user_id` (Firebase UID) so we can load/refresh
    that user's tokens. `organization_id` selects the active Zoho company and is
    required for every Books call except GET /organizations.

    List methods return normalized "cards" (list_id / name / ...) so downstream
    matcher + controller code is identical in shape to the QBO path.
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    # --- low-level transport ---

    async def _request(
        self,
        user_id: str,
        method: str,
        path: str,
        *,
        organization_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        access_token, api_domain = await ensure_access_token(user_id)
        url = f"{api_domain}/books/v3{path}"
        query = dict(params or {})
        if organization_id:
            query["organization_id"] = organization_id
        headers = {
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(method, url, params=query, json=json_body, headers=headers)
            # Zoho returns 4xx for API errors with a JSON {code, message} body.
            if resp.status_code >= 400:
                logger.error(f"Zoho {method} {path} -> {resp.status_code}: {resp.text}")
                detail: Any
                try:
                    body = resp.json()
                    detail = {
                        "zoho_error": body.get("message"),
                        "zoho_code": body.get("code"),
                    }
                except Exception:
                    detail = resp.text or f"Zoho {method} {path} failed"
                raise HTTPException(status_code=resp.status_code, detail=detail)
            if not resp.headers.get("content-type", "").startswith("application/json"):
                return {"raw": resp.text}
            body = resp.json()
            # A 200 with non-zero code is still a logical error in Zoho.
            if isinstance(body, dict) and body.get("code") not in (0, None):
                logger.error(f"Zoho {method} {path} logical error: {body}")
                raise HTTPException(
                    status_code=400,
                    detail={"zoho_error": body.get("message"), "zoho_code": body.get("code")},
                )
            return body

    async def _paged_list(
        self,
        user_id: str,
        path: str,
        collection_key: str,
        *,
        organization_id: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """Walk Zoho's page_context pagination and return the full collection."""
        out: List[Dict[str, Any]] = []
        page = 1
        base_params = dict(params or {})
        while page <= max_pages:
            merged = {**base_params, "page": page, "per_page": 200}
            data = await self._request(user_id, "GET", path, organization_id=organization_id, params=merged)
            out.extend(data.get(collection_key, []) or [])
            ctx = data.get("page_context") or {}
            if not ctx.get("has_more_page"):
                break
            page += 1
        return out

    # --- organizations / company ---

    async def list_organizations(self, user_id: str) -> List[Dict[str, Any]]:
        data = await self._request(user_id, "GET", "/organizations")
        return data.get("organizations", []) or []

    async def get_company_info(self, user_id: str, organization_id: str) -> Dict[str, Any]:
        data = await self._request(
            user_id, "GET", f"/organizations/{organization_id}",
        )
        return data.get("organization", data)

    # --- master reads (return normalized cards) ---

    @staticmethod
    def _contact_card(c: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "list_id": c.get("contact_id"),
            "name": c.get("contact_name") or c.get("company_name"),
            "company": c.get("company_name"),
            "email": c.get("email"),
            "balance": c.get("outstanding_receivable_amount") or c.get("outstanding_payable_amount") or 0,
            "raw": c,
        }

    @staticmethod
    def _account_card(a: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "list_id": a.get("account_id"),
            "name": a.get("account_name"),
            "account_type": a.get("account_type"),
            "balance": a.get("balance") or a.get("current_balance") or 0,
            "raw": a,
        }

    @staticmethod
    def _item_card(i: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "list_id": i.get("item_id"),
            "name": i.get("name"),
            "rate": i.get("rate"),
            "raw": i,
        }

    async def list_customers(self, user_id: str, organization_id: str) -> List[Dict[str, Any]]:
        rows = await self._paged_list(
            user_id, "/contacts", "contacts",
            organization_id=organization_id, params={"contact_type": "customer"},
        )
        return [self._contact_card(c) for c in rows]

    async def list_vendors(self, user_id: str, organization_id: str) -> List[Dict[str, Any]]:
        rows = await self._paged_list(
            user_id, "/contacts", "contacts",
            organization_id=organization_id, params={"contact_type": "vendor"},
        )
        return [self._contact_card(c) for c in rows]

    async def list_accounts(self, user_id: str, organization_id: str) -> List[Dict[str, Any]]:
        rows = await self._paged_list(
            user_id, "/chartofaccounts", "chartofaccounts",
            organization_id=organization_id,
        )
        return [self._account_card(a) for a in rows]

    async def list_items(self, user_id: str, organization_id: str) -> List[Dict[str, Any]]:
        rows = await self._paged_list(
            user_id, "/items", "items", organization_id=organization_id,
        )
        return [self._item_card(i) for i in rows]

    async def list_bank_accounts(self, user_id: str, organization_id: str) -> List[Dict[str, Any]]:
        # VERIFY: confirm /bankaccounts endpoint + collection key 'bankaccounts'.
        data = await self._request(user_id, "GET", "/bankaccounts", organization_id=organization_id)
        rows = data.get("bankaccounts", []) or []
        return [self._account_card(a) for a in rows]

    # --- master writes ---

    async def create_customer(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        body = {**body, "contact_type": "customer"}
        data = await self._request(user_id, "POST", "/contacts", organization_id=organization_id, json_body=body)
        return data.get("contact", data)

    async def create_vendor(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        body = {**body, "contact_type": "vendor"}
        data = await self._request(user_id, "POST", "/contacts", organization_id=organization_id, json_body=body)
        return data.get("contact", data)

    async def create_account(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "POST", "/chartofaccounts", organization_id=organization_id, json_body=body)
        return data.get("chart_of_account", data)

    async def create_item(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "POST", "/items", organization_id=organization_id, json_body=body)
        return data.get("item", data)

    # --- transactions ---

    async def create_invoice(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "POST", "/invoices", organization_id=organization_id, json_body=_clean_txn_body(body))
        return data.get("invoice", data)

    async def create_bill(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        data = await self._request(user_id, "POST", "/bills", organization_id=organization_id, json_body=_clean_txn_body(body))
        return data.get("bill", data)

    async def create_bank_transaction(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        """Create a manual bank transaction such as deposit or transfer_fund.

        Module-specific money-out transactions (Expense, vendor/customer
        payments, refunds) must be posted through their own Zoho modules, not
        /banktransactions.
        """
        data = await self._request(user_id, "POST", "/banktransactions", organization_id=organization_id, json_body=_clean_txn_body(body))
        return data.get("banktransaction", data)

    async def create_expense(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        """Create a non-billable expense paid through a bank/cash account."""
        data = await self._request(user_id, "POST", "/expenses", organization_id=organization_id, json_body=_clean_txn_body(body))
        return data.get("expense", data)

    async def create_transfer(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        """Move money between two balance-sheet accounts (the Zoho analog of a
        Tally Contra / QBO Transfer). Body needs from_account_id, to_account_id,
        amount and date. Uses Zoho Books /banktransactions with
        transaction_type='transfer_fund'."""
        out = _clean_txn_body(body)
        out = {**out, "transaction_type": "transfer_fund"}
        data = await self._request(user_id, "POST", "/banktransactions", organization_id=organization_id, json_body=out)
        return data.get("banktransaction", data)

    async def create_journal(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        """Post a manual journal entry (the Zoho analog of a Tally Journal / QBO
        JournalEntry). Body needs a line_items list of balanced debit/credit rows
        ({account_id, debit_or_credit, amount}) plus journal_date.
        VERIFY: confirm '/journals' endpoint + line_items[].debit_or_credit enum
        ('debit'/'credit') against the live Zoho Books v3 docs."""
        data = await self._request(user_id, "POST", "/journals", organization_id=organization_id, json_body=_clean_txn_body(body))
        return data.get("journal", data)

    async def create_customer_payment(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        """Record a payment received against open invoices."""
        data = await self._request(user_id, "POST", "/customerpayments", organization_id=organization_id, json_body=body)
        return data.get("payment", data)

    async def create_vendor_payment(self, user_id: str, body: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        """Record a payment made against open bills."""
        data = await self._request(user_id, "POST", "/vendorpayments", organization_id=organization_id, json_body=body)
        return data.get("vendorpayment", data)

    # --- queries used by chat / duplicate-check ---

    async def list_invoices(self, user_id: str, organization_id: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return await self._paged_list(user_id, "/invoices", "invoices", organization_id=organization_id, params=params, max_pages=5)

    async def list_bills(self, user_id: str, organization_id: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return await self._paged_list(user_id, "/bills", "bills", organization_id=organization_id, params=params, max_pages=5)

    async def get_report(self, user_id: str, report_name: str, organization_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Fetch a Zoho Books report (profitandloss, balancesheet, etc.).
        VERIFY: confirm /reports/{name} paths + response shape."""
        return await self._request(user_id, "GET", f"/reports/{report_name}", organization_id=organization_id, params=params)

    async def list_tax_codes(self, user_id: str, organization_id: str) -> List[Dict[str, Any]]:
        """Active tax codes for the org — used to attach tax to invoices/bills
        (the Zoho analog of qbo_connector.list_tax_codes). Returns the raw Zoho
        tax objects ({tax_id, tax_name, tax_percentage, tax_type, ...}). Returns
        an empty list (never raises) when tax is unconfigured / the call fails.
        VERIFY: confirm '/settings/taxes' endpoint + collection key 'taxes'."""
        try:
            data = await self._request(user_id, "GET", "/settings/taxes", organization_id=organization_id)
            return data.get("taxes", []) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("zoho list_tax_codes failed (tax may be disabled): %s", exc)
            return []


# ======================================================================
# Singleton
# ======================================================================

zoho_connector = ZohoConnector()
