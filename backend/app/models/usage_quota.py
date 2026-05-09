"""Usage Quota models for enforcing limits.

Enterprise feature: Enforce LLM token/execution limits per user/team/org.

- Phase 4: Operations
"""
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, BigInteger, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models import Base

if TYPE_CHECKING:
    pass


class ScopeType(str, Enum):
    """Quota scope types."""
    TENANT = "tenant"
    USER = "user"
    TEAM = "team"


class QuotaType(str, Enum):
    """Types of quotas that can be enforced."""
    LLM_TOKENS = "llm_tokens"
    EXECUTIONS = "executions"
    STORAGE_BYTES = "storage_bytes"
    API_CALLS = "api_calls"


class QuotaPeriod(str, Enum):
    """Quota reset periods."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ANNUAL = "annual"
    LIFETIME = "lifetime"


class UsageQuota(Base):
    """Usage quota definition and current usage tracking.

    Supports:
    - Scoping to tenant, user, or team level
    - Multiple quota types (tokens, executions, storage, API calls)
    - Various reset periods (daily, weekly, monthly, annual, lifetime)
    - Warning thresholds for proactive alerts
    """
    __tablename__ = 'usage_quotas'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Quota scope
    scope_type = Column(String(20), nullable=False)  # 'tenant', 'user', 'team'
    scope_id = Column(UUID(as_uuid=True), nullable=False)

    # Quota type and limits
    quota_type = Column(String(50), nullable=False)  # 'llm_tokens', 'executions', etc.
    limit_value = Column(BigInteger, nullable=False)
    current_usage = Column(BigInteger, default=0, nullable=False)

    # Period configuration
    period = Column(String(20), nullable=False)  # 'daily', 'weekly', 'monthly', etc.
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=True)  # null for lifetime

    # Warning threshold
    warning_threshold = Column(Float, default=0.8, nullable=False)  # 80% by default
    warning_sent = Column(Boolean, default=False, nullable=False)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    tenant = relationship("Tenant")

    @property
    def usage_percent(self) -> float:
        """Get current usage as percentage of limit."""
        if self.limit_value == 0:
            return 100.0
        return (self.current_usage / self.limit_value) * 100

    @property
    def remaining(self) -> int:
        """Get remaining quota."""
        return max(0, self.limit_value - self.current_usage)

    @property
    def is_exceeded(self) -> bool:
        """Check if quota is exceeded."""
        return self.current_usage >= self.limit_value

    @property
    def is_at_warning(self) -> bool:
        """Check if quota has reached warning threshold."""
        return self.usage_percent >= (self.warning_threshold * 100)

    def is_expired(self) -> bool:
        """Check if the quota period has expired."""
        if self.period == QuotaPeriod.LIFETIME.value:
            return False
        if self.period_end is None:
            return False
        return datetime.now(timezone.utc) > self.period_end

    def check_and_increment(self, amount: int) -> tuple[bool, str]:
        """Check if increment is allowed and perform it.

        Returns:
            Tuple of (allowed, reason)
        """
        if not self.is_active:
            return True, "Quota inactive"

        if self.is_expired():
            return True, "Quota period expired"

        if self.current_usage + amount > self.limit_value:
            return False, f"Quota exceeded: {self.current_usage}/{self.limit_value} {self.quota_type}"

        self.current_usage += amount
        return True, "OK"

    def increment(self, amount: int) -> None:
        """Increment usage without checking."""
        self.current_usage += amount

    def reset(self) -> None:
        """Reset quota for new period."""
        self.current_usage = 0
        self.warning_sent = False

        # Calculate new period
        now = datetime.now(timezone.utc)
        self.period_start = now

        if self.period == QuotaPeriod.DAILY.value:
            self.period_end = now + timedelta(days=1)
        elif self.period == QuotaPeriod.WEEKLY.value:
            self.period_end = now + timedelta(weeks=1)
        elif self.period == QuotaPeriod.MONTHLY.value:
            # Approximate month
            self.period_end = now + timedelta(days=30)
        elif self.period == QuotaPeriod.ANNUAL.value:
            self.period_end = now + timedelta(days=365)
        else:  # lifetime
            self.period_end = None

    @classmethod
    def create_default_tenant_quota(
        cls,
        tenant_id: uuid.UUID,
        quota_type: QuotaType,
        limit_value: int,
        period: QuotaPeriod = QuotaPeriod.MONTHLY
    ) -> 'UsageQuota':
        """Create a default tenant-level quota."""
        now = datetime.now(timezone.utc)

        if period == QuotaPeriod.DAILY:
            period_end = now + timedelta(days=1)
        elif period == QuotaPeriod.WEEKLY:
            period_end = now + timedelta(weeks=1)
        elif period == QuotaPeriod.MONTHLY:
            period_end = now + timedelta(days=30)
        elif period == QuotaPeriod.ANNUAL:
            period_end = now + timedelta(days=365)
        else:
            period_end = None

        return cls(
            tenant_id=tenant_id,
            scope_type=ScopeType.TENANT.value,
            scope_id=tenant_id,
            quota_type=quota_type.value,
            limit_value=limit_value,
            period=period.value,
            period_start=now,
            period_end=period_end
        )

    def __repr__(self) -> str:
        return (
            f"<UsageQuota("
            f"{self.scope_type}:{self.scope_id}, "
            f"{self.quota_type}: {self.current_usage}/{self.limit_value}"
            f")>"
        )


class UsageHistory(Base):
    """Individual usage events for audit and reporting."""
    __tablename__ = 'usage_history'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    quota_id = Column(
        UUID(as_uuid=True),
        ForeignKey('usage_quotas.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Usage event details
    scope_type = Column(String(20), nullable=False)
    scope_id = Column(UUID(as_uuid=True), nullable=False)
    quota_type = Column(String(50), nullable=False)
    amount = Column(BigInteger, nullable=False)

    # Context
    execution_id = Column(UUID(as_uuid=True), nullable=True)
    workflow_id = Column(UUID(as_uuid=True), nullable=True)
    event_metadata = Column(JSONB, nullable=True)

    # Timestamp
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant")
    quota = relationship("UsageQuota")

    def __repr__(self) -> str:
        return f"<UsageHistory({self.quota_type}: +{self.amount})>"
