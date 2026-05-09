"""Pydantic schemas for user onboarding flows."""
import re
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.tenant import TenantResponse


class UserResponse(BaseModel):
    """User information in onboarding responses."""

    id: str
    email: str
    name: Optional[str] = None
    role: str
    is_active: bool
    auth_provider: str

    class Config:
        from_attributes = True


# --- Email Check ---


class EmailCheckRequest(BaseModel):
    """Request to check email availability."""

    email: EmailStr = Field(..., description="Email address to check")


class EmailCheckResponse(BaseModel):
    """Response for email availability check."""

    available: bool = Field(..., description="Whether email is available for registration")
    has_pending_invitation: bool = Field(
        ...,
        description="Whether user has a pending invitation to an organization",
    )
    org_name: Optional[str] = Field(
        None,
        description="Organization name if user has pending invitation",
    )
    tenant_slug: Optional[str] = Field(
        None,
        description="Organization slug if user has pending invitation",
    )


# --- Registration ---


class RegisterRequest(BaseModel):
    """Request to register a new user."""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, description="User password (min 8 characters)")
    name: str = Field(..., min_length=1, max_length=255, description="User display name")
    org_name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="Organization name (required for self-service, ignored for invited users)",
    )
    org_slug: Optional[str] = Field(
        None,
        min_length=2,
        max_length=63,
        description="Organization slug (auto-generated from org_name if not provided)",
    )

    @field_validator("org_slug")
    @classmethod
    def validate_slug_format(cls, v: Optional[str]) -> Optional[str]:
        """Validate slug format: lowercase alphanumeric with hyphens."""
        if v is None:
            return v
        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", v):
            raise ValueError("Slug must be lowercase alphanumeric with hyphens, not starting/ending with hyphen")
        return v


class RegisterResponse(BaseModel):
    """Response after successful registration."""

    user: UserResponse
    tenant: TenantResponse
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type (always 'bearer')")
    expires_in: int = Field(..., description="Token expiration in seconds")


# --- Organization Creation ---


class CreateOrgRequest(BaseModel):
    """Request to create a new organization (for authenticated users)."""

    name: str = Field(..., min_length=1, max_length=255, description="Organization name")
    slug: str = Field(
        ...,
        min_length=2,
        max_length=63,
        description="URL-safe organization identifier",
    )

    @field_validator("slug")
    @classmethod
    def validate_slug_format(cls, v: str) -> str:
        """Validate slug format: lowercase alphanumeric with hyphens."""
        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", v):
            raise ValueError("Slug must be lowercase alphanumeric with hyphens, not starting/ending with hyphen")
        return v


class CreateOrgResponse(BaseModel):
    """Response after creating an organization."""

    tenant: TenantResponse
    role: str = Field(default="owner", description="User's role in the new organization")


# --- Slug Check ---


class SlugCheckResponse(BaseModel):
    """Response for slug availability check."""

    available: bool = Field(..., description="Whether slug is available")
    suggestions: List[str] = Field(
        default_factory=list,
        description="Alternative slug suggestions if not available",
    )
