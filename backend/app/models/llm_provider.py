"""LLM Provider configuration model for multi-tenant LLM failover."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship

from app.models import Base
from app.models._clock import utc_now


class LLMProvider(Base):
    """
    Tenant-specific LLM provider configuration.

    Supports built-in providers (OpenAI, Anthropic, OpenRouter) and
    custom endpoints (vLLM, Ollama, Azure OpenAI).

    Priority field determines fallback order - lower values tried first.
    """

    __tablename__ = "llm_providers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Provider identification
    name = Column(String(100), nullable=False)  # Unique per tenant
    display_name = Column(String(200), nullable=False)
    provider_type = Column(String(50), nullable=False)
    # provider_type options: "openai", "anthropic", "openrouter", "azure", "custom"

    # Connection settings
    api_base = Column(String(500), nullable=True)  # Custom endpoint URL
    api_key_encrypted = Column(Text, nullable=True)  # Fernet encrypted

    # Model configuration
    models = Column(ARRAY(String), default=list)  # Available models
    default_model = Column(String(100), nullable=True)
    embedding_model = Column(String(200), nullable=True)  # e.g. "qwen/qwen3-embedding-8b"
    workflow_builder_model = Column(String(200), nullable=True)  # Model for workflow AI chat builder

    # Fallback settings
    priority = Column(Integer, default=0)  # Lower = higher priority
    is_enabled = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utc_now,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationship
    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<LLMProvider(name='{self.name}', type='{self.provider_type}', priority={self.priority})>"
