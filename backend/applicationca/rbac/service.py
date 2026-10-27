"""RBAC service layer — pure data access + business invariants.

All endpoint logic delegates here. Each function takes the actor's user_id
where mutations need to be audited or self-protection invariants enforced.

Self-protection invariants (raised as ValueError; controller converts to 4xx):
- The last super-admin cannot be deleted, deactivated, or stripped of the
  super_admin role.
- A user cannot revoke their own super_admin role if they're the only one.
- The super_admin role and its permission set cannot be edited (no points
  taken away from the bootstrap administrator).
"""
from __future__ import annotations

import logging
import secrets
import string
from typing import Iterable, List, Optional, Set, Tuple

import bcrypt
import psycopg2

from .audit import record as audit_record
from .cache import USER_AUTH_CACHE, invalidate_all, invalidate_user_cache
from .db import get_cursor
from .registry import SUPER_ADMIN_ROLE_KEY, SUPERADMIN_PERMISSION

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Errors used by controller for clean status codes
# ─────────────────────────────────────────────────────────────────────────
class RbacError(Exception):
    """Base class for RBAC business-rule violations."""
    status_code = 400


class NotFound(RbacError):
    status_code = 404


class Conflict(RbacError):
    status_code = 409


class SelfProtection(RbacError):
    """Action would compromise the platform's ability to be administered."""
    status_code = 422


# ─────────────────────────────────────────────────────────────────────────
# Permission resolution — the core RBAC computation
# ─────────────────────────────────────────────────────────────────────────
def resolve_user_auth(user_id: str) -> Tuple[Set[str], List[dict], bool]:
    """Return (effective_permissions, roles, is_active) for a user.

    Caches the result for DEFAULT_TTL_SECONDS. Returns ({}, [], False) for
    unknown users so the caller can treat them as anonymous.
    """
    cached = USER_AUTH_CACHE.get(user_id)
    if cached is not None:
        return cached

    with get_cursor() as cur:
        cur.execute("SELECT is_active FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            return set(), [], False
        is_active = bool(row["is_active"])

        # roles
        cur.execute(
            """
            SELECT r.role_id, r.key, r.name, r.description, r.is_system
            FROM user_roles ur
            JOIN roles r ON r.role_id = ur.role_id
            WHERE ur.user_id = %s
            ORDER BY r.name
            """,
            (user_id,),
        )
        roles = [dict(r) for r in cur.fetchall()]
        role_keys = {r["key"] for r in roles}

        # short-circuit: super-admin holds the sentinel
        if SUPER_ADMIN_ROLE_KEY in role_keys:
            perms = {SUPERADMIN_PERMISSION}
        else:
            cur.execute(
                """
                SELECT DISTINCT p.key
                FROM user_roles ur
                JOIN role_permissions rp ON rp.role_id = ur.role_id
                JOIN permissions p ON p.permission_id = rp.permission_id
                WHERE ur.user_id = %s
                """,
                (user_id,),
            )
            perms = {r["key"] for r in cur.fetchall()}

            # direct grants/denies
            cur.execute(
                """
                SELECT p.key, up.is_grant
                FROM user_permissions up
                JOIN permissions p ON p.permission_id = up.permission_id
                WHERE up.user_id = %s
                """,
                (user_id,),
            )
            for r in cur.fetchall():
                if r["is_grant"]:
                    perms.add(r["key"])
                else:
                    perms.discard(r["key"])

    USER_AUTH_CACHE.put(user_id, perms, roles, is_active)
    return perms, roles, is_active


def get_user_basic(user_id: str) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT user_id, username, email, is_active FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────
# Permissions
# ─────────────────────────────────────────────────────────────────────────
def list_permissions() -> List[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT permission_id, key, resource, action, description, module, is_system "
            "FROM permissions ORDER BY module, key"
        )
        return [dict(r) for r in cur.fetchall()]


def create_permission(
    *,
    actor_user_id: str,
    key: str,
    resource: str,
    action: str,
    description: str = "",
    module: str = "Custom",
) -> dict:
    if "." not in key:
        raise RbacError("permission key must be of the form 'resource.action'")
    with get_cursor() as cur:
        cur.execute("SELECT 1 FROM permissions WHERE key = %s", (key,))
        if cur.fetchone():
            raise Conflict(f"permission '{key}' already exists")
        cur.execute(
            """
            INSERT INTO permissions (key, resource, action, description, module, is_system)
            VALUES (%s, %s, %s, %s, %s, FALSE)
            RETURNING permission_id, key, resource, action, description, module, is_system
            """,
            (key, resource, action, description, module),
        )
        row = dict(cur.fetchone())
    audit_record(
        actor_user_id=actor_user_id,
        action="permission.create",
        target_type="permission",
        target_id=row["permission_id"],
        new_value=row,
    )
    return row


# ─────────────────────────────────────────────────────────────────────────
# Roles
# ─────────────────────────────────────────────────────────────────────────
def list_roles_with_counts() -> List[dict]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT r.role_id, r.key, r.name, r.description, r.is_system,
                   r.created_at, r.updated_at,
                   COUNT(DISTINCT ur.user_id)::int AS user_count,
                   COUNT(DISTINCT rp.permission_id)::int AS permission_count
            FROM roles r
            LEFT JOIN user_roles ur ON ur.role_id = r.role_id
            LEFT JOIN role_permissions rp ON rp.role_id = r.role_id
            GROUP BY r.role_id
            ORDER BY r.is_system DESC, r.name
            """
        )
        return [dict(r) for r in cur.fetchall()]


def get_role(role_id: int) -> dict:
    with get_cursor() as cur:
        cur.execute(
            "SELECT role_id, key, name, description, is_system, created_at, updated_at "
            "FROM roles WHERE role_id = %s",
            (role_id,),
        )
        row = cur.fetchone()
        if not row:
            raise NotFound(f"role {role_id} not found")
        role = dict(row)
        cur.execute(
            """
            SELECT p.permission_id, p.key, p.resource, p.action, p.description, p.module
            FROM role_permissions rp
            JOIN permissions p ON p.permission_id = rp.permission_id
            WHERE rp.role_id = %s
            ORDER BY p.module, p.key
            """,
            (role_id,),
        )
        role["permissions"] = [dict(r) for r in cur.fetchall()]
    return role


def _slugify_role_key(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")[:80]


def create_role(
    *,
    actor_user_id: str,
    name: str,
    description: str = "",
    key: Optional[str] = None,
    permission_keys: Optional[Iterable[str]] = None,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise RbacError("role name is required")
    key = (key or _slugify_role_key(name)).strip()
    if key == SUPER_ADMIN_ROLE_KEY:
        raise Conflict("'super_admin' is a reserved role key")
    with get_cursor() as cur:
        cur.execute("SELECT 1 FROM roles WHERE key = %s", (key,))
        if cur.fetchone():
            raise Conflict(f"role key '{key}' already exists")
        cur.execute(
            """
            INSERT INTO roles (key, name, description, is_system, created_by)
            VALUES (%s, %s, %s, FALSE, %s)
            RETURNING role_id
            """,
            (key, name, description, actor_user_id),
        )
        role_id = cur.fetchone()["role_id"]

        if permission_keys:
            _replace_role_permissions(cur, role_id, list(permission_keys), actor_user_id)

    audit_record(
        actor_user_id=actor_user_id,
        action="role.create",
        target_type="role",
        target_id=role_id,
        new_value={"key": key, "name": name, "description": description},
    )
    invalidate_all()  # any user might be re-assigned to this role
    return get_role(role_id)


def update_role(
    *,
    actor_user_id: str,
    role_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    role = get_role(role_id)
    if role["is_system"]:
        raise SelfProtection("system roles cannot be edited")
    updates = {}
    if name is not None and name.strip() and name != role["name"]:
        updates["name"] = name.strip()
    if description is not None and description != role["description"]:
        updates["description"] = description
    if not updates:
        return role
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE roles SET {set_clause}, updated_at = NOW() WHERE role_id = %s",
            (*updates.values(), role_id),
        )
    audit_record(
        actor_user_id=actor_user_id,
        action="role.update",
        target_type="role",
        target_id=role_id,
        previous_value={k: role[k] for k in updates},
        new_value=updates,
    )
    invalidate_all()
    return get_role(role_id)


def delete_role(*, actor_user_id: str, role_id: int) -> None:
    role = get_role(role_id)
    if role["is_system"]:
        raise SelfProtection("system roles cannot be deleted")
    with get_cursor() as cur:
        cur.execute("DELETE FROM roles WHERE role_id = %s", (role_id,))
    audit_record(
        actor_user_id=actor_user_id,
        action="role.delete",
        target_type="role",
        target_id=role_id,
        previous_value=role,
    )
    invalidate_all()


def clone_role(
    *, actor_user_id: str, role_id: int, new_name: str, new_key: Optional[str] = None
) -> dict:
    source = get_role(role_id)
    return create_role(
        actor_user_id=actor_user_id,
        name=new_name,
        description=f"Cloned from {source['name']}",
        key=new_key,
        permission_keys=[p["key"] for p in source["permissions"]],
    )


def set_role_permissions(
    *, actor_user_id: str, role_id: int, permission_keys: List[str]
) -> dict:
    role = get_role(role_id)
    if role["is_system"]:
        raise SelfProtection("system roles' permissions are fixed")
    previous_keys = sorted(p["key"] for p in role["permissions"])
    with get_cursor() as cur:
        _replace_role_permissions(cur, role_id, permission_keys, actor_user_id)
    audit_record(
        actor_user_id=actor_user_id,
        action="role.permissions.set",
        target_type="role",
        target_id=role_id,
        previous_value=previous_keys,
        new_value=sorted(set(permission_keys)),
    )
    invalidate_all()
    return get_role(role_id)


def _replace_role_permissions(cur, role_id: int, permission_keys: List[str], actor_user_id: str):
    keys = list({k for k in permission_keys if k})
    if keys:
        cur.execute(
            "SELECT key, permission_id FROM permissions WHERE key = ANY(%s)", (keys,)
        )
        found = {r["key"]: r["permission_id"] for r in cur.fetchall()}
        missing = [k for k in keys if k not in found]
        if missing:
            raise RbacError(f"unknown permission keys: {', '.join(missing)}")
        ids = list(found.values())
    else:
        ids = []
    cur.execute("DELETE FROM role_permissions WHERE role_id = %s", (role_id,))
    if ids:
        cur.executemany(
            "INSERT INTO role_permissions (role_id, permission_id, granted_by) VALUES (%s, %s, %s)",
            [(role_id, pid, actor_user_id) for pid in ids],
        )


# ─────────────────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────────────────
def list_users(
    *, search: Optional[str] = None, limit: int = 50, offset: int = 0
) -> dict:
    where, params = "", []
    if search:
        where = "WHERE u.username ILIKE %s OR u.email ILIKE %s"
        like = f"%{search}%"
        params = [like, like]
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM users u {where}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            f"""
            SELECT u.user_id, u.username, u.email, u.is_active,
                   COALESCE(
                       json_agg(json_build_object('role_id', r.role_id, 'key', r.key, 'name', r.name))
                       FILTER (WHERE r.role_id IS NOT NULL),
                       '[]'
                   ) AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.user_id
            LEFT JOIN roles r ON r.role_id = ur.role_id
            {where}
            GROUP BY u.user_id
            ORDER BY u.username NULLS LAST, u.email
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        return {
            "total": total,
            "items": [dict(r) for r in cur.fetchall()],
            "limit": limit,
            "offset": offset,
        }


def get_user_detail(user_id: str) -> dict:
    basic = get_user_basic(user_id)
    if not basic:
        raise NotFound(f"user {user_id} not found")
    perms, roles, is_active = resolve_user_auth(user_id)
    return {
        **basic,
        "roles": roles,
        "permissions": sorted(perms),
        "is_super_admin": SUPERADMIN_PERMISSION in perms,
    }


def _generate_user_id() -> str:
    return "usr_" + secrets.token_urlsafe(12)


def _generate_temp_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_user(
    *,
    actor_user_id: str,
    email: str,
    username: str = "",
    password: Optional[str] = None,
    role_keys: Optional[List[str]] = None,
) -> Tuple[dict, str]:
    """Create a user administratively. Returns (user_detail, temp_password)."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise RbacError("a valid email is required")
    with get_cursor() as cur:
        cur.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            raise Conflict("a user with that email already exists")
        user_id = _generate_user_id()
        plain_password = password or _generate_temp_password()
        password_hash = _hash_password(plain_password)
        cur.execute(
            """
            INSERT INTO users (user_id, username, email, password_hash, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
            """,
            (user_id, username or email.split("@")[0], email, password_hash),
        )
        if role_keys:
            _assign_roles_by_keys(cur, user_id, role_keys, actor_user_id)
    audit_record(
        actor_user_id=actor_user_id,
        action="user.create",
        target_type="user",
        target_id=user_id,
        new_value={"email": email, "username": username, "role_keys": role_keys or []},
    )
    return get_user_detail(user_id), plain_password


def update_user(
    *,
    actor_user_id: str,
    user_id: str,
    username: Optional[str] = None,
    email: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> dict:
    basic = get_user_basic(user_id)
    if not basic:
        raise NotFound(f"user {user_id} not found")

    # Self-protection: cannot deactivate the last super-admin.
    if is_active is False and _is_last_super_admin(user_id):
        raise SelfProtection("cannot deactivate the last super-admin")

    updates = {}
    if username is not None and username.strip() and username != basic["username"]:
        updates["username"] = username.strip()
    if email is not None and email.strip().lower() != (basic["email"] or "").lower():
        updates["email"] = email.strip().lower()
    if is_active is not None and is_active != basic["is_active"]:
        updates["is_active"] = is_active
        if not is_active:
            updates["deactivated_at"] = "NOW()"

    if not updates:
        return get_user_detail(user_id)

    # Build the SET clause carefully — deactivated_at uses NOW(), not %s.
    set_parts, values = [], []
    for k, v in updates.items():
        if v == "NOW()":
            set_parts.append(f"{k} = NOW()")
        else:
            set_parts.append(f"{k} = %s")
            values.append(v)
    values.append(user_id)
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE users SET {', '.join(set_parts)} WHERE user_id = %s", values
        )
    audit_record(
        actor_user_id=actor_user_id,
        action="user.update",
        target_type="user",
        target_id=user_id,
        previous_value={k: basic.get(k) for k in updates if k != "deactivated_at"},
        new_value={k: v for k, v in updates.items() if k != "deactivated_at"},
    )
    invalidate_user_cache(user_id)
    return get_user_detail(user_id)


def set_user_roles(
    *, actor_user_id: str, user_id: str, role_keys: List[str]
) -> dict:
    """Whole-set replace of a user's roles."""
    basic = get_user_basic(user_id)
    if not basic:
        raise NotFound(f"user {user_id} not found")

    # Self-protection: do not allow stripping the last super-admin role.
    new_keys = set(role_keys or [])
    if SUPER_ADMIN_ROLE_KEY not in new_keys and _is_last_super_admin(user_id):
        raise SelfProtection(
            "cannot remove super_admin from the last super-administrator"
        )

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT r.key FROM user_roles ur JOIN roles r ON r.role_id = ur.role_id
            WHERE ur.user_id = %s
            """,
            (user_id,),
        )
        previous = sorted(r["key"] for r in cur.fetchall())
        cur.execute("DELETE FROM user_roles WHERE user_id = %s", (user_id,))
        if new_keys:
            _assign_roles_by_keys(cur, user_id, list(new_keys), actor_user_id)
    audit_record(
        actor_user_id=actor_user_id,
        action="user.roles.set",
        target_type="user",
        target_id=user_id,
        previous_value=previous,
        new_value=sorted(new_keys),
    )
    invalidate_user_cache(user_id)
    return get_user_detail(user_id)


def reset_user_password(
    *, actor_user_id: str, user_id: str, new_password: Optional[str] = None
) -> str:
    basic = get_user_basic(user_id)
    if not basic:
        raise NotFound(f"user {user_id} not found")
    plain = new_password or _generate_temp_password()
    password_hash = _hash_password(plain)
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE user_id = %s",
            (password_hash, user_id),
        )
    audit_record(
        actor_user_id=actor_user_id,
        action="user.reset_password",
        target_type="user",
        target_id=user_id,
        metadata={"by_admin": True},
    )
    invalidate_user_cache(user_id)
    return plain


def deactivate_user(*, actor_user_id: str, user_id: str) -> dict:
    return update_user(
        actor_user_id=actor_user_id, user_id=user_id, is_active=False
    )


# ─────────────────────────────────────────────────────────────────────────
# Direct user permissions (grants / denies on top of role-derived perms)
#
# Each entry is one of:
#   {"permission_key": "...", "is_grant": True}   # adds the permission
#   {"permission_key": "...", "is_grant": False}  # explicitly denies it
# A deny always wins over any role-derived grant (compliance-friendly).
# ─────────────────────────────────────────────────────────────────────────
def get_user_direct_permissions(user_id: str) -> List[dict]:
    basic = get_user_basic(user_id)
    if not basic:
        raise NotFound(f"user {user_id} not found")
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT p.permission_id, p.key, p.module, p.description,
                   up.is_grant, up.granted_at, up.granted_by
            FROM user_permissions up
            JOIN permissions p ON p.permission_id = up.permission_id
            WHERE up.user_id = %s
            ORDER BY p.module, p.key
            """,
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def set_user_direct_permissions(
    *, actor_user_id: str, user_id: str, entries: List[dict]
) -> List[dict]:
    """Replace the entire direct-permissions set for a user.

    `entries` is a list of {"permission_key": str, "is_grant": bool}. Empty
    list clears all direct grants/denies.

    Self-protection: an actor cannot deny themselves any `users.*` /
    `roles.*` / `permissions.*` / `audit.*` permission (would lock
    themselves out of administration).
    """
    basic = get_user_basic(user_id)
    if not basic:
        raise NotFound(f"user {user_id} not found")

    # Validate & resolve permission keys to ids.
    cleaned: list[tuple[int, bool]] = []
    if entries:
        keys = [e["permission_key"] for e in entries if e.get("permission_key")]
        if len(keys) != len({k for k in keys}):
            raise RbacError("duplicate permission_key entries are not allowed")
        if not keys:
            entries = []
    if entries:
        with get_cursor() as cur:
            cur.execute(
                "SELECT key, permission_id FROM permissions WHERE key = ANY(%s)",
                (keys,),
            )
            found = {r["key"]: r["permission_id"] for r in cur.fetchall()}
        missing = [k for k in keys if k not in found]
        if missing:
            raise RbacError(f"unknown permission keys: {', '.join(missing)}")
        for e in entries:
            cleaned.append((found[e["permission_key"]], bool(e.get("is_grant", True))))

    # Self-protection: prevent denying yourself administrative perms.
    if actor_user_id == user_id:
        deny_keys = {
            entries[i]["permission_key"]
            for i in range(len(entries))
            if not bool(entries[i].get("is_grant", True))
        }
        admin_namespaces = ("users.", "roles.", "permissions.", "audit.")
        locking_keys = [k for k in deny_keys if k.startswith(admin_namespaces)]
        if locking_keys:
            raise SelfProtection(
                "cannot deny your own administrative permissions: "
                + ", ".join(sorted(locking_keys))
            )

    # Capture previous state for audit
    previous = get_user_direct_permissions(user_id)

    with get_cursor() as cur:
        cur.execute("DELETE FROM user_permissions WHERE user_id = %s", (user_id,))
        if cleaned:
            cur.executemany(
                """
                INSERT INTO user_permissions
                    (user_id, permission_id, is_grant, granted_by)
                VALUES (%s, %s, %s, %s)
                """,
                [(user_id, pid, is_grant, actor_user_id) for pid, is_grant in cleaned],
            )

    audit_record(
        actor_user_id=actor_user_id,
        action="user.direct_permissions.set",
        target_type="user",
        target_id=user_id,
        previous_value=[
            {"key": p["key"], "is_grant": p["is_grant"]} for p in previous
        ],
        new_value=[
            {
                "permission_key": e["permission_key"],
                "is_grant": bool(e.get("is_grant", True)),
            }
            for e in entries
        ],
    )
    invalidate_user_cache(user_id)
    return get_user_direct_permissions(user_id)


# ─────────────────────────────────────────────────────────────────────────
# Self-protection helpers
# ─────────────────────────────────────────────────────────────────────────
def _is_last_super_admin(user_id: str) -> bool:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(DISTINCT ur.user_id) AS n
            FROM user_roles ur
            JOIN roles r ON r.role_id = ur.role_id
            JOIN users u ON u.user_id = ur.user_id
            WHERE r.key = %s AND u.is_active = TRUE
            """,
            (SUPER_ADMIN_ROLE_KEY,),
        )
        active_super_admins = cur.fetchone()["n"]
        if active_super_admins != 1:
            return False
        cur.execute(
            """
            SELECT 1 FROM user_roles ur
            JOIN roles r ON r.role_id = ur.role_id
            WHERE r.key = %s AND ur.user_id = %s
            """,
            (SUPER_ADMIN_ROLE_KEY, user_id),
        )
        return cur.fetchone() is not None


def _assign_roles_by_keys(cur, user_id: str, role_keys: List[str], actor_user_id: str):
    keys = list({k for k in role_keys if k})
    if not keys:
        return
    cur.execute("SELECT key, role_id FROM roles WHERE key = ANY(%s)", (keys,))
    found = {r["key"]: r["role_id"] for r in cur.fetchall()}
    missing = [k for k in keys if k not in found]
    if missing:
        raise RbacError(f"unknown role keys: {', '.join(missing)}")
    cur.executemany(
        """
        INSERT INTO user_roles (user_id, role_id, assigned_by)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        [(user_id, rid, actor_user_id) for rid in found.values()],
    )


# ─────────────────────────────────────────────────────────────────────────
# Audit
# ─────────────────────────────────────────────────────────────────────────
def _audit_where(
    *,
    actor_user_id: Optional[str],
    target_type: Optional[str],
    target_id: Optional[str],
    action: Optional[str],
    start_at: Optional[str],
    end_at: Optional[str],
) -> Tuple[str, list]:
    """Build the WHERE clause shared by list_audit and the CSV export.

    `start_at` / `end_at` are ISO-8601 strings (UTC). Postgres will coerce
    them to timestamptz.
    """
    where, params = [], []
    if actor_user_id:
        where.append("actor_user_id = %s")
        params.append(actor_user_id)
    if target_type:
        where.append("target_type = %s")
        params.append(target_type)
    if target_id:
        where.append("target_id = %s")
        params.append(str(target_id))
    if action:
        where.append("action ILIKE %s")
        params.append(f"%{action}%")
    if start_at:
        where.append("created_at >= %s")
        params.append(start_at)
    if end_at:
        where.append("created_at <= %s")
        params.append(end_at)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return where_sql, params


def list_audit(
    *,
    actor_user_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    action: Optional[str] = None,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    where_sql, params = _audit_where(
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        start_at=start_at,
        end_at=end_at,
    )
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM rbac_audit_log {where_sql}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            f"""
            SELECT audit_id, actor_user_id, action, target_type, target_id,
                   previous_value, new_value, metadata, created_at
            FROM rbac_audit_log
            {where_sql}
            ORDER BY created_at DESC, audit_id DESC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        items = []
        for r in cur.fetchall():
            row = dict(r)
            if row.get("created_at"):
                row["created_at"] = row["created_at"].isoformat()
            items.append(row)
    return {"total": total, "items": items, "limit": limit, "offset": offset}


def list_audit_actors(limit: int = 200) -> List[dict]:
    """Distinct actors who have generated audit events, with a row count.

    Drives the actor-picker in the audit log UI. We join against `users` so
    the picker can show the user's email and display name; null actor_user_id
    (system events) is surfaced separately as {actor_user_id: None}.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT a.actor_user_id, u.email, u.username,
                   COUNT(*)::int AS event_count,
                   MAX(a.created_at) AS last_seen
            FROM rbac_audit_log a
            LEFT JOIN users u ON u.user_id = a.actor_user_id
            GROUP BY a.actor_user_id, u.email, u.username
            ORDER BY last_seen DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = []
        for r in cur.fetchall():
            row = dict(r)
            if row.get("last_seen"):
                row["last_seen"] = row["last_seen"].isoformat()
            rows.append(row)
        return rows


def iter_audit_rows(
    *,
    actor_user_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    action: Optional[str] = None,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
    max_rows: int = 10000,
):
    """Streaming iterator over audit rows for CSV export.

    Capped at `max_rows` (default 10k) — sufficient for typical exports
    without OOM risk on the server. Streaming consumers can paginate the
    list_audit endpoint instead if they need everything.
    """
    where_sql, params = _audit_where(
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        start_at=start_at,
        end_at=end_at,
    )
    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT audit_id, actor_user_id, action, target_type, target_id,
                   previous_value, new_value, metadata, created_at
            FROM rbac_audit_log
            {where_sql}
            ORDER BY created_at DESC, audit_id DESC
            LIMIT %s
            """,
            [*params, max_rows],
        )
        for r in cur.fetchall():
            row = dict(r)
            if row.get("created_at"):
                row["created_at"] = row["created_at"].isoformat()
            yield row
