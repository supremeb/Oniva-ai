"""Alerting models for proactive failure notifications.

Enterprise feature: Define triggers and notification channels for alerts.

- Phase 4: Operations
"""
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from app.models import Base

if TYPE_CHECKING:
    pass


class ChannelType(str, Enum):
    """Notification channel types."""
    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    TEAMS = "teams"
    PAGERDUTY = "pagerduty"


class TriggerType(str, Enum):
    """Alert trigger types."""
    EXECUTION_FAILED = "execution_failed"
    HIGH_FAILURE_RATE = "high_failure_rate"
    SLA_BREACH = "sla_breach"
    QUOTA_EXCEEDED = "quota_exceeded"
    CUSTOM = "custom"


class NotificationStatus(str, Enum):
    """Alert notification delivery status."""
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class NotificationChannel(Base):
    """Notification delivery channel configuration.

    Supports email, Slack, webhooks, Teams, and PagerDuty.
    Config is encrypted for sensitive URLs/tokens.
    """
    __tablename__ = 'notification_channels'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    name = Column(String(255), nullable=False)
    channel_type = Column(String(50), nullable=False)

    # Channel-specific config
    # Email: {"recipients": ["email@example.com"]}
    # Slack: {"webhook_url": "...", "channel": "#alerts"}
    # Webhook: {"url": "...", "secret": "...", "headers": {...}}
    # Teams: {"webhook_url": "..."}
    # PagerDuty: {"routing_key": "..."}
    config = Column(JSONB, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

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
    creator = relationship("User")

    def __repr__(self) -> str:
        return f"<NotificationChannel({self.name}, type={self.channel_type})>"


class AlertRule(Base):
    """Alert rule definition.

    Defines when alerts should trigger and where to send them.
    Supports scoping to specific workflows or all workflows.
    """
    __tablename__ = 'alert_rules'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Trigger configuration
    trigger_type = Column(String(50), nullable=False)
    # Examples:
    # execution_failed: {} (triggers on any failure)
    # high_failure_rate: {"failure_rate": 0.1, "window_minutes": 60}
    # sla_breach: {"max_duration_minutes": 30}
    # quota_exceeded: {"threshold_percent": 80}
    trigger_config = Column(JSONB, nullable=False, default=dict)

    # Scope (optional)
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflows.id', ondelete='CASCADE'),
        nullable=True,
        index=True
    )
    scope_type = Column(String(20), nullable=True)  # 'workflow', 'user', 'all'
    scope_id = Column(String(255), nullable=True)

    # Notification targets
    notification_channel_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False)

    # Cooldown to prevent alert storms
    cooldown_minutes = Column(Integer, default=15, nullable=False)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)
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

    def is_in_cooldown(self) -> bool:
        """Check if the rule is in cooldown period."""
        if not self.last_triggered_at:
            return False
        from datetime import timedelta
        cooldown_end = self.last_triggered_at + timedelta(minutes=self.cooldown_minutes)
        return datetime.now(timezone.utc) < cooldown_end

    def matches_execution(self, execution: Any) -> bool:
        """Check if this rule matches the given execution."""
        # Check scope
        if self.workflow_id and execution.workflow_id != self.workflow_id:
            return False

        if self.scope_type == 'user' and self.scope_id:
            if str(execution.user_id) != self.scope_id:
                return False

        return True

    def __repr__(self) -> str:
        return f"<AlertRule({self.name}, trigger={self.trigger_type})>"


class AlertHistory(Base):
    """Record of triggered alerts and their delivery status."""
    __tablename__ = 'alert_history'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    alert_rule_id = Column(
        UUID(as_uuid=True),
        ForeignKey('alert_rules.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflow_executions.id', ondelete='SET NULL'),
        nullable=True
    )
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflows.id', ondelete='SET NULL'),
        nullable=True
    )

    # Alert details
    trigger_type = Column(String(50), nullable=False)
    trigger_data = Column(JSONB, nullable=True)
    message = Column(Text, nullable=False)

    # Delivery tracking
    notification_status = Column(String(20), default='pending', nullable=False)
    notification_error = Column(Text, nullable=True)
    notified_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamp
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant")
    alert_rule = relationship("AlertRule")
    execution = relationship("WorkflowExecution")
    workflow = relationship("Workflow")

    def mark_sent(self):
        """Mark alert as successfully sent."""
        self.notification_status = NotificationStatus.SENT.value
        self.notified_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str):
        """Mark alert as failed to send."""
        self.notification_status = NotificationStatus.FAILED.value
        self.notification_error = error

    def __repr__(self) -> str:
        return f"<AlertHistory({self.trigger_type}, status={self.notification_status})>"
