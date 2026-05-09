"""Routing Policy model — maps task types to LLM models per tenant."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Integer, Float, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.models import Base


class RoutingPolicy(Base):
    """Admin-configured policy mapping task type → LLM model."""
    __tablename__ = "routing_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    task_type = Column(String(50), nullable=False)  # research | analysis | code_gen | extraction | synthesis | browser
    output_type = Column(String(50), nullable=True)  # pdf_report | presentation | spreadsheet | dashboard | chart | document | data_export | NULL
    model_id = Column(String(255), nullable=False)  # e.g. "anthropic/claude-sonnet-4-5"
    provider = Column(String(50), nullable=False)  # openai | anthropic | openrouter
    priority = Column(Integer, nullable=False, default=0)
    max_tokens = Column(Integer, nullable=True)
    temperature = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<RoutingPolicy(task_type='{self.task_type}', model='{self.model_id}')>"
