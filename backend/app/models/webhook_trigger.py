"""Webhook Trigger models for inbound event-driven automation.

Enterprise feature: Allow external systems to trigger workflows via webhooks.
"""
import uuid
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET, ARRAY
from sqlalchemy.orm import relationship

from app.models import Base

if TYPE_CHECKING:
    pass


def generate_webhook_token() -> str:
    """Generate secure random webhook URL token."""
    return f"wh_{secrets.token_urlsafe(32)}"


class WebhookTrigger(Base):
    """Inbound webhook configuration for triggering workflows."""
    __tablename__ = 'webhook_triggers'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflows.id', ondelete='CASCADE'),
        nullable=True,
        index=True
    )
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey('custom_agents.id', ondelete='CASCADE'),
        nullable=True,
        index=True
    )

    # Webhook URL token (unique, unguessable)
    url_token = Column(String(64), unique=True, nullable=False, default=generate_webhook_token)

    # Authentication
    auth_method = Column(String(20), nullable=False, default='hmac')
    auth_config = Column(JSONB, nullable=False, default=dict)
    allowed_ip_addresses = Column(ARRAY(INET), nullable=True)

    # Payload processing
    payload_mapping = Column(JSONB, nullable=False, default=dict)
    filter_expression = Column(Text, nullable=True)

    # Rate limiting
    rate_limit_per_hour = Column(Integer, nullable=False, default=100)

    # Status
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    tenant = relationship("Tenant")
    workflow = relationship("Workflow")
    creator = relationship("User")

    def __repr__(self) -> str:
        return f"<WebhookTrigger(workflow_id={self.workflow_id}, token={self.url_token[:16]}...)>"


class WebhookDelivery(Base):
    """Webhook delivery log for audit and debugging."""
    __tablename__ = 'webhook_deliveries'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    webhook_trigger_id = Column(
        UUID(as_uuid=True),
        ForeignKey('webhook_triggers.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Request details
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    source_ip = Column(INET, nullable=True)
    request_headers = Column(JSONB, nullable=True)
    request_body = Column(JSONB, nullable=True)

    # Processing
    filter_matched = Column(Boolean, nullable=True)
    workflow_execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflow_executions.id', ondelete='SET NULL'),
        nullable=True
    )
    response_status = Column(Integer, nullable=False)
    error_message = Column(Text, nullable=True)

    # Idempotency
    idempotency_key = Column(String(255), nullable=True)

    processed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant")
    webhook_trigger = relationship("WebhookTrigger")
    execution = relationship("WorkflowExecution")

    def __repr__(self) -> str:
        return f"<WebhookDelivery(trigger_id={self.webhook_trigger_id}, status={self.response_status})>"
