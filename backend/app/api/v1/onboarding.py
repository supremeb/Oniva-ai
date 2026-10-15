"""Onboarding API endpoints for user registration and organization creation.

Public endpoints (no auth required):
- POST /check-email - Check if email is available for registration
- POST /register - Register a new user (self-service or invited)
- GET /check-slug/{slug} - Check if organization slug is available

Protected endpoints (auth required):
- POST /create-org - Create a new organization for existing user
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.core.database import get_db, get_db_no_tenant
from app.schemas.onboarding import (
    CreateOrgRequest,
    CreateOrgResponse,
    EmailCheckRequest,
    EmailCheckResponse,
    RegisterRequest,
    RegisterResponse,
    SlugCheckResponse,
)
from app.schemas.tenant import TenantResponse
from app.services.onboarding import OnboardingService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/check-email", response_model=EmailCheckResponse)
async def check_email(
    request: EmailCheckRequest,
    db: AsyncSession = Depends(get_db_no_tenant),
):
    """
    Check if email is available for registration.

    Public endpoint - no authentication required.
    Uses get_db_no_tenant because no tenant context exists before signup.

    Returns:
    - available: Whether email can be used for registration
    - has_pending_invitation: Whether user has pending invitation
    - org_name: Organization name if invitation exists
    - tenant_slug: Organization slug if invitation exists
    """
    service = OnboardingService(db)
    return await service.check_email_availability(request.email)


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db_no_tenant),
):
    """
    Register a new user.

    Public endpoint - no authentication required.
    Uses get_db_no_tenant because no tenant context exists before signup.

    Supports two flows:
    1. **Invited user**: Has pending invitation → joins that org with pre-assigned role
    2. **Self-service**: No invitation + org_name provided → creates new org, becomes owner

    If no invitation and no org_name provided, returns 400 error.

    Returns user info, tenant info, and JWT access token on success.
    """
    service = OnboardingService(db)
    try:
        result = await service.register_user(
            email=request.email,
            password=request.password,
            name=request.name,
            org_name=request.org_name,
            org_slug=request.org_slug,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/create-org",
    response_model=CreateOrgResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_org(
    request: CreateOrgRequest,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_no_tenant)  # tenant insert: pre-tenant, system role,
):
    """
    Create a new organization for existing user.

    Requires authentication. The authenticated user becomes owner of the new org.

    Note: This changes the user's primary tenant to the new organization.
    """
    service = OnboardingService(db)
    try:
        tenant = await service.create_organization(
            user_id=UUID(user.id),
            name=request.name,
            slug=request.slug,
        )
        return CreateOrgResponse(
            tenant=TenantResponse.model_validate(tenant),
            role="owner",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/check-slug/{slug}", response_model=SlugCheckResponse)
async def check_slug(
    slug: str,
    db: AsyncSession = Depends(get_db_no_tenant),
):
    """
    Check if organization slug is available.

    Public endpoint - no authentication required.
    Uses get_db_no_tenant because no tenant context exists before signup.

    Returns:
    - available: Whether slug is available
    - suggestions: Alternative slug suggestions if not available
    """
    service = OnboardingService(db)
    return await service.check_slug_availability(slug)
