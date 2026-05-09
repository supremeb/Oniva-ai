"""Onboarding service for user registration and organization creation.

This service handles:
- Self-service signup with organization creation (SaaS model)
- Invited user signup via pending_users (Enterprise model)
- Email and slug availability checking
- JWT token generation for new registrations

Supports hybrid deployment where each tenant configures signup_mode:
- 'open': Anyone can self-register and create an org
- 'invite_only': Users must be invited via pending_users
"""
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import create_access_token
from app.config import get_settings
from app.models.pending_user import PendingUser
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.onboarding import (
    EmailCheckResponse,
    RegisterResponse,
    SlugCheckResponse,
    UserResponse,
)
from app.schemas.tenant import TenantResponse
from app.services.email_service import get_email_service
from app.services.user_management import UserManagementService

logger = logging.getLogger(__name__)
settings = get_settings()


class OnboardingService:
    """
    Service for user onboarding flows.

    Handles both self-service and invitation-based registration,
    with support for hybrid SaaS/Enterprise deployment models.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_service = UserManagementService(db)
        self.email_service = get_email_service()

    # =========================================
    # Email Availability
    # =========================================

    async def check_email_availability(self, email: str) -> EmailCheckResponse:
        """
        Check if email is available for registration.

        Args:
            email: Email address to check

        Returns:
            EmailCheckResponse with availability status and invitation info
        """
        email = email.lower().strip()

        # Check if email exists as active user in any tenant
        existing_user_result = await self.db.execute(
            select(User).where(
                User.email == email,
                User.deleted_at.is_(None),
            )
        )
        existing_user = existing_user_result.scalar_one_or_none()

        if existing_user:
            return EmailCheckResponse(
                available=False,
                has_pending_invitation=False,
            )

        # Check for pending invitation
        pending_result = await self.db.execute(
            select(PendingUser)
            .options(selectinload(PendingUser.tenant))
            .where(PendingUser.email == email)
        )
        pending_user = pending_result.scalar_one_or_none()

        if pending_user:
            return EmailCheckResponse(
                available=True,  # Can register, will join pending org
                has_pending_invitation=True,
                org_name=pending_user.tenant.name,
                tenant_slug=pending_user.tenant.slug,
            )

        return EmailCheckResponse(
            available=True,
            has_pending_invitation=False,
        )

    # =========================================
    # User Registration
    # =========================================

    async def register_user(
        self,
        email: str,
        password: str,
        name: str,
        org_name: Optional[str] = None,
        org_slug: Optional[str] = None,
    ) -> RegisterResponse:
        """
        Register a new user.

        Supports two flows:
        1. Invited user: Has pending_user record, joins existing org with pre-assigned role
        2. Self-service: No invitation, creates new org and becomes owner

        Args:
            email: User email address
            password: User password (validated against security requirements)
            name: User display name
            org_name: Organization name (required for self-service)
            org_slug: Organization slug (auto-generated if not provided)

        Returns:
            RegisterResponse with user, tenant, and access token

        Raises:
            ValueError: If validation fails or registration not allowed
        """
        email = email.lower().strip()

        # Validate password
        is_valid, error_msg = self.user_service.validate_password(password)
        if not is_valid:
            raise ValueError(f"Password validation failed: {error_msg}")

        # Check for pending invitation
        pending_result = await self.db.execute(
            select(PendingUser)
            .options(selectinload(PendingUser.tenant))
            .where(PendingUser.email == email)
        )
        pending_user = pending_result.scalar_one_or_none()

        if pending_user:
            # Flow 1: Invited user - join existing org
            return await self._register_invited_user(pending_user, password, name)

        # No invitation - must create new org
        if not org_name:
            raise ValueError(
                "No pending invitation found. "
                "Please provide an organization name to create a new account, "
                "or contact your admin to be invited."
            )

        # Generate slug if not provided
        if not org_slug:
            org_slug = await self._generate_slug(org_name)

        # Check slug availability
        slug_available = await self._check_slug(org_slug)
        if not slug_available:
            raise ValueError(f"Organization slug '{org_slug}' is already taken.")

        # Flow 2: Self-service - create org and user
        return await self._register_with_new_org(email, password, name, org_name, org_slug)

    async def _register_invited_user(
        self,
        pending_user: PendingUser,
        password: str,
        name: str,
    ) -> RegisterResponse:
        """Create user from pending invitation."""
        tenant = pending_user.tenant

        # Create user with pre-assigned role
        hashed_password = self.user_service.hash_password(password)
        user = User(
            tenant_id=tenant.id,
            email=pending_user.email,
            name=name,
            hashed_password=hashed_password,
            role=pending_user.role,
            auth_provider="local",
            is_active=True,
            added_by=pending_user.added_by,
            added_at=datetime.utcnow(),
        )
        self.db.add(user)

        # Delete pending user
        await self.db.delete(pending_user)
        # Use flush instead of commit to preserve RLS tenant context
        # (see: docs/solutions/database-issues/rls-context-lost-after-commit-settings-api-20260219.md)
        await self.db.flush()
        await self.db.refresh(user)

        # Generate JWT token
        tokens = self._create_tokens(user, tenant)

        # Send welcome email (non-blocking)
        try:
            await self.email_service.send_welcome_email(
                email=user.email,
                name=user.name or user.email,
                org_name=tenant.name,
            )
        except Exception as e:
            logger.warning(f"Failed to send welcome email: {e}")

        logger.info(f"Invited user registered: {user.email} joined {tenant.slug} as {user.role}")

        return RegisterResponse(
            user=UserResponse(
                id=str(user.id),
                email=user.email,
                name=user.name,
                role=user.role,
                is_active=user.is_active,
                auth_provider=user.auth_provider,
            ),
            tenant=TenantResponse.model_validate(tenant),
            access_token=tokens["access_token"],
            token_type="bearer",
            expires_in=tokens["expires_in"],
        )

    async def _register_with_new_org(
        self,
        email: str,
        password: str,
        name: str,
        org_name: str,
        org_slug: str,
    ) -> RegisterResponse:
        """Create new organization and owner user."""
        # Create tenant with default settings
        tenant = Tenant(
            name=org_name,
            slug=org_slug,
            is_active=True,
            settings={"signup_mode": "open"},
        )
        self.db.add(tenant)
        await self.db.flush()  # Get tenant.id

        # Create owner user
        hashed_password = self.user_service.hash_password(password)
        user = User(
            tenant_id=tenant.id,
            email=email,
            name=name,
            hashed_password=hashed_password,
            role="owner",  # First user is always owner
            auth_provider="local",
            is_active=True,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        await self.db.refresh(tenant)

        # Create default quotas for the new tenant
        from app.services.quota_service import QuotaService
        quota_svc = QuotaService(self.db)
        await quota_svc.create_default_quotas(tenant.id, plan_type="free")

        # Generate JWT token
        tokens = self._create_tokens(user, tenant)

        # Send welcome email (non-blocking)
        try:
            await self.email_service.send_welcome_email(
                email=user.email,
                name=user.name or user.email,
                org_name=tenant.name,
            )
        except Exception as e:
            logger.warning(f"Failed to send welcome email: {e}")

        logger.info(f"Self-service registration: {user.email} created org {tenant.slug}")

        return RegisterResponse(
            user=UserResponse(
                id=str(user.id),
                email=user.email,
                name=user.name,
                role=user.role,
                is_active=user.is_active,
                auth_provider=user.auth_provider,
            ),
            tenant=TenantResponse.model_validate(tenant),
            access_token=tokens["access_token"],
            token_type="bearer",
            expires_in=tokens["expires_in"],
        )

    # =========================================
    # Organization Creation
    # =========================================

    async def create_organization(
        self,
        user_id: UUID,
        name: str,
        slug: str,
        settings: Optional[Dict] = None,
    ) -> Tenant:
        """
        Create new organization for existing user.

        The user's primary tenant will be changed to the new organization,
        and they will become the owner.

        Args:
            user_id: UUID of the user creating the org
            name: Organization display name
            slug: URL-safe organization identifier
            settings: Optional initial settings dict

        Returns:
            Created Tenant object

        Raises:
            ValueError: If slug is taken or user not found
        """
        # Validate slug
        if not await self._check_slug(slug):
            raise ValueError(f"Organization slug '{slug}' is already taken.")

        # Get existing user
        user_result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise ValueError("User not found")

        # Create tenant
        tenant = Tenant(
            name=name,
            slug=slug,
            is_active=True,
            settings=settings or {"signup_mode": "open"},
        )
        self.db.add(tenant)
        await self.db.flush()

        # Update user's tenant and make them owner
        # Note: This changes the user's primary tenant
        user.tenant_id = tenant.id
        user.role = "owner"

        await self.db.commit()
        await self.db.refresh(tenant)

        # Create default quotas for the new organization
        from app.services.quota_service import QuotaService
        quota_svc = QuotaService(self.db)
        await quota_svc.create_default_quotas(tenant.id, plan_type="free")

        logger.info(f"Organization created: {tenant.slug} by user {user.email}")

        return tenant

    # =========================================
    # Slug Availability
    # =========================================

    async def check_slug_availability(self, slug: str) -> SlugCheckResponse:
        """
        Check if organization slug is available.

        Args:
            slug: Slug to check

        Returns:
            SlugCheckResponse with availability and suggestions
        """
        slug = slug.lower().strip()

        # Validate format
        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", slug):
            return SlugCheckResponse(
                available=False,
                suggestions=await self._generate_available_slugs(self._sanitize_slug(slug), count=3),
            )

        # Check availability
        is_available = await self._check_slug(slug)

        if is_available:
            return SlugCheckResponse(available=True, suggestions=[])

        # Generate suggestions
        suggestions = await self._generate_available_slugs(slug, count=3)

        return SlugCheckResponse(
            available=False,
            suggestions=suggestions,
        )

    # =========================================
    # Private Helpers
    # =========================================

    async def _check_slug(self, slug: str) -> bool:
        """Check if slug is available."""
        result = await self.db.execute(
            select(Tenant).where(Tenant.slug == slug)
        )
        return result.scalar_one_or_none() is None

    async def _generate_available_slugs(self, base_slug: str, count: int = 3) -> List[str]:
        """Generate available slug alternatives."""
        suggestions = []
        suffixes = ["-team", "-org", "-co", "1", "2", "3", "-hq", "-inc"]

        for suffix in suffixes:
            candidate = f"{base_slug}{suffix}"
            if await self._check_slug(candidate):
                suggestions.append(candidate)
                if len(suggestions) >= count:
                    break

        return suggestions

    async def _generate_slug(self, name: str) -> str:
        """Generate slug from organization name."""
        slug = self._sanitize_slug(name)

        # Ensure minimum length
        if len(slug) < 2:
            slug = f"{slug}-org"

        # If taken, add suffix
        if not await self._check_slug(slug):
            suggestions = await self._generate_available_slugs(slug, count=1)
            if suggestions:
                slug = suggestions[0]

        return slug

    @staticmethod
    def _sanitize_slug(name: str) -> str:
        """Convert name to valid slug format."""
        # Convert to lowercase, replace non-alphanumeric with hyphens
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower())
        # Remove leading/trailing hyphens
        slug = slug.strip("-")
        # Collapse multiple hyphens
        slug = re.sub(r"-+", "-", slug)
        return slug

    def _create_tokens(self, user: User, tenant: Tenant) -> Dict:
        """Create JWT tokens for user."""
        access_token = create_access_token(
            user_id=str(user.id),
            email=user.email,
            tenant_id=str(tenant.id),
            tenant_slug=tenant.slug,
            role=user.role,
            name=user.name,
            auth_provider=user.auth_provider,
            is_platform_operator=getattr(user, 'is_superuser', False) or False,
        )

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }
