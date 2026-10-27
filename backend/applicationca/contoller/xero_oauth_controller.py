"""
Xero OAuth2 controller.

Flow (mirrors the Zoho controller):
  1. Frontend hits GET /ca/xero/oauth/connect (Bearer token).
     -> Backend signs a short-lived state JWT (user_id) and returns the Xero
        consent URL (scope must include offline_access for refresh tokens).
  2. Frontend redirects the browser to that URL.
  3. User consents on Xero. Xero redirects to GET /ca/xero/oauth/callback with
     ?code=...&state=... (no tenant id — one token set covers all the user's
     organisations; the active tenant is chosen per call via Xero-tenant-id).
  4. Backend verifies state, exchanges the code for tokens, persists them
     (keyed by user), then redirects back to XERO_FRONTEND_RETURN_URL with
     ?xero=connected.
  5. Frontend calls /ca/xero/get-companies to load the tenant picker
     (GET /connections under the hood).
"""

import datetime
import logging
from typing import Optional
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from applicationca.services.xero_connector import (
    build_authorize_url,
    delete_tokens,
    exchange_code,
    is_user_connected,
    list_connections,
    load_tokens,
    revoke_token,
    save_tokens,
)
from config.settings import settings
from utils.auth_utils import SECRET_KEY, verify_jwt
from applicationca.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/xero/oauth", tags=["Xero OAuth"])
security = HTTPBearer()

STATE_TTL_SECONDS = 600  # 10 minutes


def _sign_state(user_id: str) -> str:
    payload = {
        "uid": user_id,
        "n": uuid4().hex,
        "purpose": "xero_oauth",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(seconds=STATE_TTL_SECONDS),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def _verify_state(state: str) -> str:
    try:
        decoded = jwt.decode(state, SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="OAuth state expired — restart the connect flow")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    if decoded.get("purpose") != "xero_oauth":
        raise HTTPException(status_code=400, detail="OAuth state purpose mismatch")
    uid = decoded.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="OAuth state missing user")
    return uid


@router.get("/connect", dependencies=[Depends(require_permission("xero.connect"))])
async def connect(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Return the Xero consent URL the frontend should redirect to."""
    user = verify_jwt(credentials.credentials)
    if not settings.XERO_CLIENT_ID or not settings.XERO_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="XERO_CLIENT_ID / XERO_CLIENT_SECRET not configured on the server",
        )
    logger.info("Starting Xero OAuth with redirect_uri=%s", settings.XERO_REDIRECT_URI)
    state = _sign_state(user["uid"])
    auth_url = build_authorize_url(state)
    return {"auth_url": auth_url, "state": state}


@router.get("/callback")
async def callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """OAuth redirect target. Validates state, exchanges the code for tokens,
    persists them, then redirects back to the frontend."""
    return_url = settings.XERO_FRONTEND_RETURN_URL

    if error:
        logger.warning(f"Xero OAuth user-cancelled / error: {error}")
        return RedirectResponse(url=f"{return_url}?xero=error&reason={error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state in callback")

    user_id = _verify_state(state)

    try:
        token_data = await exchange_code(code)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Xero token exchange failed")
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    if not token_data.get("refresh_token"):
        logger.warning("Xero token exchange returned no refresh_token (request the offline_access scope).")

    save_tokens(
        user_id=user_id,
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token", ""),
        expires_in=token_data.get("expires_in", 1800),
    )

    logger.info(f"Xero connected for user {user_id}")
    return RedirectResponse(url=f"{return_url}?xero=connected")


@router.get("/status", dependencies=[Depends(require_permission("xero.read"))])
async def status(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = verify_jwt(credentials.credentials)
    tokens = load_tokens(user["uid"])
    connected = bool(tokens)
    tenants = []
    if connected:
        try:
            conns = await list_connections(user["uid"])
            tenants = [
                {"tenant_id": c.get("tenantId"), "name": c.get("tenantName"), "type": c.get("tenantType")}
                for c in conns
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Xero /connections fetch failed in status: {exc}")
    return {
        "connected": connected,
        "tenants": tenants,
        "updated_at": tokens["updated_at"].isoformat() if tokens and tokens.get("updated_at") else None,
    }


@router.post("/disconnect", dependencies=[Depends(require_permission("xero.connect"))])
async def disconnect(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Revoke the refresh token at Xero and remove it locally."""
    user = verify_jwt(credentials.credentials)
    tokens = load_tokens(user["uid"])
    if not tokens:
        return {"disconnected": False, "reason": "not connected"}

    try:
        await revoke_token(tokens["refresh_token"])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Xero revoke failed (continuing with local delete): {exc}")

    delete_tokens(user["uid"])
    return {"disconnected": True}
