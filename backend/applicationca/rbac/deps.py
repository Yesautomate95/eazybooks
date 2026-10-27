"""FastAPI dependencies for authentication + authorization.

`get_current_user` resolves the bearer token, loads the User domain object,
and returns it. Inactive users are rejected with 401. `require_permission`
is a factory that returns a dependency rejecting with 403 when the active
user lacks the requested permission.

Usage:

    @router.post("/something",
                 dependencies=[Depends(require_permission("foo.create"))])
    def handler(...): ...

Or, if you need the user inside the handler:

    @router.get("/me")
    def me(user: User = Depends(require_permission("rbac.read"))):
        return user.to_public_dict()
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from utils.auth_utils import verify_jwt

from . import audit, service
from .models import User

# Mutations on routes whose path starts with this prefix already emit
# rich audit rows inside the service layer (with previous_value / new_value).
# Auto-auditing them here would produce noisy duplicates, so we skip them.
_AUTO_AUDIT_SKIP_PREFIXES = ("/ca/rbac/", "/ca/auth/")
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _maybe_auto_audit(
    *, request: Request | None, user: User, permission_key: str
) -> None:
    """Emit an audit row for any successful permission check on a mutating route.

    Idempotent w.r.t. errors — the audit writer never raises, so this is
    safe to call from a dependency. The action key is namespaced as
    `endpoint.<permission_key>` so the audit log clearly distinguishes
    request-level events from service-emitted ones like `user.create`.
    """
    if request is None:
        return
    if request.method not in _MUTATING_METHODS:
        return
    path = request.url.path
    if any(path.startswith(p) for p in _AUTO_AUDIT_SKIP_PREFIXES):
        return
    metadata = {
        "method": request.method,
        "path": path,
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }
    audit.record(
        actor_user_id=user.user_id,
        action=f"endpoint.{permission_key}",
        target_type="endpoint",
        target_id=path,
        metadata=metadata,
    )

logger = logging.getLogger(__name__)

# Reused across all RBAC-gated endpoints; matches the existing auth pattern.
security = HTTPBearer(auto_error=False)


def _load_user_from_payload(payload: dict) -> User:
    user_id = payload.get("uid") or payload.get("user_id")
    email = payload.get("email", "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token"
        )
    perms, roles, is_active = service.resolve_user_auth(user_id)
    if not is_active:
        # Could be unknown user (treated as inactive) or explicitly deactivated.
        basic = service.get_user_basic(user_id)
        if basic is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="user_not_found"
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user_deactivated"
        )
    basic = service.get_user_basic(user_id) or {}
    return User(
        user_id=user_id,
        email=basic.get("email") or email,
        username=basic.get("username") or "",
        is_active=is_active,
        roles=roles,
        permissions=perms,
    )


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> User:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication_required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = verify_jwt(creds.credentials)
    return _load_user_from_payload(payload)


def get_optional_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[User]:
    """Like `get_current_user` but returns None for missing/invalid tokens.

    Useful for endpoints that vary their response based on auth state but
    don't require authentication.
    """
    if creds is None:
        return None
    try:
        payload = verify_jwt(creds.credentials)
        return _load_user_from_payload(payload)
    except HTTPException:
        return None


def require_permission(permission_key: str):
    """Dependency factory for single-permission checks.

    Returns the resolved User on success so handlers can use it directly.
    Also auto-emits an audit row for mutating requests on non-RBAC routes
    so the audit log reflects every authorized mutation across the platform.
    """
    def checker(request: Request, user: User = Depends(get_current_user)) -> User:
        if not user.has_permission(permission_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "permission_denied",
                    "required": permission_key,
                },
            )
        _maybe_auto_audit(request=request, user=user, permission_key=permission_key)
        return user
    return checker


def require_any_permission(*permission_keys: str):
    """Pass if the user has ANY of the given permissions."""
    keys = tuple(permission_keys)

    def checker(request: Request, user: User = Depends(get_current_user)) -> User:
        if not user.has_any(keys):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "permission_denied", "required_any": list(keys)},
            )
        # Record under the first satisfied permission for traceability.
        for k in keys:
            if user.has_permission(k):
                _maybe_auto_audit(request=request, user=user, permission_key=k)
                break
        return user
    return checker


def require_all_permissions(*permission_keys: str):
    """Pass only if the user has ALL of the given permissions."""
    keys = tuple(permission_keys)

    def checker(request: Request, user: User = Depends(get_current_user)) -> User:
        if not user.has_all(keys):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "permission_denied", "required_all": list(keys)},
            )
        # Record under the first key — they're an AND set.
        if keys:
            _maybe_auto_audit(request=request, user=user, permission_key=keys[0])
        return user
    return checker
