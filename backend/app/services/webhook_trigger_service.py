"""Webhook Trigger Service for inbound event-driven automation.

Enterprise feature: Process inbound webhooks from external systems
(Stripe, GitHub, Shopify, etc.) and trigger workflow executions.
"""
import logging
import hmac
import hashlib
import math
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import jmespath  # For payload mapping
from simpleeval import EvalWithCompoundTypes  # Safe AST-based expression evaluation
from sqlalchemy import func, select, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_trigger import WebhookTrigger, WebhookDelivery, generate_webhook_token
from app.models.workflow import Workflow, WorkflowExecution, WorkflowVersion
from app.services import webhook_secrets

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 86400  # body-hash fallback bucket (KTD8)
DEFAULT_TIMESTAMP_TOLERANCE_S = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TriggerUnowned(ValueError):
    """A webhook trigger has no ``created_by``; the engine needs a user binding."""


class WebhookTriggerService:
    """Processes inbound webhooks and starts workflow or Fleet runs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---- create ---------------------------------------------------------------------------------

    async def create_trigger(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by: Optional[uuid.UUID],
        workflow_id: Optional[uuid.UUID] = None,
        agent_id: Optional[uuid.UUID] = None,
        auth_method: str = "hmac",
        auth_config: Optional[dict] = None,
        allowed_ip_addresses: Optional[list] = None,
        payload_mapping: Optional[dict] = None,
        filter_expression: Optional[str] = None,
        rate_limit_per_hour: int = 100,
    ) -> tuple[WebhookTrigger, Optional[str]]:
        """Create a trigger. Returns ``(trigger, secret)``; the secret is shown once and stored encrypted only."""
        if created_by is None:
            raise TriggerUnowned("a webhook trigger needs an owner (created_by)")
        if (workflow_id is None) == (agent_id is None):
            raise ValueError("exactly one of workflow_id and agent_id is required")
        config = dict(auth_config or {})
        revealed: Optional[str] = None
        if auth_method == "hmac" and not (config.get("secret") or config.get("secret_enc")):
            config["secret"] = revealed = webhook_secrets.generate_secret()
        elif auth_method == "hmac" and config.get("secret"):
            revealed = str(config["secret"])
        trigger = WebhookTrigger(
            tenant_id=tenant_id, workflow_id=workflow_id, agent_id=agent_id,
            url_token=generate_webhook_token(), auth_method=auth_method,
            auth_config=webhook_secrets.seal_auth_config(config),
            allowed_ip_addresses=allowed_ip_addresses, payload_mapping=payload_mapping or {},
            filter_expression=filter_expression, rate_limit_per_hour=rate_limit_per_hour,
            created_by=created_by,
        )
        self.db.add(trigger)
        await self.db.flush()
        return trigger, revealed

    # ---- receive --------------------------------------------------------------------------------

    async def process_webhook(
        self,
        url_token: str,
        raw_body: bytes,
        request_body: Any,
        request_headers: dict,
        source_ip: str,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Verify and dispatch one delivery. ``raw_body`` is what the HMAC covers; ``request_body`` its parsed JSON.

        Returns ``{status, message, ...}``; ``status`` is the HTTP status the route answers with.
        """
        now = now or _utcnow()
        result = await self.db.execute(select(WebhookTrigger).where(
            WebhookTrigger.url_token == url_token, WebhookTrigger.is_enabled == True  # noqa: E712
        ))
        trigger = result.scalar_one_or_none()
        if not trigger:
            await self._log_delivery(None, source_ip, request_body, 404, "Webhook not found")
            return {"status": 404, "message": "Webhook not found"}

        if not await self._check_rate_limit(trigger, now):
            await self._log_delivery(trigger.id, source_ip, request_body, 429,
                                     "Rate limit exceeded", tenant_id=trigger.tenant_id)
            await self.db.commit()
            return {"status": 429, "message": "Rate limit exceeded"}

        if not await self._verify_auth(trigger, raw_body, request_headers, source_ip, now=now):
            await self._log_delivery(trigger.id, source_ip, request_body, 401,
                                     "Authentication failed", tenant_id=trigger.tenant_id)
            await self.db.commit()
            return {"status": 401, "message": "Authentication failed"}

        if trigger.created_by is None:
            await self._log_delivery(trigger.id, source_ip, request_body, 422,
                                     "trigger_unowned", tenant_id=trigger.tenant_id)
            await self.db.commit()
            return {"status": 422, "message": "trigger_unowned", "error": "trigger_unowned"}

        trigger_id, tenant_id = trigger.id, trigger.tenant_id  # plain values: a rolled-back insert expires the row
        key, permanent = self._idempotency_key(trigger, raw_body, request_body, request_headers)
        run_id = self._run_id(trigger, key, permanent, now)
        earlier_ids = self._earlier_run_ids(trigger, key, permanent, now)
        request_id = f"wh:{trigger.id}:{key}"
        audit_key = request_id if len(request_id) <= 255 else f"wh:{trigger.id}:{hashlib.sha256(key.encode()).hexdigest()}"

        if trigger.filter_expression and not self._evaluate_filter(trigger.filter_expression, request_body):
            await self._log_delivery(trigger.id, source_ip, request_body, 200, "Filter no match",
                                     tenant_id=trigger.tenant_id, filter_matched=False)
            await self.db.commit()
            return {"status": 200, "message": "Filter did not match"}

        inputs = self._map_payload(trigger.payload_mapping or {}, request_body)
        if trigger.workflow_id is not None:
            outcome = await self._dispatch_workflow(trigger, run_id, request_id, inputs, earlier_ids, now)
        elif trigger.agent_id is not None:
            outcome = await self._dispatch_agent(trigger, run_id, inputs, earlier_ids, now)
        else:
            outcome = {"status": 422, "message": "trigger has no target", "error": "trigger_no_target"}

        status = outcome["status"]
        execution_id = uuid.UUID(outcome["execution_id"]) if outcome.get("execution_id") else None
        await self._log_delivery(
            trigger_id, source_ip, request_body, status, outcome.get("message"),
            tenant_id=tenant_id, execution_id=execution_id,
            filter_matched=True, idempotency_key=audit_key)
        await self.db.commit()
        return outcome

    # ---- auth -----------------------------------------------------------------------------------

    async def _verify_auth(
        self,
        trigger: WebhookTrigger,
        raw_body: bytes,
        headers: dict,
        source_ip: str,
        *,
        now: Optional[datetime] = None,
    ) -> bool:
        """Verify webhook authentication over the raw request bytes."""
        auth_method = trigger.auth_method
        config = trigger.auth_config or {}

        if auth_method == 'none':
            return True

        if auth_method == 'hmac':
            secret = webhook_secrets.resolve_secret(config, "secret", "secret_enc")
            if not secret:
                return False
            signature_header = config.get('signature_header', 'X-Signature')
            provided_sig = headers.get(signature_header.lower(), '')
            if not provided_sig:
                return False
            signed = raw_body
            ts_header = config.get('timestamp_header')
            if ts_header:
                raw_ts = headers.get(ts_header.lower(), '')
                try:
                    ts = float(raw_ts)
                except (TypeError, ValueError):
                    return False
                tolerance = config.get('timestamp_tolerance_s', DEFAULT_TIMESTAMP_TOLERANCE_S)
                if abs((now or _utcnow()).timestamp() - ts) > tolerance:
                    return False
                signed = f"{raw_ts}.".encode() + raw_body
            expected_sig = hmac.new(secret.encode('utf-8'), signed, hashlib.sha256).hexdigest()
            return hmac.compare_digest(provided_sig, f"sha256={expected_sig}") or \
                hmac.compare_digest(provided_sig, expected_sig)

        elif auth_method == 'api_key':
            api_key_header = config.get('header', 'X-API-Key')
            expected_key = webhook_secrets.resolve_secret(config, "key", "key_enc") or ''
            provided_key = headers.get(api_key_header.lower(), '')
            if not expected_key:
                return False
            return hmac.compare_digest(provided_key, expected_key)

        elif auth_method == 'ip_whitelist':
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

    def _idempotency_key(self, trigger: WebhookTrigger, raw_body: bytes, payload: Any,
                         headers: dict) -> tuple[str, bool]:
        """``(key, permanent)``: the JMESPath into the (signed) body, then the SHA-256 of the raw body (bucketed
        by 24 h, KTD8). A header is unsigned, so replaying a captured body with another header must not start a
        run: it is trusted only when the trigger names ``idempotency_header`` and sets
        ``trust_idempotency_header`` (the sender or its gateway vouches for it)."""
        config = trigger.auth_config or {}
        if config.get('idempotency_header') and config.get('trust_idempotency_header') is True:
            value = headers.get(str(config['idempotency_header']).lower())
            if value:
                return str(value), True
        expr = config.get('idempotency_key_path')
        if expr:
            try:
                found = jmespath.search(expr, payload)
            except Exception:
                found = None
            if found not in (None, ""):
                return str(found), True
        return hashlib.sha256(raw_body).hexdigest(), False

    @staticmethod
    def _run_id(trigger: WebhookTrigger, key: str, permanent: bool, now: datetime) -> uuid.UUID:
        if permanent:
            return uuid.uuid5(trigger.id, key)
        bucket = math.floor(now.timestamp() / WINDOW_SECONDS)
        return uuid.uuid5(trigger.id, f"{key}:{bucket}")

    @staticmethod
    def _earlier_run_ids(trigger: WebhookTrigger, key: str, permanent: bool, now: datetime) -> list[uuid.UUID]:
        """Ids a body-hash delivery may already have run under: the previous bucket (kept if under 24 h old)."""
        if permanent:
            return []
        bucket = math.floor(now.timestamp() / WINDOW_SECONDS)
        return [uuid.uuid5(trigger.id, f"{key}:{bucket - 1}")]

    @staticmethod
    def _recent(stamp: Optional[datetime], now: datetime) -> bool:
        if stamp is None:
            return False
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return now - stamp < timedelta(seconds=WINDOW_SECONDS)

    async def _check_rate_limit(self, trigger: WebhookTrigger, now: Optional[datetime] = None) -> bool:
        """Within the hourly limit? Counts rows in SQL rather than loading them."""
        cutoff = (now or _utcnow()) - timedelta(hours=1)
        count = (await self.db.execute(
            select(func.count()).select_from(WebhookDelivery).where(and_(
                WebhookDelivery.webhook_trigger_id == trigger.id,
                WebhookDelivery.received_at > cutoff,
            ))
        )).scalar() or 0
        return count < trigger.rate_limit_per_hour

    # ---- dispatch -------------------------------------------------------------------------------

    async def _dispatch_workflow(self, trigger: WebhookTrigger, run_id: uuid.UUID, request_id: str,
                                 inputs: dict, earlier_ids: Optional[list] = None,
                                 now: Optional[datetime] = None) -> Dict[str, Any]:
        from fastapi import HTTPException
        from app.services import workflow_engine_runs as engine_runs

        now = now or _utcnow()
        existing = await self.db.get(WorkflowExecution, run_id)
        for earlier in ([] if existing is not None else (earlier_ids or [])):
            row = await self.db.get(WorkflowExecution, earlier)
            if row is not None and self._recent(row.started_at, now):
                existing, run_id = row, earlier
                break
        if existing is not None:
            return self._duplicate(run_id)

        workflow = (await self.db.execute(select(Workflow).where(
            Workflow.id == trigger.workflow_id, Workflow.tenant_id == trigger.tenant_id
        ))).scalar_one_or_none()
        if workflow is None:
            return {"status": 404, "message": "Workflow not found", "error": "workflow_not_found"}
        version = (await self.db.execute(select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.is_active == True  # noqa: E712
        ))).scalars().first()
        spec = (version.spec if version is not None else workflow.spec) or {}

        if await engine_runs.tenant_uses_engine_workflows(self.db, trigger.tenant_id):
            try:
                execution, _task = await engine_runs.start_workflow_run(
                    self.db, workflow, spec, inputs, user_id=trigger.created_by, tenant_id=trigger.tenant_id,
                    trigger="webhook", request_key=request_id,
                    version_id=version.id if version is not None else None, execution_id=run_id,
                    settle_on_failure=False)
            except HTTPException as exc:
                if exc.status_code == 502:
                    # The row stays queued; the reconciler starts it. Answering an error would make the sender's
                    # retry hit that row and be dropped as a duplicate.
                    return {"status": 202, "message": "Accepted; workflow start pending", "execution_id": str(run_id),
                            "deduplicated": False, "start_pending": True}
                return {"status": exc.status_code, "message": "Workflow could not start",
                        "error": exc.detail if isinstance(exc.detail, str) else (exc.detail or {}).get("error"),
                        "execution_id": None}
            return {"status": 202, "message": "Workflow triggered", "execution_id": str(execution.id),
                    "deduplicated": False}

        try:
            async with self.db.begin_nested():
                execution = await self._trigger_workflow(
                    workflow_id=trigger.workflow_id, inputs=inputs, tenant_id=trigger.tenant_id,
                    user_id=trigger.created_by, execution_id=run_id)
        except IntegrityError:  # a concurrent identical delivery inserted the row first
            return self._duplicate(run_id)
        return {"status": 202, "message": "Workflow triggered", "execution_id": str(execution.id),
                "deduplicated": False}

    @staticmethod
    def _duplicate(run_id: uuid.UUID, key: str = "execution_id") -> Dict[str, Any]:
        return {"status": 202, "message": "Already processed (idempotent)", key: str(run_id), "deduplicated": True}

    async def _dispatch_agent(self, trigger: WebhookTrigger, run_id: uuid.UUID, inputs: dict,
                              earlier_ids: Optional[list] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
        from app.models.background_job import BackgroundJob
        from app.models.custom_agent import CustomAgent
        from app.services import fleet_engine
        from app.services.background_dispatch import ConcurrentLimitExceeded, check_and_create_background_job

        now = now or _utcnow()
        if await self.db.get(BackgroundJob, run_id) is not None:
            return self._duplicate(run_id, "job_id")
        for earlier in earlier_ids or []:
            row = await self.db.get(BackgroundJob, earlier)
            if row is not None and self._recent(row.created_at, now):
                return self._duplicate(earlier, "job_id")
        agent = await self.db.get(CustomAgent, trigger.agent_id)
        if agent is None or agent.tenant_id != trigger.tenant_id:
            return {"status": 404, "message": "Agent not found", "error": "agent_not_found"}
        max_concurrent = (agent.resource_constraints or {}).get("max_concurrent", 1)
        use_engine = await fleet_engine.tenant_uses_engine_fleet(self.db, trigger.tenant_id)
        job_input = dict(inputs)
        if use_engine:
            job_input = fleet_engine.mark_engine(job_input)
        try:
            job = await check_and_create_background_job(
                db=self.db, tenant_id=trigger.tenant_id, agent_id=trigger.agent_id, user_id=trigger.created_by,
                max_concurrent=max_concurrent, trigger_type="webhook",
                trigger_info={"type": "webhook", "trigger_id": str(trigger.id)},
                input_data=job_input, job_id=run_id)
        except IntegrityError:  # a concurrent identical delivery inserted the job first
            await self.db.rollback()
            return self._duplicate(run_id, "job_id")
        except ConcurrentLimitExceeded:
            # Documented: the sender gets 202 with queued=false and nothing was started.
            return {"status": 202, "message": "Agent at its concurrent run limit; delivery not started",
                    "queued": False, "deduplicated": False}
        await self.db.commit()
        if use_engine:
            await fleet_engine.start_after_commit(trigger.tenant_id, job.id)
        else:
            import asyncio
            from app.services.background_agent_executor import run_background_agent
            asyncio.create_task(run_background_agent(
                job_id=job.id, agent_id=trigger.agent_id, tenant_id=trigger.tenant_id,
                user_id=trigger.created_by, input_data=inputs))
        return {"status": 202, "message": "Agent triggered", "job_id": str(job.id), "queued": True,
                "deduplicated": False}

    async def _trigger_workflow(
        self,
        workflow_id: uuid.UUID,
        inputs: dict,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
        execution_id: Optional[uuid.UUID] = None,
    ) -> WorkflowExecution:
        """Legacy path (engine flag off): a ``pending`` execution row for the old orchestrator."""
        execution = WorkflowExecution(
            id=execution_id or uuid.uuid4(),
            workflow_id=workflow_id,
            user_id=user_id,
            tenant_id=tenant_id,
            status='pending',
            trigger='webhook',
            input_data=inputs,
            total_nodes=0  # Will be updated by orchestrator
        )

        self.db.add(execution)
        await self.db.flush()
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
