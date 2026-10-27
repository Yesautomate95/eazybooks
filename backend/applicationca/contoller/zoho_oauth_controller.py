"""
Zoho Books OAuth2 controller.

Flow (mirrors the QBO controller, with Zoho's differences):
  1. Frontend hits GET /ca/zoho/oauth/connect (Bearer token).
     -> Backend signs a short-lived state JWT (user_id) and returns the Zoho
        consent URL (access_type=offline & prompt=consent so we get a refresh
        token).
  2. Frontend redirects the browser to that URL.
  3. User consents on Zoho. Zoho redirects to GET /ca/zoho/oauth/callback with
     ?code=...&state=...&location=...&accounts-server=... (no Bearer header —
     the signed state is the auth). NOTE: unlike QBO there is NO company id in
     the callback; one Zoho token covers all the user's organizations.
  4. Backend verifies state, exchanges the code against the regional accounts
     server, stores the tokens (keyed by user) with the api_domain + DC, then
     redirects back to ZOHO_FRONTEND_RETURN_URL with ?zoho=connected.
  5. Frontend calls /ca/zoho/get-companies to load the org picker.
"""

import datetime
import logging
from typing import Optional
from uuid import uuid4
from urllib.parse import urlparse

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from applicationca.services.zoho_connector import (
    build_authorize_url,
    delete_tokens,
    exchange_code,
    is_user_connected,
    load_tokens,
    revoke_token,
    save_tokens,
)
from config.settings import settings
from utils.auth_utils import SECRET_KEY, verify_jwt
from applicationca.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/zoho/oauth", tags=["Zoho Books OAuth"])
security = HTTPBearer()

STATE_TTL_SECONDS = 600  # 10 minutes

# Zoho's callback `location` param -> our DC key. VERIFY against live docs.
_LOCATION_TO_DC = {
    "us": "com",
    "com": "com",
    "in": "in",
    "eu": "eu",
    "au": "com.au",
    "com.au": "com.au",
    "jp": "jp",
    "ca": "ca",
    "sa": "sa",
    "uk": "eu",
}


def _sign_state(user_id: str) -> str:
    payload = {
        "uid": user_id,
        "n": uuid4().hex,
        "purpose": "zoho_oauth",
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
    if decoded.get("purpose") != "zoho_oauth":
        raise HTTPException(status_code=400, detail="OAuth state purpose mismatch")
    uid = decoded.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="OAuth state missing user")
    return uid


@router.get("/connect", dependencies=[Depends(require_permission("zoho.connect"))])
async def connect(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Return the Zoho consent URL the frontend should redirect to."""
    user = verify_jwt(credentials.credentials)
    if not settings.ZOHO_CLIENT_ID or not settings.ZOHO_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET not configured on the server",
        )
    state = _sign_state(user["uid"])
    auth_url = build_authorize_url(state)
    return {"auth_url": auth_url, "state": state}


@router.get("/callback")
async def callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    accounts_server: Optional[str] = Query(None, alias="accounts-server"),
    error: Optional[str] = Query(None),
):
    """OAuth redirect target. Validates state, exchanges the code for tokens
    against the regional accounts server, persists them, then redirects back to
    the frontend."""
    return_url = settings.ZOHO_FRONTEND_RETURN_URL

    if error:
        logger.warning(f"Zoho OAuth user-cancelled / error: {error}")
        return RedirectResponse(url=f"{return_url}?zoho=error&reason={error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state in callback")

    user_id = _verify_state(state)
    dc = _LOCATION_TO_DC.get((location or "").lower(), settings.ZOHO_DC)

    try:
        token_data = await exchange_code(code, dc=dc, accounts_server=accounts_server)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Zoho token exchange failed")
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    if not token_data.get("refresh_token"):
        # Without offline access we can't refresh — surface a clear hint.
        logger.warning("Zoho token exchange returned no refresh_token (re-consent with offline access).")

    save_tokens(
        user_id=user_id,
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_in=token_data.get("expires_in", 3600),
        api_domain=token_data.get("api_domain"),
        dc=dc,
    )

    logger.info(f"Zoho connected for user {user_id} (dc={dc})")
    return RedirectResponse(url=f"{return_url}?zoho=connected")


@router.get("/status", dependencies=[Depends(require_permission("zoho.read"))])
async def status(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = verify_jwt(credentials.credentials)
    tokens = load_tokens(user["uid"])
    return {
        "connected": bool(tokens),
        "dc": tokens.get("dc") if tokens else None,
        "api_domain": tokens.get("api_domain") if tokens else None,
        "updated_at": tokens["updated_at"].isoformat() if tokens and tokens.get("updated_at") else None,
    }


@router.post("/disconnect", dependencies=[Depends(require_permission("zoho.connect"))])
async def disconnect(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Revoke the refresh token at Zoho and remove it locally."""
    user = verify_jwt(credentials.credentials)
    tokens = load_tokens(user["uid"])
    if not tokens:
        return {"disconnected": False, "reason": "not connected"}

    try:
        await revoke_token(tokens["refresh_token"], tokens.get("dc"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Zoho revoke failed (continuing with local delete): {exc}")

    delete_tokens(user["uid"])
    return {"disconnected": True}
