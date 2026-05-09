"""WorkflowEntity model for entity tracking.

Part of Pragmatic Memory Enhancement feature.
Stores entities (companies, people, regulations, etc.) extracted from workflow executions.
Enables queries like "What workflows analyzed Acme Corp?"
"""
from typing import TYPE_CHECKING, Dict, Any
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Float, func
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import relationship

from app.models import Base

if TYPE_CHECKING:
    pass


class EntityType:
    """Standard entity types for financial domain."""
    COMPANY = "company"
    PERSON = "person"
    REGULATION = "regulation"
    FINANCIAL_INSTRUMENT = "financial_instrument"
    RISK_FACTOR = "risk_factor"
    DATE = "date"
    AMOUNT = "amount"
    LOCATION = "location"


class WorkflowEntity(Base):
    """
    Entity extracted from workflow execution.

    Tracks companies, people, regulations, and other entities
    mentioned in workflow outputs for searchability.

    Security:
    - RLS enforces tenant-level isolation
    - Entities are linked to specific workflow executions
    """
    __tablename__ = "workflow_entities"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this entity belongs to"
    )

    # Workflow references
    workflow_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Workflow that extracted this entity"
    )
    execution_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Specific execution that extracted this entity"
    )

    # Entity data
    name = Column(
        String(255),
        nullable=False,
        comment="Entity name (e.g., 'Acme Corp', 'John Smith')"
    )
    entity_type = Column(
        String(50),
        nullable=False,
        comment="Entity type (company, person, regulation, etc.)"
    )
    properties = Column(
        JSONB,
        server_default='{}',
        nullable=False,
        comment="Additional entity properties (ticker, title, jurisdiction, etc.)"
    )

    # Extraction metadata
    source_node_id = Column(
        String(100),
        nullable=True,
        comment="Workflow node that produced this entity"
    )
    confidence = Column(
        Float,
        server_default='1.0',
        nullable=False,
        comment="Extraction confidence score (0-1)"
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    created_by = Column(
        PGUUID(as_uuid=True),
        nullable=True,
        comment="User who triggered the extraction"
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="workflow_entities")
    workflow = relationship("Workflow", back_populates="entities")
    execution = relationship("WorkflowExecution", back_populates="entities")

    def __repr__(self) -> str:
        return f"<WorkflowEntity {self.entity_type}:{self.name}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert entity to dictionary for API responses."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "workflow_id": str(self.workflow_id),
            "execution_id": str(self.execution_id) if self.execution_id else None,
            "name": self.name,
            "entity_type": self.entity_type,
            "properties": self.properties,
            "source_node_id": self.source_node_id,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": str(self.created_by) if self.created_by else None,
        }
