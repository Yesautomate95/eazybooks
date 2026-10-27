"""
QuickBooks Online OAuth2 controller.

Flow:
  1. Frontend hits GET /ca/qb/oauth/connect (Bearer token).
     -> Backend signs a short-lived state JWT containing user_id and returns
        the Intuit consent URL.
  2. Frontend redirects browser to that URL.
  3. User consents on Intuit. Intuit redirects to GET /ca/qb/oauth/callback
     with ?code=...&state=...&realmId=... (no Bearer header — state is auth).
  4. Backend verifies state JWT, exchanges code for tokens, saves them, then
     redirects browser back to QBO_FRONTEND_RETURN_URL with ?qbo=connected.
  5. Frontend re-fetches /ca/qb/oauth/status to confirm.
"""

import datetime
import logging
from typing import Optional
from urllib.parse import urlencode
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from applicationca.services.qbo_connector import (
    build_authorize_url,
    delete_tokens,
    exchange_code,
    is_user_connected,
    list_user_realms,
    load_tokens,
    revoke_token,
    save_tokens,
)
from config.settings import settings
from utils.auth_utils import SECRET_KEY, verify_jwt
from applicationca.rbac import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/qb/oauth", tags=["QuickBooks Online OAuth"])
security = HTTPBearer()

STATE_TTL_SECONDS = 600  # 10 minutes


# ----------------------------------------------------------------------
# State helpers — signed with JWT_SECRET so callback can trust user_id
# ----------------------------------------------------------------------

def _sign_state(user_id: str) -> str:
    payload = {
        "uid": user_id,
        "n": uuid4().hex,
        "purpose": "qbo_oauth",
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
    if decoded.get("purpose") != "qbo_oauth":
        raise HTTPException(status_code=400, detail="OAuth state purpose mismatch")
    uid = decoded.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="OAuth state missing user")
    return uid


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@router.get("/connect", dependencies=[Depends(require_permission("quickbooks.connect"))])
async def connect(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Return the Intuit consent URL the frontend should redirect to."""
    user = verify_jwt(credentials.credentials)
    if not settings.QBO_CLIENT_ID or not settings.QBO_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="QBO_CLIENT_ID / QBO_CLIENT_SECRET not configured on the server",
        )
    state = _sign_state(user["uid"])
    auth_url = build_authorize_url(state)
    return {"auth_url": auth_url, "state": state}


@router.get("/callback")
async def callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    realmId: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """OAuth redirect target. Validates state, exchanges code for tokens,
    saves them, redirects browser back to the frontend."""
    return_url = settings.QBO_FRONTEND_RETURN_URL

    if error:
        logger.warning(f"QBO OAuth user-cancelled: {error} {error_description}")
        query = urlencode({
            "qbo": "error",
            "reason": error,
            **({"description": error_description} if error_description else {}),
        })
        return RedirectResponse(url=f"{return_url}?{query}")

    if not code or not state or not realmId:
        raise HTTPException(status_code=400, detail="Missing code/state/realmId in callback")

    user_id = _verify_state(state)

    try:
        token_data = await exchange_code(code)
    except Exception as exc:  # noqa: BLE001
        logger.exception("QBO token exchange failed")
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    save_tokens(
        user_id=user_id,
        realm_id=realmId,
        access_token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        expires_in=token_data.get("expires_in", 3600),
        refresh_expires_in=token_data.get("x_refresh_token_expires_in"),
        qbo_env=settings.QBO_ENV,
    )

    logger.info(f"QBO connected for user {user_id} realm {realmId}")
    return RedirectResponse(url=f"{return_url}?qbo=connected&realmId={realmId}")


@router.get("/status", dependencies=[Depends(require_permission("quickbooks.read"))])
async def status(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = verify_jwt(credentials.credentials)
    realms = list_user_realms(user["uid"])
    return {
        "connected": bool(realms),
        "realms": [
            {"realm_id": r["realm_id"], "qbo_env": r["qbo_env"], "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None}
            for r in realms
        ],
    }


@router.post("/disconnect", dependencies=[Depends(require_permission("quickbooks.connect"))])
async def disconnect(
    realm_id: Optional[str] = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Revoke tokens at Intuit and remove from DB. If realm_id omitted, the
    most recent connection is disconnected."""
    user = verify_jwt(credentials.credentials)
    tokens = load_tokens(user["uid"], realm_id)
    if not tokens:
        return {"disconnected": False, "reason": "not connected"}

    try:
        await revoke_token(tokens["refresh_token"])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Intuit revoke failed (continuing with local delete): {exc}")

    delete_tokens(user["uid"], tokens["realm_id"])
    return {"disconnected": True, "realm_id": tokens["realm_id"]}
