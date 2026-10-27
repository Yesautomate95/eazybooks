"""
SAP S/4HANA Cloud OAuth2 controller.

Supports both grants (settings.SAP_AUTH_MODE):

  client_credentials (default)
    1. Frontend hits GET /ca/sap/oauth/connect (Bearer token).
    2. Backend validates server config and mints/caches a token immediately —
       there is NO browser consent. Returns {connected: true} on success.

  authorization_code (mirrors the Zoho/Xero flow)
    1. GET /ca/sap/oauth/connect -> signed state JWT + the SAP consent URL.
    2. Frontend redirects the browser there; user consents.
    3. SAP redirects to GET /ca/sap/oauth/callback?code=..&state=.. (no Bearer —
       the signed state is the auth).
    4. Backend verifies state, exchanges the code, stores the tokens (keyed by
       user), then redirects back to SAP_FRONTEND_RETURN_URL with ?sap=connected.
"""

import datetime
import logging
from typing import Optional
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from applicationca.services.sap_connector import (
    build_authorize_url,
    delete_tokens,
    ensure_access_token,
    exchange_code,
    load_tokens,
    save_tokens,
)
from config.settings import settings
from utils.auth_utils import SECRET_KEY, verify_jwt
from applicationca.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sap/oauth", tags=["SAP S/4HANA OAuth"])
security = HTTPBearer()

STATE_TTL_SECONDS = 600  # 10 minutes


def _sign_state(user_id: str) -> str:
    payload = {
        "uid": user_id,
        "n": uuid4().hex,
        "purpose": "sap_oauth",
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
    if decoded.get("purpose") != "sap_oauth":
        raise HTTPException(status_code=400, detail="OAuth state purpose mismatch")
    uid = decoded.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="OAuth state missing user")
    return uid


@router.get("/connect", dependencies=[Depends(require_permission("sap.connect"))])
async def connect(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """client_credentials: mint/verify a token now (no redirect needed).
    authorization_code: return the SAP consent URL to redirect the browser to."""
    user = verify_jwt(credentials.credentials)

    if settings.SAP_AUTH_MODE == "client_credentials":
        missing = [
            name for name, value in (
                ("SAP_CLIENT_ID", settings.SAP_CLIENT_ID),
                ("SAP_CLIENT_SECRET", settings.SAP_CLIENT_SECRET),
                ("SAP_TOKEN_URL", settings.SAP_TOKEN_URL),
            ) if not value
        ]
        if missing:
            detail = (
                f"SAP is not configured. Missing or placeholder values: {', '.join(missing)}. "
                "Replace the SAP .env template values with the real BTP service-key credentials "
                "and restart the backend."
            )
            raise HTTPException(
                status_code=500,
                detail=detail,
            )
        try:
            _token, api_base = await ensure_access_token(user["uid"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("SAP client_credentials token mint failed")
            raise HTTPException(status_code=502, detail=f"SAP token mint failed: {exc}")
        return {"mode": "client_credentials", "connected": True, "api_base": api_base}

    # authorization_code
    missing = [
        name for name, value in (
            ("SAP_CLIENT_ID", settings.SAP_CLIENT_ID),
            ("SAP_CLIENT_SECRET", settings.SAP_CLIENT_SECRET),
            ("SAP_AUTHORIZE_URL", settings.SAP_AUTHORIZE_URL),
        ) if not value
    ]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"SAP is not configured. Missing: {', '.join(missing)}. Add the SAP OAuth credentials to the backend environment.",
        )
    state = _sign_state(user["uid"])
    return {"mode": "authorization_code", "auth_url": build_authorize_url(state), "state": state}


@router.get("/callback")
async def callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """OAuth redirect target (authorization_code mode). Validates state, exchanges
    the code for tokens, persists them, then redirects back to the frontend."""
    return_url = settings.SAP_FRONTEND_RETURN_URL

    if error:
        logger.warning(f"SAP OAuth user-cancelled / error: {error}")
        return RedirectResponse(url=f"{return_url}?sap=error&reason={error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state in callback")

    user_id = _verify_state(state)

    try:
        token_data = await exchange_code(code)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("SAP token exchange failed")
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    save_tokens(
        user_id=user_id,
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_in=token_data.get("expires_in", 3600),
        api_base=settings.SAP_API_BASE,
        auth_mode="authorization_code",
    )

    logger.info(f"SAP connected for user {user_id} (authorization_code)")
    return RedirectResponse(url=f"{return_url}?sap=connected")


@router.get("/status", dependencies=[Depends(require_permission("sap.read"))])
async def status(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = verify_jwt(credentials.credentials)
    if settings.SAP_AUTH_MODE == "client_credentials":
        connected = bool(settings.SAP_CLIENT_ID and settings.SAP_CLIENT_SECRET and settings.SAP_TOKEN_URL)
        return {"connected": connected, "mode": "client_credentials", "api_base": settings.SAP_API_BASE}
    tokens = load_tokens(user["uid"])
    return {
        "connected": bool(tokens),
        "mode": "authorization_code",
        "api_base": tokens.get("api_base") if tokens else None,
        "updated_at": tokens["updated_at"].isoformat() if tokens and tokens.get("updated_at") else None,
    }


@router.post("/disconnect", dependencies=[Depends(require_permission("sap.connect"))])
async def disconnect(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Remove stored tokens locally. (SAP technical-user credentials live in the
    Communication Arrangement on the SAP side; there is no remote revoke here.)"""
    user = verify_jwt(credentials.credentials)
    delete_tokens(user["uid"])
    return {"disconnected": True}
