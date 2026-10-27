"""RBAC bootstrap — runs once at app startup.

Steps:
1. Run the SQL migration if the rbac tables don't exist (idempotent).
2. Upsert every PermissionRegistry entry into the `permissions` table.
3. Ensure the `super_admin` system role exists; reset its permission set
   to include every permission in the registry so it really does have
   "everything" (cheap nightly-style heal, no harm if already correct).
4. Promote any users in env SUPER_ADMIN_EMAILS to super_admin.
5. If there are no super-admins yet AND there is exactly one user in the
   system, promote that user (zero-touch bootstrap). Otherwise leave it
   to manual admin assignment.

The whole step is wrapped in a try/except so a malformed environment
won't crash the app — it logs and continues. Mistakes are easy to fix
later through the admin UI.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import psycopg2

from config.settings import settings

from .audit import record as audit_record
from .cache import invalidate_user_cache, invalidate_all
from .db import get_cursor
from .registry import (
    PermissionRegistry,
    SUPER_ADMIN_ROLE_KEY,
    DEFAULT_ROLE_KEY,
    default_role_permission_keys,
)


def _parse_email_list(raw: str) -> list[str]:
    """Tolerantly parse a comma-separated email env var.

    Handles the common pitfalls of how shells / .env files / deploy
    platforms serialize string lists:
      - wrapping double or single quotes around the whole value
      - stray quotes around individual entries
      - extra whitespace anywhere
      - lower-cases for case-insensitive matching against users.email

    Examples that all parse to ['a@x.com', 'b@y.com']:
      a@x.com,b@y.com
      "a@x.com, b@y.com"
      'a@x.com, b@y.com'
      a@x.com, "b@y.com"
    """
    if not raw:
        return []
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in ('"', "'") and raw[0] == raw[-1]:
        raw = raw[1:-1]
    out: list[str] = []
    for part in raw.split(","):
        e = part.strip().strip('"').strip("'").strip().lower()
        if e:
            out.append(e)
    return out

logger = logging.getLogger(__name__)

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[1]
    / "database"
    / "migrations"
    / "020_create_rbac_tables.sql"
)


def _apply_migration() -> None:
    """Apply the idempotent RBAC migration.

    We do this in-process so deployments without a separate migration step
    still get the schema. Existing installations must also run the script,
    because CREATE TABLE IF NOT EXISTS does not add columns to an older table.
    """
    try:
        with get_cursor(dict_rows=False) as cur:
            sql = _MIGRATION_FILE.read_text()
            cur.execute(sql)
        logger.info("RBAC migration applied")
    except Exception as exc:
        logger.exception("failed to apply RBAC migration: %s", exc)
        raise


def _sync_permissions() -> None:
    perms = PermissionRegistry.all()
    if not perms:
        return
    with get_cursor(dict_rows=False) as cur:
        cur.executemany(
            """
            INSERT INTO permissions (key, resource, action, description, module, is_system)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (key) DO UPDATE
                SET resource = EXCLUDED.resource,
                    action = EXCLUDED.action,
                    description = EXCLUDED.description,
                    module = EXCLUDED.module,
                    is_system = TRUE
            """,
            [(p.key, p.resource, p.action, p.description, p.module) for p in perms],
        )
    logger.info("RBAC permissions synced (%d in registry)", len(perms))


def _ensure_super_admin_role() -> int:
    """Create the super_admin role if missing; ensure it holds every permission."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT role_id FROM roles WHERE key = %s",
            (SUPER_ADMIN_ROLE_KEY,),
        )
        row = cur.fetchone()
        if row:
            role_id = row["role_id"]
        else:
            cur.execute(
                """
                INSERT INTO roles (key, name, description, is_system)
                VALUES (%s, %s, %s, TRUE)
                RETURNING role_id
                """,
                (
                    SUPER_ADMIN_ROLE_KEY,
                    "Super Administrator",
                    "Full unrestricted access. Created and managed by the platform.",
                ),
            )
            role_id = cur.fetchone()["role_id"]
            audit_record(
                actor_user_id=None,
                action="role.bootstrap",
                target_type="role",
                target_id=role_id,
                metadata={"reason": "initial super_admin role"},
            )
        # Heal: super_admin holds every known permission. Using the registry
        # set here rather than a wildcard row keeps the relational model
        # consistent for reporting + audit.
        cur.execute(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT %s, p.permission_id FROM permissions p
            ON CONFLICT DO NOTHING
            """,
            (role_id,),
        )
    return role_id


def _ensure_default_role() -> int:
    """Create the default 'client' role if missing and return its id.

    Unlike super_admin, the permission set is seeded ONLY on first creation —
    after that the role is admin-owned, so customizations made through the
    admin UI survive restarts. New product permissions added later can be
    granted to the role from the UI (or by deleting it so it re-seeds).
    """
    with get_cursor() as cur:
        cur.execute("SELECT role_id FROM roles WHERE key = %s", (DEFAULT_ROLE_KEY,))
        row = cur.fetchone()
        if row:
            return row["role_id"]

        cur.execute(
            """
            INSERT INTO roles (key, name, description, is_system)
            VALUES (%s, %s, %s, TRUE)
            RETURNING role_id
            """,
            (
                DEFAULT_ROLE_KEY,
                "Client",
                "Default role for new users. Full access to every product "
                "workspace (Tally, QuickBooks, Zoho, Xero, SAP), documents, "
                "reports and AI — but not user / role administration.",
            ),
        )
        role_id = cur.fetchone()["role_id"]

        # Seed the product permission set (everything except the RBAC/identity
        # admin surface). ANY(%s) matches the registry-derived key list.
        cur.execute(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT %s, p.permission_id FROM permissions p
            WHERE p.key = ANY(%s)
            ON CONFLICT DO NOTHING
            """,
            (role_id, default_role_permission_keys()),
        )
        audit_record(
            actor_user_id=None,
            action="role.bootstrap",
            target_type="role",
            target_id=role_id,
            metadata={"reason": "initial default client role"},
        )
    logger.info("created default '%s' role (%d)", DEFAULT_ROLE_KEY, role_id)
    return role_id


def get_default_role_id() -> int | None:
    """Look up the default client role_id without creating it."""
    with get_cursor() as cur:
        cur.execute("SELECT role_id FROM roles WHERE key = %s", (DEFAULT_ROLE_KEY,))
        row = cur.fetchone()
        return row["role_id"] if row else None


def assign_default_role(user_id: str) -> bool:
    """Give a user the default client role. Idempotent and safe to call on
    every signup / login. Returns True if a role row was newly inserted.

    Never raises on the 'role missing' path so auth flows don't break — it
    just logs and returns False (bootstrap will backfill on next restart).
    """
    role_id = get_default_role_id()
    if role_id is None:
        logger.warning(
            "default role '%s' missing; could not assign to user %s",
            DEFAULT_ROLE_KEY, user_id,
        )
        return False
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO user_roles (user_id, role_id, assigned_by) "
            "VALUES (%s, %s, NULL) ON CONFLICT DO NOTHING",
            (user_id, role_id),
        )
        inserted = cur.rowcount > 0
    if inserted:
        invalidate_user_cache(user_id)
        audit_record(
            actor_user_id=None,
            action="user.roles.assign",
            target_type="user",
            target_id=user_id,
            new_value={"role_key": DEFAULT_ROLE_KEY},
            metadata={"source": "default role on signup"},
        )
    return inserted


def _backfill_roleless_users(role_id: int) -> None:
    """Assign the default role to every existing user who has NO roles at all.

    Fixes clients who signed up before default-role assignment existed and
    were stranded with zero permissions (403 on every gated endpoint). Users
    who already hold any role — including super_admins — are left untouched.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_roles (user_id, role_id, assigned_by)
            SELECT u.user_id, %s, NULL FROM users u
            WHERE NOT EXISTS (
                SELECT 1 FROM user_roles ur WHERE ur.user_id = u.user_id
            )
            ON CONFLICT DO NOTHING
            """,
            (role_id,),
        )
        n = cur.rowcount
    if n:
        # Caches are per-user; the simplest correct thing after a bulk grant
        # is to drop the whole cache so the next request recomputes.
        invalidate_all()
        logger.info("backfilled default role onto %d role-less user(s)", n)


def _promote_env_super_admins(role_id: int) -> None:
    raw = os.environ.get("SUPER_ADMIN_EMAILS", "")
    emails = _parse_email_list(raw)
    if not emails:
        return
    logger.info("SUPER_ADMIN_EMAILS resolved to %d email(s): %s", len(emails), emails)
    with get_cursor() as cur:
        cur.execute(
            "SELECT user_id, email FROM users WHERE LOWER(email) = ANY(%s)",
            (emails,),
        )
        users = cur.fetchall()
        if not users:
            logger.info(
                "SUPER_ADMIN_EMAILS set but no matching users in DB yet (%s)", emails
            )
            return
        matched = {u["email"].lower() for u in users}
        unmatched = [e for e in emails if e not in matched]
        if unmatched:
            logger.info(
                "SUPER_ADMIN_EMAILS contained unknown emails (no user row): %s", unmatched
            )
        for u in users:
            cur.execute(
                """
                INSERT INTO user_roles (user_id, role_id, assigned_by)
                VALUES (%s, %s, NULL)
                ON CONFLICT DO NOTHING
                """,
                (u["user_id"], role_id),
            )
            # Drop any cached permission set so this user sees super-admin
            # on their next request instead of waiting for the 5-minute TTL.
            invalidate_user_cache(u["user_id"])
            audit_record(
                actor_user_id=None,
                action="user.roles.assign",
                target_type="user",
                target_id=u["user_id"],
                new_value={"role_key": SUPER_ADMIN_ROLE_KEY},
                metadata={"source": "SUPER_ADMIN_EMAILS env"},
            )
        logger.info(
            "promoted %d user(s) to super_admin via SUPER_ADMIN_EMAILS", len(users)
        )


def _promote_first_user_if_no_admin(role_id: int) -> None:
    """If no super-admin exists and there is exactly one user, promote them.

    This makes the very first signup the bootstrap admin without requiring
    the operator to do anything. If there are 0 or 2+ users, we no-op and
    expect the operator to use SUPER_ADMIN_EMAILS.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM user_roles ur
            JOIN roles r ON r.role_id = ur.role_id
            WHERE r.key = %s
            """,
            (SUPER_ADMIN_ROLE_KEY,),
        )
        if cur.fetchone()["n"] > 0:
            return
        cur.execute("SELECT user_id FROM users")
        rows = cur.fetchall()
        if len(rows) != 1:
            if rows:
                logger.warning(
                    "No super-admin exists and there are %d users. "
                    "Set SUPER_ADMIN_EMAILS or assign super_admin manually.",
                    len(rows),
                )
            return
        user_id = rows[0]["user_id"]
        cur.execute(
            "INSERT INTO user_roles (user_id, role_id, assigned_by) VALUES (%s, %s, NULL) "
            "ON CONFLICT DO NOTHING",
            (user_id, role_id),
        )
        audit_record(
            actor_user_id=None,
            action="user.roles.assign",
            target_type="user",
            target_id=user_id,
            new_value={"role_key": SUPER_ADMIN_ROLE_KEY},
            metadata={"source": "first-user bootstrap"},
        )
        logger.info("promoted sole user %s to super_admin (first-user bootstrap)", user_id)


def bootstrap_rbac() -> None:
    """Top-level bootstrap. Safe to call on every app startup."""
    try:
        _apply_migration()
        _sync_permissions()
        role_id = _ensure_super_admin_role()
        default_role_id = _ensure_default_role()
        _promote_env_super_admins(role_id)
        _promote_first_user_if_no_admin(role_id)
        # Backfill AFTER first-user promotion so the bootstrap admin keeps
        # super_admin (they now hold a role, so the backfill skips them).
        _backfill_roleless_users(default_role_id)
    except Exception as exc:  # pragma: no cover
        # Logging + reraising would crash the worker; for resilience we log
        # loudly and let the app come up. Admin endpoints will surface DB
        # errors clearly if the migration truly failed.
        logger.exception("RBAC bootstrap failed: %s", exc)


def env_super_admin_emails() -> list[str]:
    """Re-parses SUPER_ADMIN_EMAILS — exposed for the promote-me endpoint."""
    return _parse_email_list(os.environ.get("SUPER_ADMIN_EMAILS", ""))


def get_super_admin_role_id() -> int | None:
    """Look up the super_admin role_id without creating it."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT role_id FROM roles WHERE key = %s", (SUPER_ADMIN_ROLE_KEY,)
        )
        row = cur.fetchone()
        return row["role_id"] if row else None


def promote_user_if_env_listed(
    *, user_id: str, jwt_email: str | None
) -> dict:
    """Promote a user to super_admin if their JWT email is in SUPER_ADMIN_EMAILS.

    Designed for the /promote-me endpoint. Returns a dict with the outcome:
        {"promoted": bool, "matched_email": Optional[str], "reason": str}

    Also backfills users.email from the JWT claim when the DB column is
    NULL — that's the actual underlying bug for Google-signed-in users
    (add_new_user never set email, so the env-driven match at startup
    could never succeed for them).
    """
    if not jwt_email:
        return {"promoted": False, "matched_email": None, "reason": "no_email_in_jwt"}
    needle = jwt_email.strip().lower()
    allowed = env_super_admin_emails()
    if needle not in allowed:
        return {
            "promoted": False,
            "matched_email": None,
            "reason": "email_not_in_super_admin_emails",
        }

    role_id = get_super_admin_role_id()
    if role_id is None:
        return {
            "promoted": False,
            "matched_email": needle,
            "reason": "super_admin_role_missing",
        }

    with get_cursor() as cur:
        # Backfill email if the row has it as NULL — fixes the underlying bug
        # for Google-signed-in users so future bootstraps also work for them.
        cur.execute(
            "UPDATE users SET email = COALESCE(email, %s) WHERE user_id = %s",
            (needle, user_id),
        )
        cur.execute(
            """
            INSERT INTO user_roles (user_id, role_id, assigned_by)
            VALUES (%s, %s, NULL)
            ON CONFLICT DO NOTHING
            """,
            (user_id, role_id),
        )
    invalidate_user_cache(user_id)
    audit_record(
        actor_user_id=user_id,
        action="user.roles.assign",
        target_type="user",
        target_id=user_id,
        new_value={"role_key": SUPER_ADMIN_ROLE_KEY},
        metadata={"source": "promote-me endpoint (JWT email match)"},
    )
    return {"promoted": True, "matched_email": needle, "reason": "ok"}
