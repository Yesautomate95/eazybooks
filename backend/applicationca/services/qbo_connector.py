"""
QuickBooks Online (QBO) connector — OAuth2 + REST.

Talks to:
  Auth:   https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer
  API:    https://sandbox-quickbooks.api.intuit.com/v3/company/{realmId}/...
          (production: https://quickbooks.api.intuit.com/...)

Token storage: refresh tokens live in postgres `qbo_tokens` (one row per
user_id + realm_id), encrypted at rest with QBO_TOKEN_ENCRYPTION_KEY. Access
tokens are NOT persisted — they live only in an in-process memory cache
(_access_token_cache below), lost on restart, and are cheap to re-derive from
the encrypted refresh token. Access tokens last 1 hour; refresh tokens last
~100 days and rotate on each refresh. We refresh proactively when there are
<60s of life left.
"""

import base64
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from applicationca.database.database_retrieval import get_connection, return_connection
from config.settings import settings

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"

# Default scope: read/write accounting entities
SCOPE_ACCOUNTING = "com.intuit.quickbooks.accounting"

# Refresh access tokens this many seconds before they actually expire
REFRESH_LEEWAY_SECONDS = 60


# ======================================================================
# Token storage (postgres qbo_tokens)
# ======================================================================

class QBOTokens(Dict[str, Any]):
    """Typed dict accessor for stored tokens."""
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ----------------------------------------------------------------------
# Refresh-token encryption (at rest in postgres)
# ----------------------------------------------------------------------

def _fernet() -> Fernet:
    key = settings.QBO_TOKEN_ENCRYPTION_KEY
    if not key:
        raise RuntimeError(
            "QBO_TOKEN_ENCRYPTION_KEY is not configured — cannot store/read QBO refresh tokens"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


# ----------------------------------------------------------------------
# Access-token cache — volatile memory only, never written to postgres.
# Lost on process restart; cheap to rebuild from the encrypted refresh
# token, which is the only thing that needs to survive a restart.
# ----------------------------------------------------------------------

_access_token_cache: Dict[Tuple[str, str], Tuple[str, datetime]] = {}
_cache_lock = threading.Lock()


def _cache_get(user_id: str, realm_id: str) -> Optional[str]:
    with _cache_lock:
        entry = _access_token_cache.get((user_id, realm_id))
    if not entry:
        return None
    access_token, expires_at = entry
    if (expires_at - _utcnow()).total_seconds() < REFRESH_LEEWAY_SECONDS:
        return None
    return access_token


def _cache_put(user_id: str, realm_id: str, access_token: str, expires_at: datetime) -> None:
    with _cache_lock:
        _access_token_cache[(user_id, realm_id)] = (access_token, expires_at)


def _cache_drop(user_id: str, realm_id: Optional[str] = None) -> None:
    with _cache_lock:
        if realm_id:
            _access_token_cache.pop((user_id, realm_id), None)
        else:
            for key in [k for k in _access_token_cache if k[0] == user_id]:
                _access_token_cache.pop(key, None)


def save_tokens(
    user_id: str,
    realm_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    refresh_expires_in: Optional[int] = None,
    qbo_env: Optional[str] = None,
) -> None:
    """Cache the access token in-process (volatile memory only — never
    written to postgres) and upsert the refresh token, encrypted, to
    qbo_tokens. Refresh tokens rotate on every refresh, so we always
    overwrite."""
    expires_at = _utcnow() + timedelta(seconds=int(expires_in or 3600))
    refresh_expires_at = (
        _utcnow() + timedelta(seconds=int(refresh_expires_in))
        if refresh_expires_in else None
    )
    env = qbo_env or settings.QBO_ENV

    _cache_put(user_id, realm_id, access_token, expires_at)

    conn = get_connection()
    if conn is None:
        logger.error("qbo_tokens save skipped — DB unavailable")
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO qbo_tokens
                (user_id, realm_id, access_token, refresh_token,
                 expires_at, refresh_expires_at, qbo_env, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, realm_id) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at = EXCLUDED.expires_at,
                refresh_expires_at = COALESCE(EXCLUDED.refresh_expires_at, qbo_tokens.refresh_expires_at),
                qbo_env = EXCLUDED.qbo_env,
                updated_at = CURRENT_TIMESTAMP
            """,
            # access_token column is intentionally left blank — it's never
            # persisted, only cached in-process (see _cache_put above).
            (user_id, realm_id, "", _encrypt(refresh_token),
             expires_at, refresh_expires_at, env),
        )
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)


def load_tokens(user_id: str, realm_id: Optional[str] = None) -> Optional[QBOTokens]:
    """Load the most recently updated token row for the user. If realm_id is
    given, scope to that realm; otherwise return the latest connected company.
    `refresh_token` is decrypted before being returned. Rows whose
    refresh_token can't be decrypted (e.g. written before encryption was
    enabled) are treated as disconnected — the user reconnects once."""
    conn = get_connection()
    if conn is None:
        logger.warning("qbo_tokens lookup skipped — DB unavailable")
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if realm_id:
            cur.execute(
                "SELECT * FROM qbo_tokens WHERE user_id=%s AND realm_id=%s",
                (user_id, realm_id),
            )
        else:
            cur.execute(
                "SELECT * FROM qbo_tokens WHERE user_id=%s "
                "ORDER BY updated_at DESC LIMIT 1",
                (user_id,),
            )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        tokens = QBOTokens(row)
        try:
            tokens["refresh_token"] = _decrypt(tokens["refresh_token"])
        except InvalidToken:
            logger.error(
                f"Failed to decrypt QBO refresh token for user {user_id} "
                f"realm {tokens.get('realm_id')} — treating as disconnected"
            )
            return None
        return tokens
    finally:
        return_connection(conn)


def delete_tokens(user_id: str, realm_id: Optional[str] = None) -> None:
    _cache_drop(user_id, realm_id)
    conn = get_connection()
    if conn is None:
        logger.warning("qbo_tokens delete skipped — DB unavailable")
        return
    try:
        cur = conn.cursor()
        if realm_id:
            cur.execute(
                "DELETE FROM qbo_tokens WHERE user_id=%s AND realm_id=%s",
                (user_id, realm_id),
            )
        else:
            cur.execute("DELETE FROM qbo_tokens WHERE user_id=%s", (user_id,))
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)


def list_user_realms(user_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT realm_id, qbo_env, updated_at FROM qbo_tokens "
            "WHERE user_id=%s ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return list(rows)
    finally:
        return_connection(conn)


# ======================================================================
# OAuth2 flow primitives
# ======================================================================

def build_authorize_url(state: str, scope: str = SCOPE_ACCOUNTING) -> str:
    """Build the Intuit consent URL the browser is redirected to."""
    from urllib.parse import urlencode
    params = {
        "client_id": settings.QBO_CLIENT_ID,
        "response_type": "code",
        "scope": scope,
        "redirect_uri": settings.QBO_REDIRECT_URI,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _basic_auth_header() -> str:
    raw = f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def exchange_code(code: str) -> Dict[str, Any]:
    """Exchange an auth code for access + refresh tokens.

    Returns: {access_token, refresh_token, expires_in, x_refresh_token_expires_in, ...}
    """
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.QBO_REDIRECT_URI,
            },
            headers={
                "Authorization": _basic_auth_header(),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
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
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def revoke_token(token: str) -> None:
    """Revoke the refresh (or access) token at Intuit."""
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            REVOKE_URL,
            json={"token": token},
            headers={
                "Authorization": _basic_auth_header(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )


class QBOReconnectRequired(RuntimeError):
    """Raised when QBO tokens are unusable (refresh token expired/revoked —
    Intuit returns invalid_grant) and the user must redo the OAuth flow.
    Subclasses RuntimeError so existing `except RuntimeError` call sites
    keep working unchanged; a global handler also catches it for call sites
    that don't."""


async def ensure_access_token(user_id: str, realm_id: Optional[str] = None) -> Tuple[str, str, str]:
    """Return (access_token, realm_id, qbo_env). The access token is served
    from the in-process cache when live; on a miss (first call after a
    restart, or <60s of life left) it's refreshed from the encrypted refresh
    token. Raises RuntimeError if user is not connected, or
    QBOReconnectRequired if the stored refresh token is no longer valid.

    `qbo_env` comes from the stored token row, NOT the global setting — so
    a server flipped from sandbox to production keeps serving sandbox-bound
    users at the sandbox API host until they reconnect.
    """
    tokens = load_tokens(user_id, realm_id)
    if not tokens:
        raise RuntimeError("QuickBooks Online is not connected for this user")

    resolved_realm = tokens["realm_id"]
    qbo_env = tokens.get("qbo_env") or settings.QBO_ENV

    cached = _cache_get(user_id, resolved_realm)
    if cached:
        return cached, resolved_realm, qbo_env

    logger.info(f"Refreshing QBO access token for user {user_id} realm {resolved_realm}")
    try:
        refreshed = await refresh_access_token(tokens["refresh_token"])
    except httpx.HTTPStatusError as exc:
        # Refresh token expired (~100 days) or was revoked — Intuit
        # rejects it with invalid_grant. Drop the dead row so /status
        # correctly reports disconnected instead of silently failing on
        # every subsequent call.
        logger.warning(
            f"QBO refresh token rejected for user {user_id} realm "
            f"{resolved_realm}: {exc.response.status_code} {exc.response.text}"
        )
        delete_tokens(user_id, resolved_realm)
        raise QBOReconnectRequired(
            "QuickBooks Online connection expired — please reconnect your QuickBooks account."
        ) from exc
    except httpx.RequestError as exc:
        logger.error("QBO token refresh network error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="QuickBooks Online is temporarily unreachable. Check your network connection and try again.",
        ) from exc
    save_tokens(
        user_id=user_id,
        realm_id=resolved_realm,
        access_token=refreshed["access_token"],
        refresh_token=refreshed.get("refresh_token", tokens["refresh_token"]),
        expires_in=refreshed.get("expires_in", 3600),
        refresh_expires_in=refreshed.get("x_refresh_token_expires_in"),
        qbo_env=qbo_env,
    )
    return refreshed["access_token"], resolved_realm, qbo_env


# ======================================================================
# REST client
# ======================================================================

class QBOConnector:
    """Async REST client for the QBO accounting API.

    All public methods need a `user_id` (Firebase UID) so we can load and
    refresh that user's stored tokens. `realm_id` is optional; if omitted we
    use whichever realm the user most recently connected.
    """

    # Per-environment API hosts — picked per-request from the user's stored
    # token row, not from process-wide settings.
    _ENV_HOSTS = {
        "sandbox": "https://sandbox-quickbooks.api.intuit.com",
        "production": "https://quickbooks.api.intuit.com",
    }

    def __init__(self, base_url: Optional[str] = None, timeout: int = 30):
        # base_url retained as a fallback (e.g., for unit tests) but real
        # requests resolve the host from the token's qbo_env.
        self.base_url = base_url or settings.QBO_API_BASE
        self.timeout = timeout

    def _host_for_env(self, qbo_env: Optional[str]) -> str:
        env = (qbo_env or "sandbox").lower()
        return self._ENV_HOSTS.get(env, self.base_url)

    # --- low-level transport ---

    async def _request(
        self,
        user_id: str,
        method: str,
        path: str,
        *,
        realm_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        access_token, resolved_realm, qbo_env = await ensure_access_token(user_id, realm_id)
        host = self._host_for_env(qbo_env)
        url = f"{host}/v3/company/{resolved_realm}{path}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
        except httpx.TimeoutException as exc:
            logger.error("QBO %s %s timed out: %s", method, path, exc)
            raise HTTPException(
                status_code=503,
                detail="QuickBooks Online did not respond in time. Check your network connection and try again.",
            ) from exc
        except httpx.RequestError as exc:
            logger.error("QBO %s %s network error: %s", method, path, exc)
            raise HTTPException(
                status_code=503,
                detail="QuickBooks Online is temporarily unreachable. Check your network connection and try again.",
            ) from exc
        if resp.status_code >= 400:
            logger.error(f"QBO {method} {path} -> {resp.status_code}: {resp.text}")
            # Convert to HTTPException so FastAPI returns a proper 4xx
            # response (with CORS headers) instead of a bare 500 that
            # strips them.
            detail: Any
            try:
                body = resp.json()
                fault = (body or {}).get("Fault") or {}
                errors = fault.get("Error") or []
                if errors:
                    first = errors[0]
                    detail = {
                        "qbo_error": first.get("Message"),
                        "qbo_detail": first.get("Detail"),
                        "qbo_code": first.get("code"),
                    }
                else:
                    detail = body
            except Exception:
                detail = resp.text or f"QBO {method} {path} failed"
            raise HTTPException(status_code=resp.status_code, detail=detail)
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return {"raw": resp.text}

    async def query(self, user_id: str, sql: str, realm_id: Optional[str] = None) -> Dict[str, Any]:
        """Run a QBO SQL-like query (SELECT * FROM Customer, etc.)."""
        return await self._request(
            user_id, "GET", "/query",
            realm_id=realm_id, params={"query": sql, "minorversion": "65"},
        )

    async def query_all(
        self,
        user_id: str,
        base_query: str,
        entity_key: str,
        realm_id: Optional[str] = None,
        page_size: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Run a QBO query across ALL pages and return the flattened rows.

        QBO caps a single query page at 1000 rows, so any caller that needs the
        full set must page with STARTPOSITION (1-based) — otherwise high-volume
        realms are silently truncated. `base_query` must NOT already contain
        STARTPOSITION/MAXRESULTS; we append them per page and stop once a short
        page comes back. A hard backstop guards against a misbehaving cursor.
        """
        out: List[Dict[str, Any]] = []
        start = 1
        while True:
            page_q = f"{base_query} STARTPOSITION {start} MAXRESULTS {page_size}"
            data = await self.query(user_id, page_q, realm_id)
            rows = (
                (data.get("QueryResponse", {}) or {}).get(entity_key, [])
                if isinstance(data, dict) else []
            )
            out.extend(rows)
            if len(rows) < page_size:
                break
            start += page_size
            if start > 100_000:  # ~100k rows — far beyond any realistic dashboard window
                logger.warning("query_all hit the 100k backstop for %s", entity_key)
                break
        return out

    async def report(
        self,
        user_id: str,
        report_name: str,
        *,
        realm_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch a QBO report (TransactionList, ProfitAndLoss, etc.).

        QBO reports return a structured Header/Columns/Rows tree, not a flat
        list — callers are expected to flatten the rows themselves.
        """
        merged = {"minorversion": "65", **(params or {})}
        return await self._request(
            user_id, "GET", f"/reports/{report_name}",
            realm_id=realm_id, params=merged,
        )

    # --- company / health ---

    async def get_company_info(self, user_id: str, realm_id: Optional[str] = None) -> Dict[str, Any]:
        _, resolved, _ = await ensure_access_token(user_id, realm_id)
        data = await self._request(user_id, "GET", f"/companyinfo/{resolved}", realm_id=resolved)
        return data.get("CompanyInfo", data)

    # --- master reads ---

    async def list_customers(self, user_id: str, realm_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.query_all(user_id, "SELECT * FROM Customer", "Customer", realm_id)

    async def list_vendors(self, user_id: str, realm_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.query_all(user_id, "SELECT * FROM Vendor", "Vendor", realm_id)

    async def list_accounts(self, user_id: str, realm_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.query_all(user_id, "SELECT * FROM Account", "Account", realm_id)

    async def list_items(self, user_id: str, realm_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.query_all(user_id, "SELECT * FROM Item", "Item", realm_id)

    async def list_tax_codes(self, user_id: str, realm_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Active TaxCodes for the realm — used to attach tax to invoices/bills
        (US Automated Sales Tax or EU/UK VAT). Returns [] if the realm has tax
        disabled or the query fails (caller treats that as 'no tax posting')."""
        try:
            data = await self.query(user_id, "SELECT * FROM TaxCode MAXRESULTS 1000", realm_id)
            return data.get("QueryResponse", {}).get("TaxCode", [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_tax_codes failed (tax may be disabled): %s", exc)
            return []

    # --- master writes ---

    async def create_customer(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(user_id, "POST", "/customer", realm_id=realm_id, json_body=body)

    async def create_vendor(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(user_id, "POST", "/vendor", realm_id=realm_id, json_body=body)

    async def create_account(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(user_id, "POST", "/account", realm_id=realm_id, json_body=body)

    async def create_item(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a product/service Item. Body must include Name, Type
        ('Service'|'Inventory'|'NonInventory'), and an IncomeAccountRef (and
        ExpenseAccountRef for Inventory/NonInventory). The controller wraps
        this with sensible defaults for the common 'Service' case."""
        return await self._request(user_id, "POST", "/item", realm_id=realm_id, json_body=body)

    # --- transactions ---

    async def create_invoice(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(user_id, "POST", "/invoice", realm_id=realm_id, json_body=body)

    async def create_bill(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(user_id, "POST", "/bill", realm_id=realm_id, json_body=body)

    async def create_deposit(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(user_id, "POST", "/deposit", realm_id=realm_id, json_body=body)

    async def create_purchase(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        """Cash/Check/CreditCard expense (PaymentType in body)."""
        return await self._request(user_id, "POST", "/purchase", realm_id=realm_id, json_body=body)

    async def create_billpayment(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        """Pays one or more open Bills (vendor payment from a bank account)."""
        return await self._request(user_id, "POST", "/billpayment", realm_id=realm_id, json_body=body)

    async def create_payment(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        """Records a customer payment received against one or more open Invoices."""
        return await self._request(user_id, "POST", "/payment", realm_id=realm_id, json_body=body)

    async def create_transfer(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        """Move money between two balance-sheet accounts (the QBO analog of a
        Tally Contra). Body needs FromAccountRef, ToAccountRef and Amount."""
        return await self._request(user_id, "POST", "/transfer", realm_id=realm_id, json_body=body)

    async def create_journalentry(self, user_id: str, body: Dict[str, Any], realm_id: Optional[str] = None) -> Dict[str, Any]:
        """Post a manual journal entry (the QBO analog of a Tally Journal). Body
        needs a Line list of balanced Debit/Credit JournalEntryLineDetail rows."""
        return await self._request(user_id, "POST", "/journalentry", realm_id=realm_id, json_body=body)


# ======================================================================
# Singleton + convenience helpers
# ======================================================================

qbo_connector = QBOConnector()


def is_user_connected(user_id: str) -> bool:
    return load_tokens(user_id) is not None
