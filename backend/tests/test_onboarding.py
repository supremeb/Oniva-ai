"""
Unit tests for OnboardingService.

Tests cover:
- Email availability checking
- Self-service registration with org creation
- Invited user registration
- Slug availability checking
- Organization creation for existing users
- Edge cases and validation errors

Note: SQLite is used for testing, which doesn't support PostgreSQL's gen_random_uuid().
Tests that rely on auto-generated UUIDs through the service are marked as postgres-only.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.models.user import User
from app.models.pending_user import PendingUser
from app.services.onboarding import OnboardingService


# Mark tests that need real PostgreSQL (skip on SQLite)
postgres_only = pytest.mark.skip(reason="Requires PostgreSQL for gen_random_uuid()")


# ============================================
# Fixtures
# ============================================

@pytest.fixture
async def sample_tenant(test_db: AsyncSession):
    """Create a sample tenant for testing."""
    tenant = Tenant(
        id=uuid4(),
        name="Test Organization",
        slug="test-org",
        is_active=True,
        settings={"signup_mode": "open"},
    )
    test_db.add(tenant)
    await test_db.commit()
    await test_db.refresh(tenant)
    return tenant


@pytest.fixture
async def sample_user(test_db: AsyncSession, sample_tenant: Tenant):
    """Create a sample user for testing."""
    user = User(
        id=uuid4(),
        tenant_id=sample_tenant.id,
        email="existing@example.com",
        name="Existing User",
        hashed_password="hashed_password_here",
        role="member",
        auth_provider="local",
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest.fixture
async def pending_user(test_db: AsyncSession, sample_tenant: Tenant):
    """Create a pending user invitation for testing."""
    pending = PendingUser(
        id=uuid4(),
        tenant_id=sample_tenant.id,
        email="invited@example.com",
        role="member",
        added_by=uuid4(),
    )
    test_db.add(pending)
    await test_db.commit()
    await test_db.refresh(pending)
    return pending


@pytest.fixture
def mock_email_service():
    """Mock email service to prevent actual email sending."""
    with patch("app.services.onboarding.get_email_service") as mock:
        email_svc = AsyncMock()
        email_svc.send_welcome_email = AsyncMock(return_value=True)
        mock.return_value = email_svc
        yield email_svc


@pytest.fixture
def mock_jwt():
    """Mock JWT token creation."""
    with patch("app.services.onboarding.create_access_token") as mock:
        mock.return_value = "mock_access_token_12345"
        yield mock


# ============================================
# Email Availability Tests
# ============================================

class TestCheckEmailAvailability:
    """Tests for email availability checking."""

    @pytest.mark.asyncio
    async def test_email_available_no_invitation(self, test_db: AsyncSession):
        """Test checking an available email with no invitation."""
        service = OnboardingService(test_db)

        result = await service.check_email_availability("new@example.com")

        assert result.available is True
        assert result.has_pending_invitation is False
        assert result.org_name is None
        assert result.tenant_slug is None

    @pytest.mark.asyncio
    async def test_email_not_available_user_exists(
        self, test_db: AsyncSession, sample_user: User
    ):
        """Test email not available when user already exists."""
        service = OnboardingService(test_db)

        result = await service.check_email_availability("existing@example.com")

        assert result.available is False
        assert result.has_pending_invitation is False

    @pytest.mark.asyncio
    async def test_email_available_with_invitation(
        self, test_db: AsyncSession, pending_user: PendingUser, sample_tenant: Tenant
    ):
        """Test email available with pending invitation."""
        service = OnboardingService(test_db)

        result = await service.check_email_availability("invited@example.com")

        assert result.available is True
        assert result.has_pending_invitation is True
        assert result.org_name == sample_tenant.name
        assert result.tenant_slug == sample_tenant.slug

    @pytest.mark.asyncio
    async def test_email_normalized(self, test_db: AsyncSession):
        """Test that email is normalized (lowercase, trimmed)."""
        service = OnboardingService(test_db)

        result = await service.check_email_availability("  NEW@EXAMPLE.COM  ")

        assert result.available is True


# ============================================
# Self-Service Registration Tests
# ============================================

class TestSelfServiceRegistration:
    """Tests for self-service registration with org creation."""

    @postgres_only
    @pytest.mark.asyncio
    async def test_register_creates_org_and_user(
        self, test_db: AsyncSession, mock_email_service, mock_jwt
    ):
        """Test successful self-service registration.

        Note: Skipped on SQLite due to gen_random_uuid() requirement.
        """
        service = OnboardingService(test_db)

        result = await service.register_user(
            email="owner@neworg.com",
            password="SecurePass123!",
            name="Org Owner",
            org_name="New Organization",
            org_slug="new-org",
        )

        assert result.user.email == "owner@neworg.com"
        assert result.user.name == "Org Owner"
        assert result.user.role == "owner"
        assert result.tenant.name == "New Organization"
        assert result.tenant.slug == "new-org"
        assert result.access_token == "mock_access_token_12345"
        assert result.token_type == "bearer"

        # Verify data persisted
        user_result = await test_db.execute(
            select(User).where(User.email == "owner@neworg.com")
        )
        user = user_result.scalar_one()
        assert user.role == "owner"

    @postgres_only
    @pytest.mark.asyncio
    async def test_register_auto_generates_slug(
        self, test_db: AsyncSession, mock_email_service, mock_jwt
    ):
        """Test that slug is auto-generated from org name.

        Note: Skipped on SQLite due to gen_random_uuid() requirement.
        """
        service = OnboardingService(test_db)

        result = await service.register_user(
            email="owner@company.com",
            password="SecurePass123!",
            name="Owner",
            org_name="My Amazing Company",
            # No org_slug provided
        )

        assert result.tenant.slug == "my-amazing-company"

    @pytest.mark.asyncio
    async def test_register_fails_duplicate_slug(
        self, test_db: AsyncSession, sample_tenant: Tenant, mock_email_service
    ):
        """Test registration fails when slug is taken."""
        service = OnboardingService(test_db)

        with pytest.raises(ValueError, match="already taken"):
            await service.register_user(
                email="new@example.com",
                password="SecurePass123!",
                name="New User",
                org_name="Test Org",
                org_slug="test-org",  # Same as sample_tenant
            )

    @pytest.mark.asyncio
    async def test_register_fails_no_org_name(self, test_db: AsyncSession):
        """Test registration fails without org name when no invitation."""
        service = OnboardingService(test_db)

        with pytest.raises(ValueError, match="No pending invitation"):
            await service.register_user(
                email="new@example.com",
                password="SecurePass123!",
                name="New User",
                # No org_name provided
            )

    @pytest.mark.asyncio
    async def test_register_fails_weak_password(self, test_db: AsyncSession):
        """Test registration fails with weak password."""
        service = OnboardingService(test_db)

        with pytest.raises(ValueError, match="Password validation failed"):
            await service.register_user(
                email="new@example.com",
                password="weak",  # Too short
                name="New User",
                org_name="New Org",
            )


# ============================================
# Invited User Registration Tests
# ============================================

class TestInvitedUserRegistration:
    """Tests for invited user registration."""

    @postgres_only
    @pytest.mark.asyncio
    async def test_register_invited_user(
        self,
        test_db: AsyncSession,
        pending_user: PendingUser,
        sample_tenant: Tenant,
        mock_email_service,
        mock_jwt,
    ):
        """Test invited user registration joins existing org.

        Note: Skipped on SQLite due to gen_random_uuid() for User creation.
        """
        service = OnboardingService(test_db)

        result = await service.register_user(
            email="invited@example.com",
            password="SecurePass123!",
            name="Invited User",
            # No org_name needed for invited users
        )

        assert result.user.email == "invited@example.com"
        assert result.user.name == "Invited User"
        assert result.user.role == "member"  # Role from invitation
        assert result.tenant.id == str(sample_tenant.id)
        assert result.tenant.slug == sample_tenant.slug

        # Verify pending user was deleted
        pending_result = await test_db.execute(
            select(PendingUser).where(PendingUser.email == "invited@example.com")
        )
        assert pending_result.scalar_one_or_none() is None

    @postgres_only
    @pytest.mark.asyncio
    async def test_invited_user_ignores_org_params(
        self,
        test_db: AsyncSession,
        pending_user: PendingUser,
        sample_tenant: Tenant,
        mock_email_service,
        mock_jwt,
    ):
        """Test invited user registration ignores org_name/org_slug.

        Note: Skipped on SQLite due to gen_random_uuid() for User creation.
        """
        service = OnboardingService(test_db)

        result = await service.register_user(
            email="invited@example.com",
            password="SecurePass123!",
            name="Invited User",
            org_name="Ignored Org Name",
            org_slug="ignored-slug",
        )

        # Should join existing org, not create new one
        assert result.tenant.slug == sample_tenant.slug
        assert result.tenant.name == sample_tenant.name


# ============================================
# Slug Availability Tests
# ============================================

class TestSlugAvailability:
    """Tests for slug availability checking."""

    @pytest.mark.asyncio
    async def test_slug_available(self, test_db: AsyncSession):
        """Test checking an available slug."""
        service = OnboardingService(test_db)

        result = await service.check_slug_availability("new-slug")

        assert result.available is True
        assert result.suggestions == []

    @pytest.mark.asyncio
    async def test_slug_not_available(
        self, test_db: AsyncSession, sample_tenant: Tenant
    ):
        """Test checking an unavailable slug returns suggestions."""
        service = OnboardingService(test_db)

        result = await service.check_slug_availability("test-org")

        assert result.available is False
        assert len(result.suggestions) > 0
        assert all(s.startswith("test-org") for s in result.suggestions)

    @pytest.mark.asyncio
    async def test_slug_invalid_format(self, test_db: AsyncSession):
        """Test checking invalid slug format."""
        service = OnboardingService(test_db)

        result = await service.check_slug_availability("Invalid Slug!!")

        assert result.available is False
        assert len(result.suggestions) > 0

    @pytest.mark.asyncio
    async def test_slug_normalized(self, test_db: AsyncSession):
        """Test that slug is normalized."""
        service = OnboardingService(test_db)

        result = await service.check_slug_availability("  NEW-SLUG  ")

        assert result.available is True


# ============================================
# Organization Creation Tests
# ============================================

class TestCreateOrganization:
    """Tests for creating organizations for existing users."""

    @postgres_only
    @pytest.mark.asyncio
    async def test_create_org_success(
        self, test_db: AsyncSession, sample_user: User, sample_tenant: Tenant
    ):
        """Test successful organization creation.

        Note: Skipped on SQLite due to gen_random_uuid() for Tenant creation.
        """
        service = OnboardingService(test_db)

        result = await service.create_organization(
            user_id=sample_user.id,
            name="New Org",
            slug="new-org-slug",
        )

        assert result.name == "New Org"
        assert result.slug == "new-org-slug"
        assert result.is_active is True

        # Verify user's tenant updated
        await test_db.refresh(sample_user)
        assert sample_user.tenant_id == result.id
        assert sample_user.role == "owner"

    @pytest.mark.asyncio
    async def test_create_org_fails_duplicate_slug(
        self, test_db: AsyncSession, sample_user: User, sample_tenant: Tenant
    ):
        """Test org creation fails with duplicate slug."""
        service = OnboardingService(test_db)

        with pytest.raises(ValueError, match="already taken"):
            await service.create_organization(
                user_id=sample_user.id,
                name="Another Org",
                slug="test-org",  # Same as sample_tenant
            )

    @pytest.mark.asyncio
    async def test_create_org_fails_user_not_found(self, test_db: AsyncSession):
        """Test org creation fails when user not found."""
        service = OnboardingService(test_db)

        with pytest.raises(ValueError, match="User not found"):
            await service.create_organization(
                user_id=uuid4(),
                name="New Org",
                slug="new-org",
            )

    @postgres_only
    @pytest.mark.asyncio
    async def test_create_org_with_custom_settings(
        self, test_db: AsyncSession, sample_user: User, sample_tenant: Tenant
    ):
        """Test org creation with custom settings.

        Note: Skipped on SQLite due to gen_random_uuid() for Tenant creation.
        """
        service = OnboardingService(test_db)

        result = await service.create_organization(
            user_id=sample_user.id,
            name="Enterprise Org",
            slug="enterprise-org",
            settings={"signup_mode": "invite_only", "max_users": 100},
        )

        assert result.settings["signup_mode"] == "invite_only"
        assert result.settings["max_users"] == 100


# ============================================
# Helper Method Tests
# ============================================

class TestHelperMethods:
    """Tests for private helper methods."""

    def test_sanitize_slug(self):
        """Test slug sanitization."""
        assert OnboardingService._sanitize_slug("My Company") == "my-company"
        assert OnboardingService._sanitize_slug("Test!!!Org") == "test-org"
        assert OnboardingService._sanitize_slug("---test---") == "test"
        assert OnboardingService._sanitize_slug("A  B  C") == "a-b-c"
        assert OnboardingService._sanitize_slug("123-test") == "123-test"

    @pytest.mark.asyncio
    async def test_generate_available_slugs(self, test_db: AsyncSession, sample_tenant: Tenant):
        """Test slug suggestion generation."""
        service = OnboardingService(test_db)

        suggestions = await service._generate_available_slugs("test-org", count=3)

        assert len(suggestions) <= 3
        for suggestion in suggestions:
            assert suggestion.startswith("test-org")
            # Verify suggestions are available
            is_available = await service._check_slug(suggestion)
            assert is_available is True

    @pytest.mark.asyncio
    async def test_generate_slug_from_name(self, test_db: AsyncSession):
        """Test slug generation from organization name."""
        service = OnboardingService(test_db)

        slug = await service._generate_slug("My Amazing Company")

        assert slug == "my-amazing-company"

    @pytest.mark.asyncio
    async def test_generate_slug_handles_collision(
        self, test_db: AsyncSession, sample_tenant: Tenant
    ):
        """Test slug generation handles collisions."""
        service = OnboardingService(test_db)

        # sample_tenant has slug "test-org"
        slug = await service._generate_slug("Test Org")

        # Should generate alternative since "test-org" is taken
        assert slug != "test-org"
        assert slug.startswith("test-org")


# ============================================
# Edge Cases
# ============================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_email_case_insensitive(
        self, test_db: AsyncSession, sample_user: User
    ):
        """Test email checking is case-insensitive."""
        service = OnboardingService(test_db)

        # sample_user has email "existing@example.com"
        result = await service.check_email_availability("EXISTING@EXAMPLE.COM")

        assert result.available is False

    @pytest.mark.asyncio
    async def test_deleted_user_email_available(self, test_db: AsyncSession):
        """Test that soft-deleted user's email is available."""
        # Create a soft-deleted user
        tenant = Tenant(
            id=uuid4(),
            name="Test",
            slug="deleted-user-test",
            is_active=True,
        )
        test_db.add(tenant)
        await test_db.flush()

        user = User(
            id=uuid4(),
            tenant_id=tenant.id,
            email="deleted@example.com",
            hashed_password="hash",
            role="member",
            auth_provider="local",
            deleted_at=datetime.utcnow(),  # Soft deleted
        )
        test_db.add(user)
        await test_db.commit()

        service = OnboardingService(test_db)
        result = await service.check_email_availability("deleted@example.com")

        # Email should be available since user is soft-deleted
        assert result.available is True

    @pytest.mark.asyncio
    async def test_short_slug_minimum_length(self, test_db: AsyncSession):
        """Test slug generation handles very short names."""
        service = OnboardingService(test_db)

        # Single letter should get "-org" suffix
        slug = await service._generate_slug("A")

        assert len(slug) >= 2
        assert slug == "a-org"
