"""
Webhook Service for workflow completion callbacks

Enterprise feature: Enable async notifications when workflows complete.
Supports HMAC-SHA256 signature verification for secure callbacks.

- Phase 2: API Integration
"""

import hashlib
import hmac
import logging
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.security.url_validator import is_url_safe

logger = logging.getLogger(__name__)
settings = get_settings()


class WebhookConfig(BaseModel):
    """Configuration for webhook callbacks."""
    url: str = Field(..., description="Webhook URL to POST to")
    secret: Optional[str] = Field(None, description="HMAC secret for signature")
    auth_type: str = Field("hmac-sha256", description="Authentication type")
    headers: Dict[str, str] = Field(default_factory=dict, description="Custom headers")
    timeout_seconds: int = Field(30, ge=5, le=120, description="Request timeout")
    retry_count: int = Field(3, ge=0, le=5, description="Number of retry attempts")


class WebhookPayload(BaseModel):
    """Standard webhook payload for workflow events."""
    event_type: str = Field(..., description="Event type: workflow.completed, workflow.failed")
    workflow_id: Optional[str] = Field(None, description="Workflow ID")
    execution_id: str = Field(..., description="Execution ID")
    tenant_id: str = Field(..., description="Tenant ID")
    status: str = Field(..., description="Execution status")
    message: Optional[str] = Field(None, description="Status message")
    results: Dict[str, Any] = Field(default_factory=dict, description="Execution results")
    executed_nodes: int = Field(0, description="Number of executed nodes")
    total_nodes: int = Field(0, description="Total number of nodes")
    execution_time_ms: Optional[int] = Field(None, description="Execution time in ms")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class WebhookDeliveryResult(BaseModel):
    """Result of webhook delivery attempt."""
    success: bool
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    delivered_at: Optional[str] = None


class WebhookService:
    """
    Service for sending webhook callbacks on workflow events.

    Features:
    - HMAC-SHA256 signature for payload verification
    - Exponential backoff retry logic
    - Async delivery to avoid blocking execution
    - Detailed delivery tracking
    """

    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
            )
        return self._client

    def _generate_signature(self, payload: str, secret: str) -> str:
        """
        Generate HMAC-SHA256 signature for payload.

        Args:
            payload: JSON string payload
            secret: HMAC secret key

        Returns:
            Hex-encoded signature prefixed with 'sha256='
        """
        signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"sha256={signature}"

    def _verify_signature(self, payload: str, secret: str, signature: str) -> bool:
        """
        Verify HMAC-SHA256 signature.

        Args:
            payload: JSON string payload
            secret: HMAC secret key
            signature: Signature to verify (with 'sha256=' prefix)

        Returns:
            True if signature is valid
        """
        expected = self._generate_signature(payload, secret)
        return hmac.compare_digest(expected, signature)

    async def send_webhook(
        self,
        config: WebhookConfig,
        payload: WebhookPayload
    ) -> WebhookDeliveryResult:
        """
        Send webhook with retry logic and signature.

        Args:
            config: Webhook configuration
            payload: Payload to send

        Returns:
            Delivery result with status and response details
        """
        # SSRF validation: check URL is safe before sending
        ssrf_error = is_url_safe(config.url)
        if ssrf_error:
            logger.warning(f"Webhook URL blocked by SSRF validation: {ssrf_error}")
            return WebhookDeliveryResult(
                success=False,
                error=f"URL validation failed: {ssrf_error}",
                attempts=0,
            )

        payload_json = payload.model_dump_json()

        # Build headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Oniva-Webhook/1.0",
            "X-Webhook-Event": payload.event_type,
            "X-Webhook-Timestamp": payload.timestamp,
        }

        # Add HMAC signature if secret provided
        if config.secret and config.auth_type == "hmac-sha256":
            signature = self._generate_signature(payload_json, config.secret)
            headers["X-Signature"] = signature
            headers["X-Signature-256"] = signature  # GitHub-style header

        # Add custom headers
        headers.update(config.headers)

        client = await self._get_client()
        last_error: Optional[str] = None

        for attempt in range(config.retry_count + 1):
            try:
                response = await client.post(
                    config.url,
                    content=payload_json,
                    headers=headers,
                    timeout=config.timeout_seconds
                )

                # Success on 2xx status codes
                if 200 <= response.status_code < 300:
                    logger.info(
                        f"Webhook delivered successfully to {config.url}",
                        extra={
                            "execution_id": payload.execution_id,
                            "status_code": response.status_code,
                            "attempt": attempt + 1
                        }
                    )
                    return WebhookDeliveryResult(
                        success=True,
                        status_code=response.status_code,
                        response_body=response.text[:1000],  # Truncate long responses
                        attempts=attempt + 1,
                        delivered_at=datetime.now(timezone.utc).isoformat()
                    )

                # Retry on 5xx errors
                if response.status_code >= 500:
                    last_error = f"Server error: {response.status_code}"
                    logger.warning(
                        f"Webhook delivery failed with {response.status_code}, will retry",
                        extra={"attempt": attempt + 1, "url": config.url}
                    )
                else:
                    # Don't retry 4xx errors
                    return WebhookDeliveryResult(
                        success=False,
                        status_code=response.status_code,
                        response_body=response.text[:1000],
                        error=f"Client error: {response.status_code}",
                        attempts=attempt + 1
                    )

            except httpx.TimeoutException as e:
                last_error = f"Timeout: {str(e)}"
                logger.warning(
                    f"Webhook timeout, will retry",
                    extra={"attempt": attempt + 1, "url": config.url}
                )
            except httpx.RequestError as e:
                last_error = f"Request error: {str(e)}"
                logger.warning(
                    f"Webhook request error: {e}, will retry",
                    extra={"attempt": attempt + 1, "url": config.url}
                )
            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"
                logger.error(
                    f"Unexpected webhook error: {e}",
                    extra={"attempt": attempt + 1, "url": config.url}
                )

            # Exponential backoff before retry
            if attempt < config.retry_count:
                delay = (2 ** attempt) * 1.0  # 1s, 2s, 4s...
                await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(
            f"Webhook delivery failed after {config.retry_count + 1} attempts",
            extra={"url": config.url, "error": last_error}
        )
        return WebhookDeliveryResult(
            success=False,
            error=last_error,
            attempts=config.retry_count + 1
        )

    async def send_completion_webhook(
        self,
        config: WebhookConfig,
        execution_id: str,
        tenant_id: str,
        workflow_id: Optional[str],
        status: str,
        results: Dict[str, Any],
        executed_nodes: int = 0,
        total_nodes: int = 0,
        execution_time_ms: Optional[int] = None,
        message: Optional[str] = None
    ) -> WebhookDeliveryResult:
        """
        Send workflow completion webhook.

        Convenience method that builds the payload and sends webhook.
        """
        event_type = "workflow.completed" if status == "success" else "workflow.failed"

        payload = WebhookPayload(
            event_type=event_type,
            workflow_id=workflow_id,
            execution_id=execution_id,
            tenant_id=tenant_id,
            status=status,
            message=message,
            results=results,
            executed_nodes=executed_nodes,
            total_nodes=total_nodes,
            execution_time_ms=execution_time_ms
        )

        return await self.send_webhook(config, payload)

    async def send_webhook_fire_and_forget(
        self,
        config: WebhookConfig,
        payload: WebhookPayload
    ) -> None:
        """
        Send webhook without waiting for result.

        Creates a background task to deliver webhook asynchronously.
        Use when you don't need to track delivery status.
        """
        asyncio.create_task(
            self._send_webhook_background(config, payload)
        )

    async def _send_webhook_background(
        self,
        config: WebhookConfig,
        payload: WebhookPayload
    ) -> None:
        """Background task for webhook delivery."""
        try:
            result = await self.send_webhook(config, payload)
            if not result.success:
                logger.warning(
                    f"Background webhook delivery failed: {result.error}",
                    extra={"url": config.url, "execution_id": payload.execution_id}
                )
        except Exception as e:
            logger.error(
                f"Background webhook error: {e}",
                extra={"url": config.url}
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Singleton instance for reuse
_webhook_service: Optional[WebhookService] = None


def get_webhook_service(db: Optional[AsyncSession] = None) -> WebhookService:
    """Get or create webhook service singleton."""
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = WebhookService(db)
    return _webhook_service
