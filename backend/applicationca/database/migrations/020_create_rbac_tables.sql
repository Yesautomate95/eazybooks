-- Migration: 020_create_rbac_tables.sql
-- Description: Role-Based Access Control (RBAC) — users/roles/permissions, mappings,
--              direct-grant overrides, audit log, and a soft-deactivate flag on users.
-- Author: EazyBooks Platform
-- Date: 2026-06-01
--
-- Forward-compatible: schema accommodates future multi-tenancy by adding an
-- organization_id column to roles / user_roles / user_permissions without
-- breaking existing data (null = global scope).

-- ============================================
-- 1. permissions — central registry of every permission key the system knows.
--    Populated at app startup from the in-code PermissionRegistry; user-defined
--    additions go through the admin UI and are flagged is_system=false.
-- ============================================
CREATE TABLE IF NOT EXISTS permissions (
    permission_id SERIAL PRIMARY KEY,
    key VARCHAR(120) UNIQUE NOT NULL,            -- e.g. "users.read"
    resource VARCHAR(60) NOT NULL,               -- e.g. "users"
    action VARCHAR(60) NOT NULL,                 -- e.g. "read"
    description TEXT NOT NULL DEFAULT '',
    module VARCHAR(60) NOT NULL DEFAULT 'General', -- UI grouping
    is_system BOOLEAN NOT NULL DEFAULT FALSE,    -- system perms can't be deleted
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Upgrade installations that created an older permissions table before the
-- resource/action metadata was introduced.
ALTER TABLE permissions ADD COLUMN IF NOT EXISTS resource VARCHAR(60) NOT NULL DEFAULT 'General';
ALTER TABLE permissions ADD COLUMN IF NOT EXISTS action VARCHAR(60) NOT NULL DEFAULT 'read';
ALTER TABLE permissions ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
ALTER TABLE permissions ADD COLUMN IF NOT EXISTS module VARCHAR(60) NOT NULL DEFAULT 'General';
ALTER TABLE permissions ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_permissions_module ON permissions(module);
CREATE INDEX IF NOT EXISTS idx_permissions_resource ON permissions(resource);

-- ============================================
-- 2. roles — named bundles of permissions assignable to users.
--    super_admin is seeded by the bootstrap script with is_system=true.
-- ============================================
CREATE TABLE IF NOT EXISTS roles (
    role_id SERIAL PRIMARY KEY,
    key VARCHAR(80) UNIQUE NOT NULL,             -- e.g. "super_admin"
    name VARCHAR(120) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_system BOOLEAN NOT NULL DEFAULT FALSE,    -- system roles can't be deleted or renamed
    created_by VARCHAR(255),                     -- user_id (FK soft — users may be removed)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_roles_is_system ON roles(is_system);

-- ============================================
-- 3. role_permissions — M:N between roles and permissions.
-- ============================================
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_by VARCHAR(255),
    PRIMARY KEY (role_id, permission_id)
);
CREATE INDEX IF NOT EXISTS idx_role_permissions_role ON role_permissions(role_id);

-- ============================================
-- 4. user_roles — M:N between users and roles.
-- ============================================
CREATE TABLE IF NOT EXISTS user_roles (
    user_id VARCHAR(255) NOT NULL,
    role_id INTEGER NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    assigned_by VARCHAR(255),
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role_id);

-- ============================================
-- 5. user_permissions — direct grants/denies that override role-derived perms.
--    is_grant=true ADDS the permission; is_grant=false explicitly DENIES it
--    (a deny overrides any role-derived grant — important for compliance).
-- ============================================
CREATE TABLE IF NOT EXISTS user_permissions (
    user_id VARCHAR(255) NOT NULL,
    permission_id INTEGER NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    is_grant BOOLEAN NOT NULL DEFAULT TRUE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_by VARCHAR(255),
    PRIMARY KEY (user_id, permission_id)
);
CREATE INDEX IF NOT EXISTS idx_user_permissions_user ON user_permissions(user_id);

-- Upgrade older RBAC tables that were created without audit metadata.
ALTER TABLE roles ADD COLUMN IF NOT EXISTS created_by VARCHAR(255);
ALTER TABLE roles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE role_permissions ADD COLUMN IF NOT EXISTS granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE role_permissions ADD COLUMN IF NOT EXISTS granted_by VARCHAR(255);
ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS assigned_by VARCHAR(255);
ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS granted_by VARCHAR(255);

-- ============================================
-- 6. Soft-deactivate flag on existing users table.
-- ============================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'is_active'
    ) THEN
        ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'deactivated_at'
    ) THEN
        ALTER TABLE users ADD COLUMN deactivated_at TIMESTAMPTZ;
    END IF;
END $$;

-- ============================================
-- 7. rbac_audit_log — append-only ledger of every RBAC mutation + auth event.
--    previous_value / new_value capture before-and-after for forensics.
-- ============================================
CREATE TABLE IF NOT EXISTS rbac_audit_log (
    audit_id BIGSERIAL PRIMARY KEY,
    actor_user_id VARCHAR(255),                  -- null = system / pre-auth event
    action VARCHAR(120) NOT NULL,                -- e.g. "role.create", "user.role.assign"
    target_type VARCHAR(40),                     -- "user" | "role" | "permission" | "auth"
    target_id VARCHAR(255),
    previous_value JSONB,
    new_value JSONB,
    metadata JSONB,                              -- ip, user_agent, etc.
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON rbac_audit_log(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target ON rbac_audit_log(target_type, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON rbac_audit_log(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON rbac_audit_log(created_at DESC);

-- ============================================
-- 8. Self-protection: marker row identifying the "super_admin" role key.
--    The application layer enforces that the last super_admin cannot be
--    demoted/deactivated; this comment is here so future readers know
--    the invariant lives in code (rbac/service.py), not in DB constraints.
-- ============================================
