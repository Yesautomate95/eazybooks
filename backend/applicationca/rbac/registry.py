"""Permission registry — the in-process catalog of every permission key.

Each module registers its permissions at import time via
`register_permission(...)`. On app startup, `bootstrap.sync_permissions()`
upserts the registry into the `permissions` table so that admin UIs see
every permission a developer has declared without manual DB seeding.

Permission keys follow the convention `<resource>.<action>` and are matched
exactly. Super-admins implicitly hold the sentinel `*` permission which
short-circuits every check.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

# Sentinel permission key — only super-admins implicitly hold this.
# It short-circuits all permission checks via User.has_permission().
SUPERADMIN_PERMISSION = "*"

# Reserved system role key used by bootstrap + self-protection logic.
SUPER_ADMIN_ROLE_KEY = "super_admin"

# Default role assigned to every new user at signup. Holds the full product
# surface (all workspaces, documents, reports, AI) but NOT the RBAC / identity
# administration permissions — so clients can use the platform without being
# super-admins. Seeded once by bootstrap; admins may customize it afterwards.
DEFAULT_ROLE_KEY = "client"

# Resources whose permissions are administrative and therefore excluded from
# the default client role (RBAC / identity surface).
_ADMIN_RESOURCES = frozenset({"users", "roles", "permissions", "audit"})


@dataclass(frozen=True)
class PermissionDef:
    key: str
    resource: str
    action: str
    description: str
    module: str
    is_system: bool = True  # registry entries are system perms by default


class _Registry:
    """Singleton store keyed by permission key."""
    def __init__(self) -> None:
        self._perms: Dict[str, PermissionDef] = {}

    def register(self, perm: PermissionDef) -> None:
        existing = self._perms.get(perm.key)
        if existing is not None and existing != perm:
            # Reject conflicting redefinitions early — these are almost
            # certainly programmer errors and would silently take the
            # last-registered shape.
            raise ValueError(
                f"Permission '{perm.key}' is already registered with a different definition. "
                f"Existing: {existing}, attempted: {perm}"
            )
        self._perms[perm.key] = perm

    def all(self) -> List[PermissionDef]:
        return list(self._perms.values())

    def get(self, key: str) -> PermissionDef | None:
        return self._perms.get(key)


PermissionRegistry = _Registry()


def register_permission(
    key: str,
    *,
    resource: str | None = None,
    action: str | None = None,
    description: str = "",
    module: str = "General",
) -> str:
    """Register a permission and return its key.

    `resource` and `action` default to the two halves of `key` so the typical
    call is just `register_permission("users.read", description="...", module="Identity")`.
    """
    if "." not in key:
        raise ValueError(f"Permission key must follow '<resource>.<action>' shape (got {key!r})")
    parsed_resource, parsed_action = key.split(".", 1)
    perm = PermissionDef(
        key=key,
        resource=resource or parsed_resource,
        action=action or parsed_action,
        description=description,
        module=module,
        is_system=True,
    )
    PermissionRegistry.register(perm)
    return key


# ───────────────────────────────────────────────────────────────────────
# Built-in system permissions — every module the platform ships with.
# Adding a new module only requires calling register_permission() at its
# import time; it will appear in the admin UI automatically.
# ───────────────────────────────────────────────────────────────────────

# Identity — users & auth
for action, desc in [
    ("read", "View users and their roles"),
    ("create", "Create new users"),
    ("update", "Edit user profile and status"),
    ("delete", "Deactivate or remove users"),
    ("assign_roles", "Assign or revoke roles on users"),
    ("reset_password", "Reset a user's password"),
]:
    register_permission(f"users.{action}", description=desc, module="Identity")

# RBAC — roles & permissions
for action, desc in [
    ("read", "View roles"),
    ("create", "Create new roles"),
    ("update", "Edit role name / description / permissions"),
    ("delete", "Delete roles"),
    ("clone", "Clone an existing role"),
]:
    register_permission(f"roles.{action}", description=desc, module="Identity")

for action, desc in [
    ("read", "View the permission registry"),
    ("create", "Register custom permissions"),
    ("update", "Edit custom permission metadata"),
    ("delete", "Remove custom permissions"),
]:
    register_permission(f"permissions.{action}", description=desc, module="Identity")

register_permission("audit.read", description="Read the RBAC audit log", module="Identity")

# Clients
for action, desc in [
    ("read", "View clients"),
    ("create", "Create new clients"),
    ("update", "Edit client details"),
    ("delete", "Delete clients"),
]:
    register_permission(f"clients.{action}", description=desc, module="Clients")

# Documents / Vault
for action, desc in [
    ("read", "View documents in the vault"),
    ("create", "Upload documents"),
    ("update", "Rename or move documents"),
    ("delete", "Delete documents"),
]:
    register_permission(f"documents.{action}", description=desc, module="Documents")

# Invoices
for action, desc in [
    ("read", "View invoices"),
    ("create", "Create invoices"),
    ("update", "Edit invoices"),
    ("delete", "Delete invoices"),
]:
    register_permission(f"invoices.{action}", description=desc, module="Accounting")

# Bookkeeping / Journal entries
for action, desc in [
    ("read", "View bookkeeping entries"),
    ("create", "Create bookkeeping entries"),
    ("update", "Edit bookkeeping entries"),
    ("delete", "Delete bookkeeping entries"),
]:
    register_permission(f"bookkeeping.{action}", description=desc, module="Accounting")

# Reports / MIS
register_permission("reports.read", description="View MIS reports", module="Reports")
register_permission("reports.export", description="Export reports to PDF / Excel", module="Reports")

# Tally workspace
for action, desc in [
    ("read", "View Tally workspace"),
    ("push", "Push entries to Tally"),
    ("sync", "Sync Tally master data"),
]:
    register_permission(f"tally.{action}", description=desc, module="Tally")

# QuickBooks workspace
for action, desc in [
    ("read", "View QuickBooks workspace"),
    ("push", "Push entries to QuickBooks"),
    ("connect", "Connect / disconnect the QuickBooks OAuth integration"),
]:
    register_permission(f"quickbooks.{action}", description=desc, module="QuickBooks")

# Zoho Books workspace
for action, desc in [
    ("read", "View Zoho Books workspace"),
    ("push", "Push entries to Zoho Books"),
    ("connect", "Connect / disconnect the Zoho OAuth integration"),
]:
    register_permission(f"zoho.{action}", description=desc, module="Zoho")

# Xero workspace
for action, desc in [
    ("read", "View Xero workspace"),
    ("push", "Push entries to Xero"),
    ("connect", "Connect / disconnect the Xero OAuth integration"),
]:
    register_permission(f"xero.{action}", description=desc, module="Xero")

# SAP S/4HANA Cloud workspace
for action, desc in [
    ("read", "View SAP workspace"),
    ("push", "Push entries to SAP S/4HANA"),
    ("connect", "Connect / disconnect the SAP OAuth integration"),
]:
    register_permission(f"sap.{action}", description=desc, module="SAP")

# AI / Advisor
register_permission("advisor.use", description="Use the AI advisor chat", module="AI")
register_permission("chat.use", description="Use module-specific AI chats (Tally/QB/Zoho/Xero/SAP)", module="AI")


def default_role_permission_keys() -> List[str]:
    """Permission keys granted to the default client role on first seed.

    Everything in the registry except the administrative RBAC / identity
    surface (users, roles, permissions, audit). This is the set a normal
    client/CA user needs to use every product workspace without being able
    to manage other users or roles.
    """
    return [p.key for p in PermissionRegistry.all() if p.resource not in _ADMIN_RESOURCES]
