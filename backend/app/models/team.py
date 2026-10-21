"""Team model for RBAC in Agent Studio (Phase A.2)."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SQLEnum, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.models import Base
from app.models._clock import utc_now


class TeamRole(str, enum.Enum):
    """Roles within a team with increasing permission levels."""
    VIEWER = "viewer"    # Can view agent configs and executions
    MEMBER = "member"    # Can create/edit agents, run tests
    ADMIN = "admin"      # Can deploy agents, manage team settings
    OWNER = "owner"      # Full control, can delete team, approve deployments


class Team(Base):
    """
    Teams group users for collaborative agent development.
    Enables RBAC for agent operations within Agent Studio.
    """
    __tablename__ = 'teams'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Team info
    name = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)
    slug = Column(String(100), nullable=False, index=True)  # URL-friendly identifier

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now, onupdate=utc_now)

    # Relationships
    tenant = relationship("Tenant")
    memberships = relationship("TeamMembership", back_populates="team", cascade="all, delete-orphan")
    agents = relationship("CustomAgent", backref="team", lazy="dynamic")

    def __repr__(self):
        return f"<Team(name='{self.name}', tenant_id='{self.tenant_id}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'name': self.name,
            'description': self.description,
            'slug': self.slug,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class TeamMembership(Base):
    """
    Associates users with teams and defines their role within the team.
    """
    __tablename__ = 'team_memberships'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # References
    team_id = Column(
        UUID(as_uuid=True),
        ForeignKey('teams.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Role within the team
    role = Column(
        SQLEnum(TeamRole, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TeamRole.MEMBER
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now, onupdate=utc_now)

    # Relationships
    team = relationship("Team", back_populates="memberships")
    user = relationship("User")

    def __repr__(self):
        return f"<TeamMembership(team_id='{self.team_id}', user_id='{self.user_id}', role='{self.role}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'team_id': str(self.team_id),
            'user_id': str(self.user_id),
            'role': self.role.value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
