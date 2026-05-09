"""User management service for RBAC and user lifecycle operations.

This service handles:
- User listing and filtering
- Pending user management (admin adds users)
- Role changes with permission checks
- User activation/deactivation
- Password management (admin reset)
"""
import logging
from datetime import datetime
from typing import List, Optional, Tuple
from uuid import UUID

from passlib.context import CryptContext
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password_policy import PasswordPolicy
from app.models.user import User
from app.models.pending_user import PendingUser
from app.models.tenant import Tenant
from app.services.email_service import get_email_service

logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserManagementService:
    """Service for managing users and pending users."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.email_service = get_email_service()

    # =========================================
    # Password Utilities
    # =========================================

    @staticmethod
    def validate_password(password: str) -> Tuple[bool, Optional[str]]:
        """
        Validate password against security requirements using centralized PasswordPolicy.

        Uses the PasswordPolicy class which enforces NIST SP 800-63B guidelines:
        - Minimum 12 characters (configurable via PASSWORD_MIN_LENGTH env var)
        - At least one uppercase letter
        - At least one lowercase letter
        - At least one digit
        - At least one special character
        - No common passwords
        - No sequential characters (e.g., "1234", "abcd")
        - No more than 3 consecutive identical characters

        Returns:
            Tuple of (is_valid, error_message)
        """
        is_valid, errors = PasswordPolicy.validate(password)
        if not is_valid:
            # Return first error for backwards compatibility
            return False, errors[0] if errors else "Invalid password"
        return True, None

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt.

        Note: bcrypt has a 72-byte limit, so we truncate longer passwords.
        This is safe because 72 bytes provides sufficient entropy.
        """
        # Truncate to 72 bytes (bcrypt limit)
        password_bytes = password.encode('utf-8')[:72]
        return pwd_context.hash(password_bytes.decode('utf-8', errors='ignore'))

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against a hash."""
        # Truncate to 72 bytes to match hashing behavior
        password_bytes = plain_password.encode('utf-8')[:72]
        return pwd_context.verify(password_bytes.decode('utf-8', errors='ignore'), hashed_password)

    # =========================================
    # User Listing
    # =========================================

    async def list_users(
        self,
        tenant_id: UUID,
        search: Optional[str] = None,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[User], int]:
        """
        List users for a tenant with optional filtering.

        Args:
            tenant_id: Tenant UUID
            search: Search term for email/name
            role: Filter by role
            is_active: Filter by active status
            limit: Max results
            offset: Pagination offset

        Returns:
            Tuple of (users list, total count)
        """
        # Build base query
        query = select(User).where(User.tenant_id == tenant_id)
        count_query = select(func.count()).select_from(User).where(User.tenant_id == tenant_id)

        # Apply filters
        if search:
            search_filter = or_(
                User.email.ilike(f"%{search}%"),
                User.name.ilike(f"%{search}%")
            )
            query = query.where(search_filter)
            count_query = count_query.where(search_filter)

        if role:
            query = query.where(User.role == role)
            count_query = count_query.where(User.role == role)

        if is_active is not None:
            query = query.where(User.is_active == is_active)
            count_query = count_query.where(User.is_active == is_active)

        # Get total count
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()

        # Get paginated results
        query = query.order_by(User.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        users = result.scalars().all()

        return list(users), total

    async def get_user_by_id(self, user_id: UUID, tenant_id: UUID) -> Optional[User]:
        """Get a user by ID within a tenant."""
        result = await self.db.execute(
            select(User).where(
                and_(User.id == user_id, User.tenant_id == tenant_id)
            )
        )
        return result.scalars().first()

    async def get_user_by_email(self, email: str, tenant_id: UUID) -> Optional[User]:
        """Get a user by email within a tenant."""
        result = await self.db.execute(
            select(User).where(
                and_(User.email == email.lower(), User.tenant_id == tenant_id)
            )
        )
        return result.scalars().first()

    # =========================================
    # Pending Users
    # =========================================

    async def list_pending_users(
        self,
        tenant_id: UUID,
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[PendingUser], int]:
        """List pending users for a tenant."""
        # Get total count
        count_result = await self.db.execute(
            select(func.count()).select_from(PendingUser).where(
                PendingUser.tenant_id == tenant_id
            )
        )
        total = count_result.scalar()

        # Get paginated results
        result = await self.db.execute(
            select(PendingUser)
            .where(PendingUser.tenant_id == tenant_id)
            .order_by(PendingUser.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        pending_users = result.scalars().all()

        return list(pending_users), total

    async def add_pending_user(
        self,
        tenant_id: UUID,
        email: str,
        role: str,
        added_by: UUID,
        org_name: Optional[str] = None,
        inviter_name: Optional[str] = None
    ) -> PendingUser:
        """
        Add a user to the pending list (admin invites user).

        Args:
            tenant_id: Tenant UUID
            email: User's email address
            role: Pre-assigned role (admin, member, viewer)
            added_by: Admin user UUID
            org_name: Organization name for email
            inviter_name: Admin's name for email

        Returns:
            Created PendingUser
        """
        email = email.lower()

        # Check if email already exists as a user
        existing_user = await self.get_user_by_email(email, tenant_id)
        if existing_user:
            raise ValueError(f"User with email {email} already exists")

        # Check if email already pending
        existing_pending = await self.db.execute(
            select(PendingUser).where(
                and_(
                    PendingUser.tenant_id == tenant_id,
                    PendingUser.email == email
                )
            )
        )
        if existing_pending.scalars().first():
            raise ValueError(f"User with email {email} is already pending")

        # Validate role
        if role not in ('admin', 'member', 'viewer'):
            raise ValueError(f"Invalid role: {role}")

        # Create pending user
        pending_user = PendingUser(
            tenant_id=tenant_id,
            email=email,
            role=role,
            added_by=added_by
        )
        self.db.add(pending_user)
        await self.db.flush()

        # Send invitation email
        if org_name and inviter_name:
            await self.email_service.send_invitation_email(
                email=email,
                org_name=org_name,
                inviter_name=inviter_name,
                role=role
            )

        logger.info(f"Added pending user {email} to tenant {tenant_id} with role {role}")
        return pending_user

    async def get_pending_user_by_email(
        self,
        email: str,
        tenant_id: Optional[UUID] = None
    ) -> Optional[PendingUser]:
        """
        Get pending user by email, optionally within a specific tenant.

        If tenant_id is None, searches across all tenants.
        """
        email = email.lower()
        query = select(PendingUser).where(PendingUser.email == email)

        if tenant_id:
            query = query.where(PendingUser.tenant_id == tenant_id)

        result = await self.db.execute(query)
        return result.scalars().first()

    async def delete_pending_user(
        self,
        pending_user_id: UUID,
        tenant_id: UUID
    ) -> bool:
        """Remove a pending user."""
        result = await self.db.execute(
            select(PendingUser).where(
                and_(
                    PendingUser.id == pending_user_id,
                    PendingUser.tenant_id == tenant_id
                )
            )
        )
        pending_user = result.scalars().first()

        if not pending_user:
            return False

        await self.db.delete(pending_user)
        logger.info(f"Deleted pending user {pending_user.email} from tenant {tenant_id}")
        return True

    # =========================================
    # Signup Flow
    # =========================================

    async def check_can_signup(self, email: str) -> Tuple[bool, Optional[PendingUser]]:
        """
        Check if an email can sign up.

        Returns:
            Tuple of (can_signup, pending_user if found)
        """
        email = email.lower()

        # Check if already a user anywhere
        result = await self.db.execute(
            select(User).where(User.email == email)
        )
        if result.scalars().first():
            return False, None

        # Check if in pending users
        pending_user = await self.get_pending_user_by_email(email)
        if pending_user:
            return True, pending_user

        return False, None

    async def signup_user(
        self,
        email: str,
        password: str,
        name: str
    ) -> User:
        """
        Complete user signup from pending status.

        Args:
            email: User's email (must be in pending_users)
            password: User's chosen password
            name: User's display name

        Returns:
            Created User
        """
        email = email.lower()

        # Validate password
        is_valid, error = self.validate_password(password)
        if not is_valid:
            raise ValueError(error)

        # Find pending user
        pending_user = await self.get_pending_user_by_email(email)
        if not pending_user:
            raise ValueError("Email not found. Contact your admin to be added.")

        # Get tenant
        tenant_result = await self.db.execute(
            select(Tenant).where(Tenant.id == pending_user.tenant_id)
        )
        tenant = tenant_result.scalars().first()
        if not tenant:
            raise ValueError("Organization not found")

        # Create user
        user = User(
            tenant_id=pending_user.tenant_id,
            email=email,
            name=name,
            hashed_password=self.hash_password(password),
            role=pending_user.role,
            auth_provider="local",
            is_active=True,
            added_by=pending_user.added_by,
            added_at=pending_user.created_at,
            password_changed_at=datetime.utcnow()
        )
        self.db.add(user)

        # Delete pending user
        await self.db.delete(pending_user)
        await self.db.flush()

        # Send welcome email
        await self.email_service.send_welcome_email(
            email=email,
            name=name,
            org_name=tenant.name
        )

        logger.info(f"User {email} completed signup for tenant {tenant.slug}")
        return user

    # =========================================
    # Role Management
    # =========================================

    async def change_user_role(
        self,
        user_id: UUID,
        new_role: str,
        changed_by: UUID,
        tenant_id: UUID
    ) -> User:
        """
        Change a user's role.

        Permission rules:
        - Owner can change anyone's role
        - Admin can only change Member/Viewer roles

        Args:
            user_id: Target user UUID
            new_role: New role (owner, admin, member, viewer)
            changed_by: Admin user UUID making the change
            tenant_id: Tenant UUID

        Returns:
            Updated User
        """
        # Validate new role
        if new_role not in ('owner', 'admin', 'member', 'viewer'):
            raise ValueError(f"Invalid role: {new_role}")

        # Get target user
        target_user = await self.get_user_by_id(user_id, tenant_id)
        if not target_user:
            raise ValueError("User not found")

        # Get actor (person making the change)
        actor = await self.get_user_by_id(changed_by, tenant_id)
        if not actor:
            raise ValueError("Actor not found")

        # Permission checks
        if not actor.can_manage_users:
            raise PermissionError("You don't have permission to change user roles")

        # Admin cannot change owner or other admin roles
        if actor.role == 'admin':
            if target_user.role in ('owner', 'admin'):
                raise PermissionError("Admins cannot change Owner or Admin roles")
            if new_role in ('owner', 'admin'):
                raise PermissionError("Admins cannot assign Owner or Admin roles")

        # Cannot demote the only owner
        if target_user.role == 'owner' and new_role != 'owner':
            owner_count = await self.db.execute(
                select(func.count()).select_from(User).where(
                    and_(User.tenant_id == tenant_id, User.role == 'owner')
                )
            )
            if owner_count.scalar() <= 1:
                raise ValueError("Cannot change the only owner's role. Transfer ownership first.")

        old_role = target_user.role
        target_user.role = new_role

        # Get tenant for email
        tenant_result = await self.db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = tenant_result.scalars().first()

        # Send notification
        await self.email_service.send_role_change_notification(
            email=target_user.email,
            name=target_user.name or target_user.email,
            old_role=old_role,
            new_role=new_role,
            org_name=tenant.name if tenant else "your organization"
        )

        logger.info(f"Changed user {target_user.email} role from {old_role} to {new_role}")
        return target_user

    # =========================================
    # User Activation/Deactivation
    # =========================================

    async def deactivate_user(
        self,
        user_id: UUID,
        deactivated_by: UUID,
        tenant_id: UUID
    ) -> User:
        """Deactivate a user (soft disable)."""
        target_user = await self.get_user_by_id(user_id, tenant_id)
        if not target_user:
            raise ValueError("User not found")

        actor = await self.get_user_by_id(deactivated_by, tenant_id)
        if not actor or not actor.can_manage_users:
            raise PermissionError("You don't have permission to deactivate users")

        # Cannot deactivate owner
        if target_user.role == 'owner':
            raise ValueError("Cannot deactivate the owner")

        # Admin cannot deactivate other admins
        if actor.role == 'admin' and target_user.role == 'admin':
            raise PermissionError("Admins cannot deactivate other admins")

        target_user.is_active = False

        # Get tenant for email
        tenant_result = await self.db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = tenant_result.scalars().first()

        await self.email_service.send_account_deactivated_notification(
            email=target_user.email,
            name=target_user.name or target_user.email,
            org_name=tenant.name if tenant else "your organization"
        )

        logger.info(f"Deactivated user {target_user.email}")
        return target_user

    async def reactivate_user(
        self,
        user_id: UUID,
        reactivated_by: UUID,
        tenant_id: UUID
    ) -> User:
        """Reactivate a deactivated user."""
        target_user = await self.get_user_by_id(user_id, tenant_id)
        if not target_user:
            raise ValueError("User not found")

        actor = await self.get_user_by_id(reactivated_by, tenant_id)
        if not actor or not actor.can_manage_users:
            raise PermissionError("You don't have permission to reactivate users")

        target_user.is_active = True
        logger.info(f"Reactivated user {target_user.email}")
        return target_user

    # =========================================
    # Password Management
    # =========================================

    async def change_password(
        self,
        user_id: UUID,
        current_password: str,
        new_password: str,
        tenant_id: UUID
    ) -> User:
        """Change user's own password."""
        user = await self.get_user_by_id(user_id, tenant_id)
        if not user:
            raise ValueError("User not found")

        if not user.hashed_password:
            raise ValueError("Password not set for this user")

        if not self.verify_password(current_password, user.hashed_password):
            raise ValueError("Current password is incorrect")

        # Validate new password
        is_valid, error = self.validate_password(new_password)
        if not is_valid:
            raise ValueError(error)

        user.hashed_password = self.hash_password(new_password)
        user.password_changed_at = datetime.utcnow()

        logger.info(f"User {user.email} changed their password")
        return user

    async def admin_reset_password(
        self,
        user_id: UUID,
        new_password: str,
        reset_by: UUID,
        tenant_id: UUID
    ) -> User:
        """Admin resets a user's password."""
        target_user = await self.get_user_by_id(user_id, tenant_id)
        if not target_user:
            raise ValueError("User not found")

        actor = await self.get_user_by_id(reset_by, tenant_id)
        if not actor or not actor.can_manage_users:
            raise PermissionError("You don't have permission to reset passwords")

        # Admin cannot reset other admin or owner passwords
        if actor.role == 'admin' and target_user.role in ('admin', 'owner'):
            raise PermissionError("Admins cannot reset Admin or Owner passwords")

        # Validate new password
        is_valid, error = self.validate_password(new_password)
        if not is_valid:
            raise ValueError(error)

        target_user.hashed_password = self.hash_password(new_password)
        target_user.password_changed_at = datetime.utcnow()

        logger.info(f"Admin {actor.email} reset password for {target_user.email}")
        return target_user

    async def update_user(
        self,
        user_id: UUID,
        tenant_id: UUID,
        name: Optional[str] = None
    ) -> User:
        """Update user profile information."""
        user = await self.get_user_by_id(user_id, tenant_id)
        if not user:
            raise ValueError("User not found")

        if name is not None:
            user.name = name

        logger.info(f"Updated user {user.email}")
        return user
