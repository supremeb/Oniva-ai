"""Custom Agent model with multi-tenancy support."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Integer, Text, Boolean, func, text
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base


class CustomAgent(Base):
    """
    Stores user-created custom agents with configurable skills and connectors.
    These are persistent versions of agents that users can create, configure, and reuse.
    """
    __tablename__ = 'custom_agents'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Team ownership (Phase A.2: RBAC)
    # NULL = user_id is sole owner (existing behavior for backward compatibility)
    team_id = Column(
        UUID(as_uuid=True),
        ForeignKey('teams.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )

    # Human-readable identifier (AGT-001, AGT-002, etc.)
    # Tenant-scoped auto-incrementing, assigned at creation, never recycled
    sequence_number = Column(Integer, nullable=True, index=True)

    # Basic Info
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), default='Robot')  # Icon name for UI
    agent_type = Column(String(100), default='custom')  # Type category

    # LLM Configuration
    model = Column(String(100), default='gpt-4o')  # LLM model to use
    system_prompt = Column(Text, nullable=True)  # Custom system prompt
    temperature = Column(Float, default=0.7)  # LLM temperature
    max_tokens = Column(Integer, default=2000)  # Max tokens for responses

    # Skills Configuration (array of skill names from registry)
    # e.g., ['web_search', 'browse_url', 'execute_python', 'read_file']
    skills = Column(ARRAY(String), default=list)

    # Connectors (array of connector UUIDs)
    # Links to the Connector model for data access
    connector_ids = Column(ARRAY(UUID(as_uuid=True)), default=list)

    # Knowledge bases attached to this agent (Frontier Phase C)
    knowledge_base_ids = Column(ARRAY(UUID(as_uuid=True)), default=list)

    # Workflows attached to this agent (workflows-as-agent-resources)
    workflow_ids = Column(ARRAY(UUID(as_uuid=True)), default=list)

    # Additional Configuration
    config = Column(JSONB, default=dict)  # Flexible config for future extensions

    # Workflows using this agent (for tracking)
    workflow_usage = Column(JSONB, default=dict)  # {workflow_id: usage_count}

    # Status
    is_active = Column(Boolean, default=True)
    is_template = Column(Boolean, default=False)  # Is this a template for cloning?

    # Phase A.3: Approval Workflows
    risk_tier = Column(String(20), default='low')
    requires_approval = Column(Boolean, default=False)

    # Phase B: Registry-First Architecture
    # Visibility: private (owner only), team (team members), tenant (whole org)
    visibility = Column(String(20), default='private', nullable=False)
    category = Column(String(50), nullable=True)  # e.g. "Financial", "Compliance", "Research"
    published_at = Column(DateTime(timezone=True), nullable=True)
    clone_count = Column(Integer, default=0)
    cloned_from_id = Column(UUID(as_uuid=True), nullable=True)  # Lineage tracking

    # Compliance tags (industry-agnostic: SOC2, GDPR, HIPAA, etc.)
    compliance_tags = Column(ARRAY(String(50)), default=list)
    compliance_notes = Column(Text, nullable=True)

    # Orchestration (supervisor pattern)
    is_supervisor = Column(Boolean, default=False)
    child_agent_config = Column(JSONB, default=list)  # [{agent_id, task, order}]
    execution_mode = Column(String(20), default='sequential')  # sequential, parallel
    resource_constraints = Column(JSONB, default=dict)  # {max_concurrent, token_budget, timeout_seconds, max_depth}

    # Background agent execution config (Phase 3: Autonomous Execution)
    # Minimal structure: {"enabled": true, "webhook_url": "...", "webhook_headers": {}}
    # Schedule lives in ScheduledWorkflow. Limits live in resource_constraints.
    background_config = Column(JSONB, nullable=True, comment="Background execution config")

    # Optimistic locking for concurrent modification detection
    row_version = Column(Integer, default=1, server_default=text("1"), nullable=False)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    # Statistics
    usage_count = Column(Integer, default=0)  # How many times this agent has been run

    # Relationships
    user = relationship("User", back_populates="custom_agents")
    tenant = relationship("Tenant")
    chat_sessions = relationship("ChatSession", back_populates="agent", cascade="all, delete-orphan")
    versions = relationship("AgentVersion", back_populates="agent", cascade="all, delete-orphan")
    aliases = relationship("AgentAlias", back_populates="agent", cascade="all, delete-orphan")
    background_jobs = relationship("BackgroundJob", back_populates="agent", cascade="all, delete-orphan")

    @property
    def instruction_format(self):
        """Read instruction_format from config JSONB (no dedicated column needed)."""
        if self.config and isinstance(self.config, dict):
            return self.config.get('instruction_format', 'plain_text')
        return 'plain_text'

    @instruction_format.setter
    def instruction_format(self, value):
        """Write instruction_format into config JSONB."""
        if not self.config or not isinstance(self.config, dict):
            self.config = {}
        self.config['instruction_format'] = value or 'plain_text'

    @property
    def thinking_model(self):
        """Read thinking_model from config JSONB (used when thinking mode is enabled)."""
        if self.config and isinstance(self.config, dict):
            return self.config.get('thinking_model')
        return None

    @thinking_model.setter
    def thinking_model(self, value):
        """Write thinking_model into config JSONB."""
        if not self.config or not isinstance(self.config, dict):
            self.config = {}
        if value:
            self.config['thinking_model'] = value
        else:
            self.config.pop('thinking_model', None)

    def __repr__(self):
        return f"<CustomAgent(name='{self.name}', type='{self.agent_type}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'user_id': str(self.user_id),
            'tenant_id': str(self.tenant_id),
            'team_id': str(self.team_id) if self.team_id else None,
            'sequence_number': self.sequence_number,
            'name': self.name,
            'description': self.description,
            'icon': self.icon,
            'type': self.agent_type,
            'model': self.model,
            'system_prompt': self.system_prompt,
            'instruction_format': self.instruction_format,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'skills': self.skills,
            'connectors': [str(cid) for cid in (self.connector_ids or [])],
            'knowledge_base_ids': [str(kid) for kid in (self.knowledge_base_ids or [])],
            'workflow_ids': [str(wid) for wid in (self.workflow_ids or [])],
            'config': self.config,
            'thinking_model': self.thinking_model,
            'is_active': self.is_active,
            'is_template': self.is_template,
            'risk_tier': self.risk_tier or 'low',
            'requires_approval': self.requires_approval or False,
            # Phase B: Registry
            'visibility': self.visibility or 'private',
            'category': self.category,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'clone_count': self.clone_count or 0,
            'cloned_from_id': str(self.cloned_from_id) if self.cloned_from_id else None,
            'compliance_tags': self.compliance_tags or [],
            'compliance_notes': self.compliance_notes,
            # Phase B: Orchestration
            'is_supervisor': self.is_supervisor or False,
            'child_agent_config': self.child_agent_config or [],
            'execution_mode': self.execution_mode or 'sequential',
            'resource_constraints': self.resource_constraints or {},
            'background_config': self.background_config,
            # Metadata
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'usage_count': self.usage_count,
            'row_version': self.row_version or 1,
        }
