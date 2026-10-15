"""LLM Usage tracking model for cost attribution and analytics."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models import Base


class LLMUsageRecord(Base):
    """
    Tracks individual LLM API calls for cost attribution and analytics.

    Each record represents a single LLM completion call with:
    - Token counts (input/output)
    - Cost in USD (calculated via LiteLLM)
    - Latency metrics
    - Context (workflow, node, user)

    Usage data can be aggregated via SQL for dashboards.
    No pre-computed daily tables - use SQL aggregations.
    """

    __tablename__ = "llm_usage_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Provider info
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_name = Column(String(100), nullable=False)
    model = Column(String(100), nullable=False)

    # Usage metrics
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    cost_usd = Column(Numeric(10, 6), nullable=False)  # Precision for small costs
    latency_ms = Column(Integer, nullable=False)

    # Context
    workflow_id = Column(UUID(as_uuid=True), nullable=True)
    node_id = Column(String(100), nullable=True)
    agent_id = Column(UUID(as_uuid=True), nullable=True)

    # Cache tracking
    cache_creation_tokens = Column(Integer, nullable=True, doc="Tokens written to provider cache")
    cache_read_tokens = Column(Integer, nullable=True, doc="Tokens read from provider cache")

    # Engine ledger keys (plan 003): request_id makes usage posts idempotent per tenant
    request_id = Column(String(255), nullable=True)
    run_id = Column(String(128), nullable=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)

    # Why the call was made: generation (default) or compaction (engine summaries)
    purpose = Column(String(20), nullable=True)

    # Status
    status = Column(String(20), default="success")  # success, failed, cached, unpriced

    # Timestamp
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=datetime.utcnow,
        index=True,
    )

    __table_args__ = (
        Index(
            "uq_llm_usage_tenant_request",
            "tenant_id",
            "request_id",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
    )

    # Relationships
    tenant = relationship("Tenant")
    provider = relationship("LLMProvider")

    def __repr__(self):
        return f"<LLMUsageRecord(provider='{self.provider_name}', model='{self.model}', tokens={self.input_tokens + self.output_tokens})>"
