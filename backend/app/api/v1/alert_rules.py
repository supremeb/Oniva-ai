"""Alert Rules and Notification Channels API.

Enterprise feature: Configure proactive alerts for workflow failures
and operational issues.

Provides:
- CRUD for alert rules
- CRUD for notification channels
- Test notification delivery

- Phase 4: Operations
"""
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.models.alerting import (
    NotificationChannel, AlertRule, AlertHistory
)

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Schemas ---

class NotificationChannelCreate(BaseModel):
    name: str
    channel_type: str  # email, slack, webhook, teams, pagerduty
    config: dict  # Channel-specific config


class NotificationChannelUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class NotificationChannelResponse(BaseModel):
    id: uuid.UUID
    name: str
    channel_type: str
    is_active: bool
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AlertRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_type: str  # execution_failed, high_failure_rate, etc.
    trigger_config: dict = Field(default_factory=dict)
    workflow_id: Optional[uuid.UUID] = None
    notification_channel_ids: List[uuid.UUID]
    cooldown_minutes: int = 15


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_config: Optional[dict] = None
    notification_channel_ids: Optional[List[uuid.UUID]] = None
    cooldown_minutes: Optional[int] = None
    is_active: Optional[bool] = None


class AlertRuleResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    trigger_type: str
    trigger_config: dict
    workflow_id: Optional[uuid.UUID]
    notification_channel_ids: List[uuid.UUID]
    cooldown_minutes: int
    is_active: bool
    last_triggered_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# --- Notification Channels ---

@router.post("/notification-channels", response_model=NotificationChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_notification_channel(
    request: NotificationChannelCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Create a notification channel.

    Only admins can create notification channels.
    """
    if user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only admins can create notification channels")

    channel = NotificationChannel(
        tenant_id=tenant_id,
        name=request.name,
        channel_type=request.channel_type,
        config=request.config,
        created_by=uuid.UUID(user.id)
    )

    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    return channel


@router.get("/notification-channels", response_model=List[NotificationChannelResponse])
async def list_notification_channels(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """List all notification channels for tenant."""
    query = select(NotificationChannel).where(
        NotificationChannel.tenant_id == tenant_id
    ).order_by(NotificationChannel.created_at.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.patch("/notification-channels/{channel_id}", response_model=NotificationChannelResponse)
async def update_notification_channel(
    channel_id: uuid.UUID,
    request: NotificationChannelUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Update a notification channel."""
    if user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only admins can update notification channels")

    query = select(NotificationChannel).where(
        NotificationChannel.id == channel_id,
        NotificationChannel.tenant_id == tenant_id
    )
    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(404, "Notification channel not found")

    if request.name is not None:
        channel.name = request.name
    if request.config is not None:
        channel.config = request.config
    if request.is_active is not None:
        channel.is_active = request.is_active

    await db.commit()
    await db.refresh(channel)

    return channel


@router.delete("/notification-channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Delete a notification channel."""
    if user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only admins can delete notification channels")

    query = select(NotificationChannel).where(
        NotificationChannel.id == channel_id,
        NotificationChannel.tenant_id == tenant_id
    )
    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(404, "Notification channel not found")

    await db.delete(channel)
    await db.commit()


# --- Alert Rules ---

@router.post("/alert-rules", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    request: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Create an alert rule.

    Only admins can create alert rules.
    """
    if user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only admins can create alert rules")

    rule = AlertRule(
        tenant_id=tenant_id,
        name=request.name,
        description=request.description,
        trigger_type=request.trigger_type,
        trigger_config=request.trigger_config,
        workflow_id=request.workflow_id,
        notification_channel_ids=[str(cid) for cid in request.notification_channel_ids],
        cooldown_minutes=request.cooldown_minutes,
        created_by=uuid.UUID(user.id)
    )

    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    logger.info(f"Created alert rule '{request.name}' for tenant {tenant_id}")

    return rule


@router.get("/alert-rules", response_model=List[AlertRuleResponse])
async def list_alert_rules(
    workflow_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """List all alert rules for tenant."""
    query = select(AlertRule).where(AlertRule.tenant_id == tenant_id)

    if workflow_id:
        query = query.where(
            (AlertRule.workflow_id == workflow_id) | (AlertRule.workflow_id.is_(None))
        )

    query = query.order_by(AlertRule.created_at.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: uuid.UUID,
    request: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Update an alert rule."""
    if user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only admins can update alert rules")

    query = select(AlertRule).where(
        AlertRule.id == rule_id,
        AlertRule.tenant_id == tenant_id
    )
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(404, "Alert rule not found")

    if request.name is not None:
        rule.name = request.name
    if request.description is not None:
        rule.description = request.description
    if request.trigger_config is not None:
        rule.trigger_config = request.trigger_config
    if request.notification_channel_ids is not None:
        rule.notification_channel_ids = [str(cid) for cid in request.notification_channel_ids]
    if request.cooldown_minutes is not None:
        rule.cooldown_minutes = request.cooldown_minutes
    if request.is_active is not None:
        rule.is_active = request.is_active

    await db.commit()
    await db.refresh(rule)

    return rule


@router.delete("/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Delete an alert rule."""
    if user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only admins can delete alert rules")

    query = select(AlertRule).where(
        AlertRule.id == rule_id,
        AlertRule.tenant_id == tenant_id
    )
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(404, "Alert rule not found")

    await db.delete(rule)
    await db.commit()


@router.get("/alert-history", response_model=List[dict])
async def list_alert_history(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """List recent alert deliveries."""
    query = select(AlertHistory).where(
        AlertHistory.tenant_id == tenant_id
    ).order_by(AlertHistory.created_at.desc()).limit(limit)

    result = await db.execute(query)
    alerts = result.scalars().all()

    return [
        {
            "id": str(a.id),
            "alert_rule_id": str(a.alert_rule_id),
            "trigger_type": a.trigger_type,
            "message": a.message,
            "notification_status": a.notification_status,
            "created_at": a.created_at.isoformat()
        }
        for a in alerts
    ]
