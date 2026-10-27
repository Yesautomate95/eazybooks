"""In-process domain objects used by deps + services.

These are intentionally simple — no ORM, no validation — they just carry
resolved auth state through the request lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

from .registry import SUPERADMIN_PERMISSION


@dataclass
class User:
    """Authenticated user with resolved roles + effective permissions."""
    user_id: str
    email: str
    username: str = ""
    is_active: bool = True
    roles: List[dict] = field(default_factory=list)
    # Effective permission set (already accounting for role membership +
    # direct grants - direct denies). Super-admins hold {SUPERADMIN_PERMISSION}.
    permissions: Set[str] = field(default_factory=set)

    def has_permission(self, key: str) -> bool:
        if not self.is_active:
            return False
        if SUPERADMIN_PERMISSION in self.permissions:
            return True
        return key in self.permissions

    def has_any(self, keys) -> bool:
        return any(self.has_permission(k) for k in keys)

    def has_all(self, keys) -> bool:
        return all(self.has_permission(k) for k in keys)

    @property
    def is_super_admin(self) -> bool:
        return SUPERADMIN_PERMISSION in self.permissions

    def to_public_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "username": self.username,
            "is_active": self.is_active,
            "roles": self.roles,
            "permissions": sorted(self.permissions),
            "is_super_admin": self.is_super_admin,
        }
