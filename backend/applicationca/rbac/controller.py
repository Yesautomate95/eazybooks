"""RBAC HTTP API mounted at /ca/rbac.

Endpoints are organized in three groups:
- /me           — current user's identity + effective permissions
- /permissions  — registry (system + custom)
- /roles        — role CRUD + permission set management
- /users        — user CRUD + role assignment
- /audit        — append-only ledger

Every mutating endpoint validates permissions through Depends() and emits
a row to rbac_audit_log via the service layer.
"""
from __future__ import annotations

import logging
from typing import Optional

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import bootstrap as bootstrap_module

from . import service
from .deps import (
    get_current_user,
    require_any_permission,
    require_permission,
)
from .models import User
from .schemas import (
    PasswordReset,
    PermissionCreate,
    RoleClone,
    RoleCreate,
    RolePermissionsSet,
    RoleUpdate,
    UserCreate,
    UserDirectPermissionsSet,
    UserRolesSet,
    UserUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rbac", tags=["RBAC"])


def _handle_rbac_error(exc: service.RbacError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.__class__.__name__.lower(), "message": str(exc)},
    )


# ─────────────────────────────────────────────────────────────────────────
# /me — what the current user can do
# ─────────────────────────────────────────────────────────────────────────
@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    return user.to_public_dict()


# ─────────────────────────────────────────────────────────────────────────
# /bootstrap/promote-me — self-promote if your JWT email is in SUPER_ADMIN_EMAILS
#
# This exists because the startup bootstrap matches by users.email, which
# is NULL for users created via Google OAuth (add_new_user only inserts
# user_id + username). The endpoint uses the email claim from the JWT,
# which Firebase populates reliably, so it succeeds where the startup
# match couldn't. It also backfills users.email so future startups can
# promote the same user normally.
# ─────────────────────────────────────────────────────────────────────────
@router.post("/bootstrap/promote-me")
def promote_me(user: User = Depends(get_current_user)):
    result = bootstrap_module.promote_user_if_env_listed(
        user_id=user.user_id, jwt_email=user.email
    )
    if not result["promoted"]:
        # 403 is misleading here — the caller IS authenticated, they're just
        # not listed. 200 with a structured payload is friendlier to a UI
        # button that needs to render "not eligible" vs "promoted".
        return {
            "promoted": False,
            "reason": result["reason"],
            "jwt_email": user.email,
        }
    return {
        "promoted": True,
        "matched_email": result["matched_email"],
        "user_id": user.user_id,
        "note": "Log out and back in (or wait 5 minutes for the permission cache to expire) to see super-admin in /me.",
    }


# ─────────────────────────────────────────────────────────────────────────
# /permissions
# ─────────────────────────────────────────────────────────────────────────
@router.get("/permissions",
            dependencies=[Depends(require_permission("permissions.read"))])
def list_permissions():
    return {"items": service.list_permissions()}


@router.post("/permissions", status_code=201)
def create_permission(
    body: PermissionCreate,
    actor: User = Depends(require_permission("permissions.create")),
):
    try:
        return service.create_permission(
            actor_user_id=actor.user_id,
            key=body.key,
            resource=body.resource,
            action=body.action,
            description=body.description,
            module=body.module,
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


# ─────────────────────────────────────────────────────────────────────────
# /roles
# ─────────────────────────────────────────────────────────────────────────
@router.get("/roles",
            dependencies=[Depends(require_permission("roles.read"))])
def list_roles():
    return {"items": service.list_roles_with_counts()}


@router.post("/roles", status_code=201)
def create_role(body: RoleCreate, actor: User = Depends(require_permission("roles.create"))):
    try:
        return service.create_role(
            actor_user_id=actor.user_id,
            name=body.name,
            description=body.description,
            key=body.key,
            permission_keys=body.permission_keys,
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.get("/roles/{role_id}",
            dependencies=[Depends(require_permission("roles.read"))])
def get_role(role_id: int):
    try:
        return service.get_role(role_id)
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.patch("/roles/{role_id}")
def update_role(
    role_id: int, body: RoleUpdate, actor: User = Depends(require_permission("roles.update"))
):
    try:
        return service.update_role(
            actor_user_id=actor.user_id,
            role_id=role_id,
            name=body.name,
            description=body.description,
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(role_id: int, actor: User = Depends(require_permission("roles.delete"))):
    try:
        service.delete_role(actor_user_id=actor.user_id, role_id=role_id)
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)
    return JSONResponse(status_code=204, content=None)


@router.post("/roles/{role_id}/clone", status_code=201)
def clone_role(
    role_id: int, body: RoleClone, actor: User = Depends(require_permission("roles.clone"))
):
    try:
        return service.clone_role(
            actor_user_id=actor.user_id,
            role_id=role_id,
            new_name=body.new_name,
            new_key=body.new_key,
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.put("/roles/{role_id}/permissions")
def set_role_permissions(
    role_id: int,
    body: RolePermissionsSet,
    actor: User = Depends(require_permission("roles.update")),
):
    try:
        return service.set_role_permissions(
            actor_user_id=actor.user_id,
            role_id=role_id,
            permission_keys=body.permission_keys,
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


# ─────────────────────────────────────────────────────────────────────────
# /users
# ─────────────────────────────────────────────────────────────────────────
@router.get("/users",
            dependencies=[Depends(require_permission("users.read"))])
def list_users(
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return service.list_users(search=search, limit=limit, offset=offset)


@router.post("/users", status_code=201)
def create_user(body: UserCreate, actor: User = Depends(require_permission("users.create"))):
    try:
        user_detail, temp_password = service.create_user(
            actor_user_id=actor.user_id,
            email=body.email,
            username=body.username,
            password=body.password,
            role_keys=body.role_keys,
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)
    # Temp password returned once; caller must communicate it to the user out-of-band.
    return {"user": user_detail, "temp_password": temp_password}


@router.get("/users/{user_id}",
            dependencies=[Depends(require_permission("users.read"))])
def get_user(user_id: str):
    try:
        return service.get_user_detail(user_id)
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.patch("/users/{user_id}")
def update_user(
    user_id: str, body: UserUpdate, actor: User = Depends(require_permission("users.update"))
):
    try:
        return service.update_user(
            actor_user_id=actor.user_id,
            user_id=user_id,
            username=body.username,
            email=body.email,
            is_active=body.is_active,
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.delete("/users/{user_id}")
def deactivate_user(user_id: str, actor: User = Depends(require_permission("users.delete"))):
    try:
        return service.deactivate_user(actor_user_id=actor.user_id, user_id=user_id)
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.put("/users/{user_id}/roles")
def set_user_roles(
    user_id: str,
    body: UserRolesSet,
    actor: User = Depends(require_permission("users.assign_roles")),
):
    try:
        return service.set_user_roles(
            actor_user_id=actor.user_id, user_id=user_id, role_keys=body.role_keys
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    body: PasswordReset,
    actor: User = Depends(require_permission("users.reset_password")),
):
    try:
        new_password = service.reset_user_password(
            actor_user_id=actor.user_id, user_id=user_id, new_password=body.new_password
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)
    return {"temp_password": new_password}


@router.get("/users/{user_id}/direct-permissions",
            dependencies=[Depends(require_permission("users.read"))])
def list_user_direct_permissions(user_id: str):
    try:
        return {"items": service.get_user_direct_permissions(user_id)}
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)


@router.put("/users/{user_id}/direct-permissions")
def set_user_direct_permissions(
    user_id: str,
    body: UserDirectPermissionsSet,
    actor: User = Depends(require_permission("users.assign_roles")),
):
    try:
        items = service.set_user_direct_permissions(
            actor_user_id=actor.user_id,
            user_id=user_id,
            entries=[e.model_dump() for e in body.entries],
        )
    except service.RbacError as exc:
        raise _handle_rbac_error(exc)
    return {"items": items}


# ─────────────────────────────────────────────────────────────────────────
# /audit
# ─────────────────────────────────────────────────────────────────────────
@router.get("/audit",
            dependencies=[Depends(require_permission("audit.read"))])
def list_audit(
    actor_user_id: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    start_at: Optional[str] = Query(None, description="ISO-8601 lower bound (inclusive)"),
    end_at: Optional[str] = Query(None, description="ISO-8601 upper bound (inclusive)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return service.list_audit(
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        offset=offset,
    )


@router.get("/audit/actors",
            dependencies=[Depends(require_permission("audit.read"))])
def list_audit_actors(limit: int = Query(200, ge=1, le=1000)):
    """Distinct actors who have generated audit events.

    Drives the actor-picker in the audit log UI.
    """
    return {"items": service.list_audit_actors(limit=limit)}


@router.get("/audit/export.csv",
            dependencies=[Depends(require_permission("audit.read"))])
def export_audit_csv(
    actor_user_id: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    start_at: Optional[str] = Query(None),
    end_at: Optional[str] = Query(None),
    max_rows: int = Query(10000, ge=1, le=50000),
):
    """Stream a CSV download of the filtered audit log.

    Capped at 50k rows even when callers ask for more — anything above
    that should be done via a database export rather than a UI download.
    """
    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "audit_id", "created_at", "actor_user_id", "action",
            "target_type", "target_id",
            "previous_value", "new_value", "metadata",
        ])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()
        for row in service.iter_audit_rows(
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            start_at=start_at,
            end_at=end_at,
            max_rows=max_rows,
        ):
            writer.writerow([
                row.get("audit_id"),
                row.get("created_at"),
                row.get("actor_user_id") or "",
                row.get("action"),
                row.get("target_type") or "",
                row.get("target_id") or "",
                json.dumps(row.get("previous_value")) if row.get("previous_value") is not None else "",
                json.dumps(row.get("new_value")) if row.get("new_value") is not None else "",
                json.dumps(row.get("metadata")) if row.get("metadata") is not None else "",
            ])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    filename = "audit-log.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
