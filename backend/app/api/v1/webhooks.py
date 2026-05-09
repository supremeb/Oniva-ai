"""Webhook Triggers API.

Enterprise feature: Inbound webhook endpoints for event-driven automation.

Provides:
- Public webhook receiver endpoint (no auth - validated via signature)
- Webhook trigger management (CRUD)
- Delivery logs for debugging

- Phase 2: API Integration
"""
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_no_tenant
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.models.webhook_trigger import WebhookTrigger, generate_webhook_token
from app.services.webhook_trigger_service import WebhookTriggerService

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Schemas ---

class WebhookTriggerCreate(BaseModel):
    """Create webhook trigger request."""
    workflow_id: uuid.UUID
    auth_method: str = Field("hmac", description="hmac, api_key, ip_whitelist, none")
    auth_config: dict = Field(default_factory=dict)
    allowed_ip_addresses: Optional[List[str]] = None
    payload_mapping: dict = Field(default_factory=dict)
    filter_expression: Optional[str] = None
    rate_limit_per_hour: int = 100


class WebhookTriggerUpdate(BaseModel):
    """Update webhook trigger request."""
    auth_config: Optional[dict] = None
    payload_mapping: Optional[dict] = None
    filter_expression: Optional[str] = None
    is_enabled: Optional[bool] = None


class WebhookTriggerResponse(BaseModel):
    """Webhook trigger response."""
    id: uuid.UUID
    workflow_id: uuid.UUID
    url: str  # Full webhook URL
    auth_method: str
    is_enabled: bool
    rate_limit_per_hour: int
    delivery_count: int = 0
    created_at: str

    class Config:
        from_attributes = True


# --- Public Webhook Receiver ---

@router.post("/webhooks/{url_token}")
async def receive_webhook(
    url_token: str,
    request: Request,
    db: AsyncSession = Depends(get_db_no_tenant),  # No auth - validated via webhook signature
):
    """Receive and process inbound webhook.

    PUBLIC ENDPOINT - Authentication via webhook-specific secret.

    Returns 202 Accepted for async processing.
    Actual workflow execution happens asynchronously.
    """
    # Get client IP
    source_ip = request.client.host if request.client else "0.0.0.0"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        source_ip = forwarded.split(",")[0].strip()

    # Parse body
    try:
        body = await request.json()
    except Exception as e:
        return {"status": 400, "message": f"Invalid JSON: {str(e)}"}

    headers = {k.lower(): v for k, v in dict(request.headers).items()}

    # Process webhook
    webhook_service = WebhookTriggerService(db)
    result = await webhook_service.process_webhook(
        url_token=url_token,
        request_body=body,
        request_headers=headers,
        source_ip=source_ip
    )

    return result


# --- Webhook Management (Authenticated) ---

@router.post("/workflows/{workflow_id}/webhooks", response_model=WebhookTriggerResponse)
async def create_webhook_trigger(
    workflow_id: uuid.UUID,
    request: WebhookTriggerCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Create a webhook trigger for a workflow.

    Only workflow owner or admins can create webhook triggers.
    """
    # Verify workflow exists and user has access
    from app.models.workflow import Workflow
    query = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant_id
    )
    result = await db.execute(query)
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(404, "Workflow not found")

    # Check authorization
    if str(workflow.user_id) != user.id and user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only workflow owner or admins can create webhooks")

    # Create webhook trigger
    trigger = WebhookTrigger(
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        url_token=generate_webhook_token(),
        auth_method=request.auth_method,
        auth_config=request.auth_config,
        allowed_ip_addresses=request.allowed_ip_addresses,
        payload_mapping=request.payload_mapping,
        filter_expression=request.filter_expression,
        rate_limit_per_hour=request.rate_limit_per_hour,
        created_by=uuid.UUID(user.id)
    )

    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)

    # Generate full URL
    webhook_url = f"https://api.oniva.ai/api/v1/webhooks/{trigger.url_token}"

    return WebhookTriggerResponse(
        id=trigger.id,
        workflow_id=trigger.workflow_id,
        url=webhook_url,
        auth_method=trigger.auth_method,
        is_enabled=trigger.is_enabled,
        rate_limit_per_hour=trigger.rate_limit_per_hour,
        delivery_count=0,
        created_at=trigger.created_at.isoformat()
    )


@router.get("/workflows/{workflow_id}/webhooks", response_model=List[WebhookTriggerResponse])
async def list_webhook_triggers(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """List all webhook triggers for a workflow."""
    query = select(WebhookTrigger).where(
        WebhookTrigger.workflow_id == workflow_id,
        WebhookTrigger.tenant_id == tenant_id
    ).order_by(WebhookTrigger.created_at.desc())

    result = await db.execute(query)
    triggers = result.scalars().all()

    return [
        WebhookTriggerResponse(
            id=t.id,
            workflow_id=t.workflow_id,
            url=f"https://api.oniva.ai/api/v1/webhooks/{t.url_token}",
            auth_method=t.auth_method,
            is_enabled=t.is_enabled,
            rate_limit_per_hour=t.rate_limit_per_hour,
            delivery_count=0,  # TODO: Count from deliveries
            created_at=t.created_at.isoformat()
        )
        for t in triggers
    ]


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook_trigger(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Delete a webhook trigger."""
    query = select(WebhookTrigger).where(
        WebhookTrigger.id == webhook_id,
        WebhookTrigger.tenant_id == tenant_id
    )
    result = await db.execute(query)
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(404, "Webhook not found")

    # Check authorization
    from app.models.workflow import Workflow
    workflow_query = select(Workflow).where(Workflow.id == trigger.workflow_id)
    workflow_result = await db.execute(workflow_query)
    workflow = workflow_result.scalar_one_or_none()

    if workflow and str(workflow.user_id) != user.id and user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only workflow owner or admins can delete webhooks")

    await db.delete(trigger)
    await db.commit()
