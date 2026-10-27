"""Role-Based Access Control (RBAC) module.

Public surface used by the rest of the codebase:

    from applicationca.rbac import (
        register_permission,            # register a permission at import time
        get_current_user,               # FastAPI dependency
        require_permission,             # FastAPI dependency factory
        require_any_permission,         # FastAPI dependency factory (OR)
        invalidate_user_cache,          # call after mutating user/role perms
        SUPERADMIN_PERMISSION,          # the sentinel wildcard "*"
    )

Endpoints are exposed via `router` (mounted at /ca/rbac).
Bootstrap runs at app startup (see `applicationca.rbac.bootstrap.bootstrap_rbac`).
"""
from .registry import (
    register_permission,
    PermissionRegistry,
    SUPERADMIN_PERMISSION,
    SUPER_ADMIN_ROLE_KEY,
)
from .deps import (
    get_current_user,
    get_optional_user,
    require_permission,
    require_any_permission,
    require_all_permissions,
)
from .cache import invalidate_user_cache, invalidate_all
from .controller import router

__all__ = [
    "register_permission",
    "PermissionRegistry",
    "SUPERADMIN_PERMISSION",
    "SUPER_ADMIN_ROLE_KEY",
    "get_current_user",
    "get_optional_user",
    "require_permission",
    "require_any_permission",
    "require_all_permissions",
    "invalidate_user_cache",
    "invalidate_all",
    "router",
]
