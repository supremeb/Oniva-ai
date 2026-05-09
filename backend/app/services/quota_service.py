"""Quota Service for enforcing usage limits.

Enterprise feature: Enforce LLM token/execution limits per user/team/org.

Supports:
- Multiple quota types (tokens, executions, storage, API calls)
- Scoping to tenant, user, or team level
- Various reset periods (daily, weekly, monthly, annual, lifetime)
- Warning thresholds for proactive alerts
- Automatic quota reset on period expiry

- Phase 4: Operations
"""
import logging
import uuid
from typing import Optional, List, Tuple

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage_quota import (
    UsageQuota, UsageHistory,
    ScopeType, QuotaType, QuotaPeriod
)

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Raised when a quota limit is exceeded."""

    def __init__(self, message: str, quota_type: str, current: int, limit: int):
        self.message = message
        self.quota_type = quota_type
        self.current = current
        self.limit = limit
        super().__init__(message)


class QuotaService:
    """Service for managing and enforcing usage quotas.

    Features:
    - Check quotas before operations
    - Increment usage after operations
    - Automatic period reset
    - Warning threshold notifications
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_quota(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        quota_type: QuotaType,
        estimated_usage: int = 1
    ) -> Tuple[bool, Optional[str]]:
        """Check if operation is allowed under current quotas.

        Checks quotas in order:
        1. User quota (most specific)
        2. Tenant quota (fallback)

        Args:
            tenant_id: The tenant ID
            user_id: The user attempting the operation
            quota_type: Type of quota to check
            estimated_usage: Expected usage amount

        Returns:
            Tuple of (allowed, reason)
            - allowed: True if operation is allowed
            - reason: Explanation string (error message if not allowed)
        """
        # Check user quota first
        user_quota = await self._get_quota(
            tenant_id=tenant_id,
            scope_type=ScopeType.USER,
            scope_id=user_id,
            quota_type=quota_type
        )

        if user_quota:
            if user_quota.is_expired():
                # Reset expired quota
                user_quota.reset()
                await self.db.commit()
            elif user_quota.current_usage + estimated_usage > user_quota.limit_value:
                return False, (
                    f"User quota exceeded for {quota_type.value}: "
                    f"{user_quota.current_usage}/{user_quota.limit_value}"
                )

        # Check tenant quota
        tenant_quota = await self._get_quota(
            tenant_id=tenant_id,
            scope_type=ScopeType.TENANT,
            scope_id=tenant_id,
            quota_type=quota_type
        )

        if tenant_quota:
            if tenant_quota.is_expired():
                # Reset expired quota
                tenant_quota.reset()
                await self.db.commit()
            elif tenant_quota.current_usage + estimated_usage > tenant_quota.limit_value:
                return False, (
                    f"Organization quota exceeded for {quota_type.value}: "
                    f"{tenant_quota.current_usage}/{tenant_quota.limit_value}"
                )

        return True, "OK"

    async def check_and_raise(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        quota_type: QuotaType,
        estimated_usage: int = 1
    ) -> None:
        """Check quota and raise exception if exceeded.

        Use this before operations that should fail if quota exceeded.
        """
        allowed, reason = await self.check_quota(
            tenant_id, user_id, quota_type, estimated_usage
        )

        if not allowed:
            # Get the specific quota for error details
            quota = await self._get_quota(
                tenant_id=tenant_id,
                scope_type=ScopeType.USER,
                scope_id=user_id,
                quota_type=quota_type
            )
            if not quota:
                quota = await self._get_quota(
                    tenant_id=tenant_id,
                    scope_type=ScopeType.TENANT,
                    scope_id=tenant_id,
                    quota_type=quota_type
                )

            raise QuotaExceededError(
                message=reason,
                quota_type=quota_type.value,
                current=quota.current_usage if quota else 0,
                limit=quota.limit_value if quota else 0
            )

    async def increment_usage(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        quota_type: QuotaType,
        amount: int,
        execution_id: Optional[uuid.UUID] = None,
        workflow_id: Optional[uuid.UUID] = None,
        metadata: Optional[dict] = None
    ) -> None:
        """Increment usage for both user and tenant quotas.

        Also creates usage history records for audit.

        Args:
            tenant_id: The tenant ID
            user_id: The user performing the operation
            quota_type: Type of quota to increment
            amount: Amount to increment
            execution_id: Optional execution for context
            workflow_id: Optional workflow for context
            metadata: Optional additional context
        """
        # Increment user quota
        user_quota = await self._get_quota(
            tenant_id=tenant_id,
            scope_type=ScopeType.USER,
            scope_id=user_id,
            quota_type=quota_type
        )

        if user_quota:
            user_quota.increment(amount)

            # Record history
            history = UsageHistory(
                tenant_id=tenant_id,
                quota_id=user_quota.id,
                scope_type=ScopeType.USER.value,
                scope_id=user_id,
                quota_type=quota_type.value,
                amount=amount,
                execution_id=execution_id,
                workflow_id=workflow_id,
                metadata=metadata
            )
            self.db.add(history)

            # Check warning threshold
            if user_quota.is_at_warning and not user_quota.warning_sent:
                user_quota.warning_sent = True
                logger.warning(
                    f"User {user_id} quota warning: {user_quota.usage_percent:.1f}% used"
                )
                # TODO: Trigger warning alert

        # Increment tenant quota
        tenant_quota = await self._get_quota(
            tenant_id=tenant_id,
            scope_type=ScopeType.TENANT,
            scope_id=tenant_id,
            quota_type=quota_type
        )

        if tenant_quota:
            tenant_quota.increment(amount)

            # Record history
            history = UsageHistory(
                tenant_id=tenant_id,
                quota_id=tenant_quota.id,
                scope_type=ScopeType.TENANT.value,
                scope_id=tenant_id,
                quota_type=quota_type.value,
                amount=amount,
                execution_id=execution_id,
                workflow_id=workflow_id,
                metadata=metadata
            )
            self.db.add(history)

            # Check warning threshold
            if tenant_quota.is_at_warning and not tenant_quota.warning_sent:
                tenant_quota.warning_sent = True
                logger.warning(
                    f"Tenant {tenant_id} quota warning: {tenant_quota.usage_percent:.1f}% used"
                )
                # TODO: Trigger warning alert

        await self.db.commit()

    async def get_usage_summary(
        self,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None
    ) -> List[dict]:
        """Get usage summary for quota display.

        Args:
            tenant_id: The tenant ID
            user_id: Optional user for user-specific quotas

        Returns:
            List of quota summaries
        """
        summaries = []

        # Query conditions
        conditions = [UsageQuota.tenant_id == tenant_id, UsageQuota.is_active == True]

        if user_id:
            conditions.append(
                or_(
                    and_(
                        UsageQuota.scope_type == ScopeType.USER.value,
                        UsageQuota.scope_id == user_id
                    ),
                    UsageQuota.scope_type == ScopeType.TENANT.value
                )
            )

        query = select(UsageQuota).where(and_(*conditions))
        result = await self.db.execute(query)
        quotas = result.scalars().all()

        for quota in quotas:
            summaries.append({
                "id": str(quota.id),
                "scope_type": quota.scope_type,
                "quota_type": quota.quota_type,
                "limit": quota.limit_value,
                "current": quota.current_usage,
                "remaining": quota.remaining,
                "usage_percent": round(quota.usage_percent, 1),
                "period": quota.period,
                "period_start": quota.period_start.isoformat() if quota.period_start else None,
                "period_end": quota.period_end.isoformat() if quota.period_end else None,
                "is_exceeded": quota.is_exceeded,
                "is_at_warning": quota.is_at_warning
            })

        return summaries

    async def create_default_quotas(
        self,
        tenant_id: uuid.UUID,
        plan_type: str = "free"
    ) -> List[UsageQuota]:
        """Create default quotas for a new tenant based on plan.

        Args:
            tenant_id: The tenant ID
            plan_type: Subscription plan (free, pro, enterprise)

        Returns:
            List of created quotas
        """
        # Define limits by plan
        limits = {
            "free": {
                QuotaType.LLM_TOKENS: 100_000,  # 100k tokens/month
                QuotaType.EXECUTIONS: 50,  # 50 executions/month
            },
            "pro": {
                QuotaType.LLM_TOKENS: 1_000_000,  # 1M tokens/month
                QuotaType.EXECUTIONS: 500,  # 500 executions/month
            },
            "enterprise": {
                QuotaType.LLM_TOKENS: 10_000_000,  # 10M tokens/month
                QuotaType.EXECUTIONS: 5000,  # 5000 executions/month
            }
        }

        plan_limits = limits.get(plan_type, limits["free"])
        created = []

        for quota_type, limit in plan_limits.items():
            quota = UsageQuota.create_default_tenant_quota(
                tenant_id=tenant_id,
                quota_type=quota_type,
                limit_value=limit,
                period=QuotaPeriod.MONTHLY
            )
            self.db.add(quota)
            created.append(quota)

        await self.db.commit()

        # Refresh to get IDs
        for quota in created:
            await self.db.refresh(quota)

        logger.info(f"Created {len(created)} default quotas for tenant {tenant_id}")
        return created

    async def update_quota_limit(
        self,
        quota_id: uuid.UUID,
        new_limit: int
    ) -> UsageQuota:
        """Update quota limit (admin operation).

        Args:
            quota_id: The quota to update
            new_limit: New limit value

        Returns:
            Updated quota
        """
        query = select(UsageQuota).where(UsageQuota.id == quota_id)
        result = await self.db.execute(query)
        quota = result.scalar_one_or_none()

        if not quota:
            raise ValueError(f"Quota not found: {quota_id}")

        quota.limit_value = new_limit
        await self.db.commit()

        logger.info(f"Updated quota {quota_id} limit to {new_limit}")
        return quota

    async def _get_quota(
        self,
        tenant_id: uuid.UUID,
        scope_type: ScopeType,
        scope_id: uuid.UUID,
        quota_type: QuotaType
    ) -> Optional[UsageQuota]:
        """Get a specific quota."""
        query = select(UsageQuota).where(
            UsageQuota.tenant_id == tenant_id,
            UsageQuota.scope_type == scope_type.value,
            UsageQuota.scope_id == scope_id,
            UsageQuota.quota_type == quota_type.value,
            UsageQuota.is_active == True
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()


# --- Service Dependency ---

async def get_quota_service(db: AsyncSession) -> QuotaService:
    """Dependency injection for QuotaService."""
    return QuotaService(db)
