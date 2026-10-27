"""Pydantic request/response schemas for the RBAC controller."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


def _empty_str_to_none(value):
    """Coerce empty/whitespace strings to None.

    The frontend often round-trips DB-null fields as empty strings (e.g.
    setEmail(user.email || "")). Without this coercion, PATCH endpoints
    where the field type is EmailStr fail validation when the user didn't
    actually touch the field — they tried to "update" null to "", which
    isn't a valid email.
    """
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


# ── Permissions ──────────────────────────────────────────────────────────
class PermissionCreate(BaseModel):
    key: str = Field(..., min_length=3, max_length=120)
    resource: str = Field(..., min_length=1, max_length=60)
    action: str = Field(..., min_length=1, max_length=60)
    description: str = ""
    module: str = "Custom"


# ── Roles ────────────────────────────────────────────────────────────────
class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    key: Optional[str] = None
    permission_keys: List[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class RolePermissionsSet(BaseModel):
    permission_keys: List[str]


class RoleClone(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=120)
    new_key: Optional[str] = None


# ── Users ────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    email: EmailStr
    username: str = ""
    password: Optional[str] = None
    role_keys: List[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None

    # Treat "" as "field omitted" rather than "blank email" — the frontend
    # round-trips DB-null emails as empty strings, and we don't want a
    # role-update to fail because the email field happens to be cleared.
    @field_validator("email", "username", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return _empty_str_to_none(v)


class UserRolesSet(BaseModel):
    role_keys: List[str]


class PasswordReset(BaseModel):
    new_password: Optional[str] = None


class DirectPermissionEntry(BaseModel):
    permission_key: str = Field(..., min_length=3, max_length=120)
    is_grant: bool = True


class UserDirectPermissionsSet(BaseModel):
    entries: List[DirectPermissionEntry] = Field(default_factory=list)
