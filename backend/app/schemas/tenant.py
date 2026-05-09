"""Pydantic schemas for Tenant management."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TenantSettings(BaseModel):
    """Tenant settings stored in JSONB."""

    signup_mode: Literal["open", "invite_only"] = Field(
        default="open",
        description="'open' allows self-registration, 'invite_only' requires pending_users invitation",
    )
    allowed_email_domains: List[str] = Field(
        default_factory=list,
        description="If non-empty, only these email domains can self-signup",
    )
    max_users: Optional[int] = Field(
        default=None,
        description="Maximum users allowed in tenant (None = unlimited)",
    )
    features: Dict[str, Any] = Field(
        default_factory=dict,
        description="Feature flags for this tenant",
    )


class TenantCreate(BaseModel):
    """Schema for creating a new tenant."""

    name: str = Field(..., min_length=1, max_length=255, description="Display name for the tenant")
    slug: str = Field(
        ...,
        min_length=2,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$",
        description="URL-safe identifier (lowercase alphanumeric with hyphens)",
    )
    settings: Optional[TenantSettings] = Field(
        default=None,
        description="Initial tenant settings",
    )


class TenantUpdate(BaseModel):
    """Schema for updating a tenant."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    is_active: Optional[bool] = None
    settings: Optional[TenantSettings] = None


class TenantResponse(BaseModel):
    """Schema for tenant API responses."""

    id: UUID
    name: str
    slug: str
    is_active: bool
    settings: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TenantListResponse(BaseModel):
    """Schema for paginated tenant list response."""

    items: List[TenantResponse]
    total: int
    skip: int
    limit: int
    has_more: bool
