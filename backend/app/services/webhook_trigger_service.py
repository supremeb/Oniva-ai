"""Webhook Trigger Service for inbound event-driven automation.

Enterprise feature: Process inbound webhooks from external systems
(Stripe, GitHub, Shopify, etc.) and trigger workflow executions.
"""
import logging
import hmac
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import jmespath  # For payload mapping
from simpleeval import EvalWithCompoundTypes  # Safe AST-based expression evaluation
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_trigger import WebhookTrigger, WebhookDelivery
from app.models.workflow import WorkflowExecution

logger = logging.getLogger(__name__)


class WebhookTriggerService:
    """Processes inbound webhooks and triggers workflows."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def process_webhook(
        self,
        url_token: str,
        request_body: dict,
        request_headers: dict,
        source_ip: str
    ) -> Dict[str, Any]:
        """Process inbound webhook and trigger workflow if valid.

        Returns dict with status, message, execution_id (if triggered).
        """
        # Lookup webhook trigger
        query = select(WebhookTrigger).where(
            WebhookTrigger.url_token == url_token,
            WebhookTrigger.is_enabled == True
        )
        result = await self.db.execute(query)
        trigger = result.scalar_one_or_none()

        if not trigger:
            await self._log_delivery(
                None, source_ip, request_body, 404, "Webhook not found"
            )
            return {"status": 404, "message": "Webhook not found"}

        # Check rate limit
        if not await self._check_rate_limit(trigger):
            await self._log_delivery(
                trigger.id, source_ip, request_body, 429,
                "Rate limit exceeded", tenant_id=trigger.tenant_id
            )
            return {"status": 429, "message": "Rate limit exceeded"}

        # Verify authentication
        auth_valid = await self._verify_auth(
            trigger, request_body, request_headers, source_ip
        )
        if not auth_valid:
            await self._log_delivery(
                trigger.id, source_ip, request_body, 401,
                "Authentication failed", tenant_id=trigger.tenant_id
            )
            return {"status": 401, "message": "Authentication failed"}

        # Check idempotency
        idempotency_key = self._extract_idempotency_key(request_body, trigger)
        if idempotency_key and await self._is_duplicate(trigger.id, idempotency_key):
            logger.info(f"Duplicate webhook: {idempotency_key}")
            return {"status": 200, "message": "Already processed (idempotent)"}

        # Apply filter
        if trigger.filter_expression:
            if not self._evaluate_filter(trigger.filter_expression, request_body):
                await self._log_delivery(
                    trigger.id, source_ip, request_body, 200,
                    "Filter no match", tenant_id=trigger.tenant_id,
                    filter_matched=False
                )
                return {"status": 200, "message": "Filter did not match"}

        # Map payload to workflow inputs
        workflow_inputs = self._map_payload(trigger.payload_mapping, request_body)

        # Atomic: create execution + log delivery in one transaction
        execution = await self._trigger_workflow(
            workflow_id=trigger.workflow_id,
            inputs=workflow_inputs,
            tenant_id=trigger.tenant_id
        )

        await self._log_delivery(
            trigger.id, source_ip, request_body, 202,
            f"Triggered execution {execution.id}",
            tenant_id=trigger.tenant_id,
            execution_id=execution.id,
            filter_matched=True,
            idempotency_key=idempotency_key
        )

        await self.db.commit()

        return {
            "status": 202,
            "message": "Workflow triggered",
            "execution_id": str(execution.id)
        }

    async def _verify_auth(
        self,
        trigger: WebhookTrigger,
        body: dict,
        headers: dict,
        source_ip: str
    ) -> bool:
        """Verify webhook authentication."""
        auth_method = trigger.auth_method

        if auth_method == 'none':
            return True

        if auth_method == 'hmac':
            # HMAC signature verification
            secret = trigger.auth_config.get('secret', '')
            signature_header = trigger.auth_config.get('signature_header', 'X-Signature')
            provided_sig = headers.get(signature_header.lower(), '')

            import json
            payload = json.dumps(body, separators=(',', ':'))
            expected_sig = hmac.new(
                secret.encode('utf-8'),
                payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()

            expected_with_prefix = f"sha256={expected_sig}"

            return hmac.compare_digest(provided_sig, expected_with_prefix) or \
                   hmac.compare_digest(provided_sig, expected_sig)

        elif auth_method == 'api_key':
            # API key in header
            api_key_header = trigger.auth_config.get('header', 'X-API-Key')
            expected_key = trigger.auth_config.get('key', '')
            provided_key = headers.get(api_key_header.lower(), '')
            return hmac.compare_digest(provided_key, expected_key)

        elif auth_method == 'ip_whitelist':
            # IP whitelist check
            if not trigger.allowed_ip_addresses:
                return False

            from app.auth.dependencies import is_ip_in_whitelist
            allowed_ips = [str(ip) for ip in trigger.allowed_ip_addresses]
            return is_ip_in_whitelist(source_ip, allowed_ips)

        return False

    def _map_payload(self, mapping: dict, payload: dict) -> dict:
        """Map webhook payload to workflow inputs using JMESPath."""
        result = {}

        for workflow_var, jmespath_expr in mapping.items():
            try:
                value = jmespath.search(jmespath_expr, payload)
                result[workflow_var] = value
            except Exception as e:
                logger.warning(f"Payload mapping failed for {workflow_var}: {e}")
                result[workflow_var] = None

        return result

    def _evaluate_filter(self, expression: str, payload: dict) -> bool:
        """Evaluate filter expression against payload.

        Uses simpleeval for safe AST-based evaluation. A new evaluator is
        created per call because EvalWithCompoundTypes has mutable .names,
        which would cause race conditions in async FastAPI if shared.
        """
        try:
            evaluator = EvalWithCompoundTypes(
                functions={"len": len, "str": str, "int": int, "float": float, "bool": bool},
                names={"payload": payload},
            )
            return bool(evaluator.eval(expression))
        except Exception as e:
            logger.error(f"Filter evaluation failed: {e}")
            return False

    def _extract_idempotency_key(self, payload: dict, trigger: WebhookTrigger) -> Optional[str]:
        """Extract idempotency key from payload.

        Common patterns:
        - Stripe: event.id
        - GitHub: delivery ID in header
        - Custom: user-specified JMESPath
        """
        idempotency_expr = trigger.auth_config.get('idempotency_key_path')
        if idempotency_expr:
            try:
                return jmespath.search(idempotency_expr, payload)
            except Exception:
                pass

        # Fallback: hash of entire payload
        import json
        payload_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(payload_str.encode()).hexdigest()[:32]

    async def _is_duplicate(self, trigger_id: uuid.UUID, idempotency_key: str) -> bool:
        """Check if webhook was already processed (24-hour window)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        query = select(WebhookDelivery).where(
            and_(
                WebhookDelivery.webhook_trigger_id == trigger_id,
                WebhookDelivery.idempotency_key == idempotency_key,
                WebhookDelivery.received_at > cutoff
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none() is not None

    async def _check_rate_limit(self, trigger: WebhookTrigger) -> bool:
        """Check if webhook is within rate limit."""
        # Count deliveries in last hour
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)

        query = select(WebhookDelivery).where(
            and_(
                WebhookDelivery.webhook_trigger_id == trigger.id,
                WebhookDelivery.received_at > cutoff
            )
        )
        result = await self.db.execute(query)
        count = len(result.scalars().all())

        return count < trigger.rate_limit_per_hour

    async def _trigger_workflow(
        self,
        workflow_id: uuid.UUID,
        inputs: dict,
        tenant_id: uuid.UUID
    ) -> WorkflowExecution:
        """Trigger workflow execution asynchronously."""
        execution = WorkflowExecution(
            workflow_id=workflow_id,
            user_id=None,  # System-triggered, no user
            tenant_id=tenant_id,
            status='pending',
            trigger='webhook',
            input_data=inputs,
            total_nodes=0  # Will be updated by orchestrator
        )

        self.db.add(execution)
        await self.db.flush()  # Get execution.id without committing
        logger.info(f"Triggered workflow {workflow_id} via webhook, execution_id={execution.id}")

        return execution

    async def _log_delivery(
        self,
        webhook_trigger_id: Optional[uuid.UUID],
        source_ip: str,
        request_body: dict,
        response_status: int,
        error_message: Optional[str] = None,
        tenant_id: Optional[uuid.UUID] = None,
        execution_id: Optional[uuid.UUID] = None,
        filter_matched: Optional[bool] = None,
        idempotency_key: Optional[str] = None
    ):
        """Log webhook delivery for audit trail."""
        if not webhook_trigger_id and not tenant_id:
            return

        delivery = WebhookDelivery(
            tenant_id=tenant_id if tenant_id else uuid.uuid4(),
            webhook_trigger_id=webhook_trigger_id if webhook_trigger_id else uuid.uuid4(),
            source_ip=source_ip,
            request_body=request_body,
            response_status=response_status,
            error_message=error_message,
            workflow_execution_id=execution_id,
            filter_matched=filter_matched,
            idempotency_key=idempotency_key
        )

        self.db.add(delivery)


async def get_webhook_trigger_service(db: AsyncSession) -> WebhookTriggerService:
    """Dependency for webhook trigger service."""
    return WebhookTriggerService(db)
