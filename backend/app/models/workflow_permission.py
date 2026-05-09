"""Workflow Permission model for sharing workflows.

Enterprise feature: Enable workflow collaboration with granular permission
controls. Supports sharing with users, roles, and organization-wide access.

- Phase 3: Collaboration
"""
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, TYPE_CHECKING

from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models import Base

if TYPE_CHECKING:
    pass


class GranteeType(str, Enum):
    """Type of permission grantee."""
    USER = "user"
    ROLE = "role"
    ORGANIZATION = "organization"


class PermissionLevel(str, Enum):
    """Permission levels for workflow access.

    Hierarchy (higher includes lower):
    - admin: Full control (edit, execute, view, share, delete)
    - edit: Modify workflow (execute, view)
    - execute: Run workflow (view)
    - view: Read-only access
    """
    VIEW = "view"
    EXECUTE = "execute"
    EDIT = "edit"
    ADMIN = "admin"

    @classmethod
    def includes(cls, held: 'PermissionLevel', required: 'PermissionLevel') -> bool:
        """Check if held permission includes required permission.

        Permission hierarchy: admin > edit > execute > view
        """
        hierarchy = {
            cls.VIEW: 0,
            cls.EXECUTE: 1,
            cls.EDIT: 2,
            cls.ADMIN: 3,
        }
        return hierarchy.get(held, -1) >= hierarchy.get(required, 99)


class WorkflowPermission(Base):
    """Model for workflow sharing permissions.

    Supports three grantee types:
    - user: Direct share with specific user UUID
    - role: Share with all users of a role (admin, analyst, viewer)
    - organization: Share with entire tenant/organization

    Permission levels follow a hierarchy where higher levels
    include all lower level permissions.
    """
    __tablename__ = 'workflow_permissions'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Tenant isolation
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Workflow being shared
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflows.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Grantee info
    grantee_type = Column(
        String(20),
        nullable=False
    )
    grantee_id = Column(
        String(255),
        nullable=False
    )

    # Permission level
    permission = Column(
        String(20),
        nullable=False
    )

    # Audit info
    granted_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    granted_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    expires_at = Column(
        DateTime(timezone=True),
        nullable=True
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    workflow = relationship("Workflow", back_populates="permissions")
    tenant = relationship("Tenant")
    granter = relationship("User", foreign_keys=[granted_by])

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            'workflow_id', 'grantee_type', 'grantee_id',
            name='uq_workflow_permission_grantee'
        ),
        Index('idx_workflow_permissions_grantee', 'grantee_type', 'grantee_id'),
        Index(
            'idx_workflow_permissions_expires',
            'expires_at',
            postgresql_where='expires_at IS NOT NULL'
        ),
    )

    def is_expired(self) -> bool:
        """Check if this permission has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def is_valid(self) -> bool:
        """Check if this permission is currently valid (not expired)."""
        return not self.is_expired()

    def has_permission(self, required: PermissionLevel) -> bool:
        """Check if this grant includes the required permission level.

        Args:
            required: The permission level needed

        Returns:
            True if this grant's permission includes the required level
        """
        if self.is_expired():
            return False
        try:
            held = PermissionLevel(self.permission)
            return PermissionLevel.includes(held, required)
        except ValueError:
            return False

    def can_view(self) -> bool:
        """Check if this grant allows viewing the workflow."""
        return self.has_permission(PermissionLevel.VIEW)

    def can_execute(self) -> bool:
        """Check if this grant allows executing the workflow."""
        return self.has_permission(PermissionLevel.EXECUTE)

    def can_edit(self) -> bool:
        """Check if this grant allows editing the workflow."""
        return self.has_permission(PermissionLevel.EDIT)

    def can_admin(self) -> bool:
        """Check if this grant allows full admin access."""
        return self.has_permission(PermissionLevel.ADMIN)

    @classmethod
    def create_user_permission(
        cls,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        user_id: uuid.UUID,
        permission: PermissionLevel,
        granted_by: Optional[uuid.UUID] = None,
        expires_at: Optional[datetime] = None
    ) -> 'WorkflowPermission':
        """Create a permission for a specific user."""
        return cls(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            grantee_type=GranteeType.USER.value,
            grantee_id=str(user_id),
            permission=permission.value,
            granted_by=granted_by,
            expires_at=expires_at
        )

    @classmethod
    def create_role_permission(
        cls,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        role_name: str,
        permission: PermissionLevel,
        granted_by: Optional[uuid.UUID] = None,
        expires_at: Optional[datetime] = None
    ) -> 'WorkflowPermission':
        """Create a permission for all users with a specific role."""
        return cls(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            grantee_type=GranteeType.ROLE.value,
            grantee_id=role_name,
            permission=permission.value,
            granted_by=granted_by,
            expires_at=expires_at
        )

    @classmethod
    def create_org_permission(
        cls,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        permission: PermissionLevel,
        granted_by: Optional[uuid.UUID] = None,
        expires_at: Optional[datetime] = None
    ) -> 'WorkflowPermission':
        """Create an organization-wide permission."""
        return cls(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            grantee_type=GranteeType.ORGANIZATION.value,
            grantee_id="organization",
            permission=permission.value,
            granted_by=granted_by,
            expires_at=expires_at
        )

    def __repr__(self) -> str:
        return (
            f"<WorkflowPermission("
            f"workflow={self.workflow_id}, "
            f"grantee={self.grantee_type}:{self.grantee_id}, "
            f"permission={self.permission}"
            f")>"
        )
