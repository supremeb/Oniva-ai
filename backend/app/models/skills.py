"""Skills models with multi-tenancy support."""
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Integer, JSON, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base
from app.models._clock import utc_now


class Skill(Base):
    """
    Skill model for storing persistent skill definitions.
    Supports both built-in (synced) and custom (user-defined) skills.
    """
    __tablename__ = 'skills'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=False, index=True)

    # Multi-tenancy (nullable for system skills)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=True,  # NULL for system skills
        index=True,
        comment="Tenant for custom skills; NULL for system skills"
    )

    # Implementation details
    code = Column(Text, nullable=True)  # For custom skills (python code)
    configuration = Column(JSON, default={})  # Default config schema/values
    requires = Column(JSON, default=[])       # List of skill dependencies
    mcp_dependencies = Column(JSON, default=[])  # List of MCP server dependencies

    version = Column(String(50), default="1.0.0")
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    is_custom = Column(Boolean, default=False)  # True if custom/user-defined
    is_system = Column(Boolean, default=True)   # True if built-in
    enabled = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now, onupdate=utc_now)

    # Relationships
    author = relationship("User", backref="created_skills")
    tenant = relationship("Tenant")
    executions = relationship("SkillExecution", back_populates="skill", cascade="all, delete-orphan")


class SkillExecution(Base):
    """
    Audit log for skill executions.
    Supports full audit trail for compliance and debugging.
    """
    __tablename__ = 'skill_executions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("skills.id"), nullable=True, index=True)
    skill_name = Column(String(255), nullable=False, index=True)  # For fast queries without joins
    agent_id = Column(String(255), nullable=True, index=True)  # ID of the agent calling the skill

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # User and session tracking
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    session_id = Column(String(255), nullable=True, index=True)

    # Workflow context
    workflow_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    workflow_run_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    node_id = Column(String(255), nullable=True)

    # Execution data
    inputs = Column(JSON, nullable=True)
    outputs = Column(JSON, nullable=True)

    status = Column(String(50), nullable=False, index=True)  # 'success', 'failed', 'timeout'
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    error_type = Column(String(100), nullable=True)  # Exception class name

    # Audit fields
    input_hash = Column(String(64), nullable=True)  # SHA-256 hash of inputs for integrity
    output_hash = Column(String(64), nullable=True)  # SHA-256 hash of outputs for integrity
    audit_metadata = Column(JSON, nullable=True)  # Additional context (IP, user agent, etc.)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    skill = relationship("Skill", back_populates="executions")
    tenant = relationship("Tenant")
    user = relationship("User")
