"""
Human-in-Loop Node Executor
Pauses workflow for human approval with SLA tracking and escalation.

Engine runs (plan 003 U21, KTD7): ``prepare`` records one Phase 5 ledger row (``kind = 'review'``, so it shows
in the "Needs attention" inbox) and returns a ``wait`` body with the signal ``decision:{node_id}`` and the
SLA as ``slaUntilEpochMs``. Deciding goes through Phase 5's decision route and RBAC; the decision hook in
``workflow_wait_decisions`` signals the node's task, which wakes into ``finish``. An SLA wake arrives as
``wakeReason = "deadline"``.

Why ``review``: the ledger already has the kind, the inbox already renders it as "review", and RBAC and
expiry are kind-agnostic. A workflow wait differs from an engine tool approval only in how a decision is
delivered, which the hook registry (keyed by kind and run kind) expresses without a new enum value.
"""

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engine import EngineConversation
from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult, PrepareResult
from app.node_executors.wait_executor import DEFAULT_MAX_WAIT_SECONDS
from app.services import workflow_wait_decisions as wait_decisions
from app.services.approval_service import ApprovalService

logger = logging.getLogger(__name__)


class HumanInLoopExecutor(BaseNodeExecutor):
    """Executor for Human-in-Loop node - pauses for human approval"""

    node_type = "human_in_loop"

    # ---- engine path (prepare / finish) -------------------------------------------------

    @staticmethod
    def _options(node_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        return node_data.get("options") or [
            {"id": "approve", "label": "Approve"},
            {"id": "reject", "label": "Reject"},
        ]

    @staticmethod
    def _approver_role(node_data: Dict[str, Any]) -> str:
        """Ledger ``approver_role``: ``admin`` when the node names an admin or owner role, else ``member``."""
        config = node_data.get("approverConfig") or {}
        named = config.get("role") if isinstance(config, dict) else None
        if node_data.get("approverType") == "role" and named in ("admin", "owner"):
            return "admin"
        return "member"

    def _failed(self, context: NodeExecutionContext, message: str, started: float) -> NodeExecutionResult:
        return NodeExecutionResult(
            node_id=context.node_id, status="failed", error=message, elapsed_ms=int((time.time() - started) * 1000)
        )

    async def prepare(self, context: NodeExecutionContext, db: AsyncSession) -> Any:
        started = time.time()
        node_data = context.node_data
        if not context.node_task_id or not context.session_id or not context.tenant_id:
            return self._failed(context, "Human approval needs an engine run (no node task to signal)", started)
        sla_hours = node_data.get("slaHours")
        max_wait = node_data.get("maxWaitSeconds", DEFAULT_MAX_WAIT_SECONDS)
        sla_ms: Optional[int] = None
        if sla_hours not in (None, 0):
            if isinstance(sla_hours, bool) or not isinstance(sla_hours, (int, float)) or sla_hours < 0:
                return self._failed(context, f"Invalid slaHours: {sla_hours!r}", started)
            if sla_hours * 3600 > max_wait:
                return self._failed(
                    context, f"Wait duration ({sla_hours * 3600}s) exceeds maximum ({max_wait}s)", started
                )
            sla_ms = int((datetime.now(timezone.utc) + timedelta(hours=sla_hours)).timestamp() * 1000)

        title = self.substitute_variables(node_data.get("title", "Approval Required"), context.variables, context.inputs)
        description = self.substitute_variables(node_data.get("description", ""), context.variables, context.inputs)
        options = self._options(node_data)
        conversation_id = uuid.UUID(str(context.session_id))
        conv = await db.get(EngineConversation, conversation_id)
        if conv is None or str(conv.tenant_id) != str(context.tenant_id):
            return self._failed(context, "Run conversation not found for the approval", started)

        row = await ApprovalService(db).record_pending(
            tenant_id=context.tenant_id,
            conversation_id=conversation_id,
            tool_task_id=context.node_task_id,
            engine_conversation_id=str(conversation_id),
            run_id=context.workflow_run_id,
            tool_call_id=f"{context.node_id}:r{context.run_attempt or 0}",
            tool_name=title,
            arguments={
                "nodeId": context.node_id,
                "signal": wait_decisions.signal_key(context.node_id),
                "options": [{"id": o.get("id"), "label": o.get("label")} for o in options if isinstance(o, dict)],
                "title": title,
                "requireComment": bool(node_data.get("requireComment", False)),
            },
            reason=description or None,
            rule="workflow_review",
            approver_role=self._approver_role(node_data),
            requested_by=context.user_id,
            expires_at=datetime.fromtimestamp(sla_ms / 1000, timezone.utc) if sla_ms is not None else None,
            project_id=conv.project_id,
            kind=wait_decisions.LEDGER_KIND,
        )
        if row.expires_at is not None:
            # A replay keeps the first deadline: the row, not this call's clock, is the SLA.
            expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
            sla_ms = int(expires.timestamp() * 1000)
        return PrepareResult.wait(
            duration_ms=int(max_wait * 1000),  # the cap bounds a node with no SLA
            signal=wait_decisions.signal_key(context.node_id),
            sla_until_epoch_ms=sla_ms,
        )

    async def finish(self, context: NodeExecutionContext, db: AsyncSession, finish_input: Any) -> NodeExecutionResult:
        started = time.time()
        node_data = context.node_data
        options = self._options(node_data)
        wake = getattr(finish_input, "wakeReason", None)
        payload = getattr(finish_input, "signalPayload", None)
        if wake == "signal":
            if not isinstance(payload, dict) or not isinstance(payload.get("optionId") or payload.get("decision"), str):
                return self._failed(context, "Approval wake carried no decision", started)
            decision = str(payload.get("optionId") or payload["decision"])
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": decision,
                    "approval_id": payload.get("approvalId"),
                    "decision": decision,
                    "decided_by": payload.get("decidedBy"),
                    "decided_at": payload.get("decidedAt"),
                    "comment": payload.get("comment"),
                    "options_presented": options,
                },
                elapsed_ms=int((time.time() - started) * 1000),
                next_node_id=self._get_next_node_for_decision(decision, options, node_data),
            )
        if wake == "deadline":
            return await self._on_expired(context, db, options, started)
        return self._failed(context, "Approval node woke without a decision or a deadline", started)

    async def _on_expired(
        self, context: NodeExecutionContext, db: AsyncSession, options: List[Dict[str, Any]], started: float
    ) -> NodeExecutionResult:
        """SLA (or the wait cap) elapsed: follow the node's escalation config, else fail the node.

        ``escalationEnabled`` with ``escalationConfig.onExpire`` naming an option id decides as that option, by
        ``system:sla``. Otherwise a ``decisionRoutes["sla_expired"]`` entry routes the run. With neither, the
        node fails. The ledger row is expired best-effort; Phase 5's sweep settles any the clock skew leaves.
        """
        node_data = context.node_data
        if context.tenant_id and context.workflow_run_id:
            try:
                await ApprovalService(db).expire_overdue(context.tenant_id)
            except Exception:
                logger.warning("could not expire the approval row for node %s", context.node_id, exc_info=True)
        out = {
            "decision": "sla_expired",
            "decided_by": "system:sla",
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "comment": None,
            "options_presented": options,
        }
        elapsed = int((time.time() - started) * 1000)
        escalation = node_data.get("escalationConfig") or {}
        on_expire = escalation.get("onExpire") if node_data.get("escalationEnabled") else None
        ids = wait_decisions.option_ids(options)
        if isinstance(on_expire, str) and on_expire in ids:
            out.update(output=on_expire, decision="sla_expired", escalated_to=on_expire)
            return NodeExecutionResult(
                node_id=context.node_id, status="success", output=out, elapsed_ms=elapsed,
                next_node_id=self._get_next_node_for_decision(on_expire, options, node_data),
            )
        routes = node_data.get("decisionRoutes") or {}
        if "sla_expired" in routes:
            out["output"] = "sla_expired"
            return NodeExecutionResult(
                node_id=context.node_id, status="success", output=out, elapsed_ms=elapsed,
                next_node_id=routes["sla_expired"],
            )
        return NodeExecutionResult(
            node_id=context.node_id, status="failed", output=out, elapsed_ms=elapsed,
            error="Approval SLA expired before a decision was made",
        )

    # ---- legacy in-process path (removed by U30) ----------------------------------------

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            title = self.substitute_variables(
                node_data.get("title", "Approval Required"),
                context.variables,
                context.inputs
            )
            description = self.substitute_variables(
                node_data.get("description", ""),
                context.variables,
                context.inputs
            )
            approver_type = node_data.get("approverType", "user")
            approver_config = node_data.get("approverConfig", {})
            options = node_data.get("options", [
                {"id": "approve", "label": "Approve"},
                {"id": "reject", "label": "Reject"}
            ])
            sla_hours = node_data.get("slaHours")
            escalation_enabled = node_data.get("escalationEnabled", False)
            escalation_config = node_data.get("escalationConfig", {})
            require_comment = node_data.get("requireComment", False)

            # Create approval request
            approval_id = self._generate_approval_id(context)
            sla_deadline = None
            if sla_hours:
                sla_deadline = datetime.utcnow() + timedelta(hours=sla_hours)

            # Build approval request
            approval_request = {
                "approval_id": approval_id,
                "title": title,
                "description": description,
                "approver_type": approver_type,
                "approver_config": approver_config,
                "options": options,
                "sla_deadline": sla_deadline.isoformat() if sla_deadline else None,
                "escalation_enabled": escalation_enabled,
                "escalation_config": escalation_config,
                "require_comment": require_comment,
                "context_data": {
                    "workflow_id": context.workflow_spec.get("id") if context.workflow_spec else None,
                    "node_id": context.node_id,
                    "user_id": context.user_id,
                    "variables": dict(context.variables),  # Snapshot of current state
                },
                "created_at": datetime.utcnow().isoformat(),
            }

            # In production, this would:
            # 1. Create a record in workflow_approvals table
            # 2. Send notifications to approvers
            # 3. Create DBOS checkpoint for recovery
            # 4. Wait for approval or timeout

            # For now, simulate a pending approval
            logger.info(f"Created approval request: {approval_id}")

            # Record approval in database (stub)
            await self._create_approval_record(approval_request, db)

            # In a real implementation, we would:
            # - Create a DBOS checkpoint here
            # - Return with status="waiting" or similar
            # - Resume when approval is received via API

            # For demonstration, we simulate immediate auto-approval
            # In production, the workflow would pause here
            simulated_decision = options[0]["id"] if options else "approve"

            elapsed_ms = int((time.time() - start_time) * 1000)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": simulated_decision,
                    "approval_id": approval_id,
                    "decision": simulated_decision,
                    "decided_by": "system_auto",  # In production: actual user ID
                    "decided_at": datetime.utcnow().isoformat(),
                    "comment": None,
                    "options_presented": options,
                },
                elapsed_ms=elapsed_ms,
                next_node_id=self._get_next_node_for_decision(
                    simulated_decision,
                    options,
                    node_data
                )
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )

    def _generate_approval_id(self, context: NodeExecutionContext) -> str:
        """Generate unique approval ID"""
        data = f"{context.node_id}_{context.user_id}_{time.time()}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def _get_next_node_for_decision(
        self,
        decision: str,
        options: List[Dict],
        node_data: Dict
    ) -> Optional[str]:
        """Get the next node ID based on the decision made"""
        # The node configuration may have mappings from decision to next node
        decision_routes = node_data.get("decisionRoutes", {})
        if decision in decision_routes:
            return decision_routes[decision]

        # By default, return the decision ID as the handle/output identifier
        # The workflow orchestrator will route based on this
        return decision

    async def _create_approval_record(
        self,
        approval_request: Dict[str, Any],
        db: AsyncSession
    ) -> None:
        """Create approval record in database"""
        # In production, this would insert into workflow_approvals table
        # For now, just log
        logger.info(f"Would create approval record: {approval_request['approval_id']}")

    @staticmethod
    def generate_decision_hash(
        approval_id: str,
        decision: str,
        decided_by: str,
        timestamp: str,
        previous_hash: Optional[str] = None
    ) -> str:
        """Generate cryptographic hash for audit trail"""
        data = json.dumps({
            "approval_id": approval_id,
            "decision": decision,
            "decided_by": decided_by,
            "timestamp": timestamp,
            "previous_hash": previous_hash or ""
        }, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()
