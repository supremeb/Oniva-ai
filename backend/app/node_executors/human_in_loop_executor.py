"""
Human-in-Loop Node Executor
Pauses workflow for human approval with SLA tracking and escalation
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import hashlib
import json
import logging
from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class HumanInLoopExecutor(BaseNodeExecutor):
    """Executor for Human-in-Loop node - pauses for human approval"""

    node_type = "human_in_loop"

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

            # Check if there's a pre-existing decision (for checkpoint recovery)
            existing_decision = context.inputs.get("__approval_decision")
            if existing_decision:
                return self._handle_existing_decision(
                    context.node_id,
                    existing_decision,
                    options,
                    start_time
                )

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

    def _handle_existing_decision(
        self,
        node_id: str,
        decision: Dict[str, Any],
        options: List[Dict],
        start_time: float
    ) -> NodeExecutionResult:
        """Handle a pre-existing decision (e.g., from checkpoint recovery)"""
        elapsed_ms = int((time.time() - start_time) * 1000)

        return NodeExecutionResult(
            node_id=node_id,
            status="success",
            output={
                "output": decision.get("decision"),
                "approval_id": decision.get("approval_id"),
                "decision": decision.get("decision"),
                "decided_by": decision.get("decided_by"),
                "decided_at": decision.get("decided_at"),
                "comment": decision.get("comment"),
                "recovered_from_checkpoint": True,
            },
            elapsed_ms=elapsed_ms,
            next_node_id=self._get_next_node_for_decision(
                decision.get("decision"),
                options,
                {}
            )
        )

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
