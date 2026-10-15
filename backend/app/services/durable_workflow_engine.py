"""
Durable Workflow Engine - DBOS-wrapped workflow execution with automatic checkpointing.

Part of Phase 1: Production Reliability.

Features:
- Zero data loss on crash via database-backed checkpointing
- Automatic workflow recovery from last checkpoint
- Regulatory audit replay capability
- Backward compatible with existing WorkflowOrchestrator

Updated in Phase 4 to use PostgreSQL-backed checkpoint storage instead of in-memory dict.
"""

from typing import Dict, Any, List, Optional, AsyncGenerator
from uuid import UUID, uuid4
from datetime import datetime
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text

from app.config import get_settings
from app.services.workflow_orchestrator import WorkflowOrchestrator, BranchState
from app.models.workflow_checkpoint import WorkflowCheckpoint
from app.models.workflow import WorkflowExecution
from app.node_executors import (
    NodeExecutionResult,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class DurableWorkflowEngine:
    """
    DBOS-wrapped workflow execution engine with PostgreSQL checkpoint persistence.

    Provides crash-safe workflow execution with automatic checkpointing.
    Falls back to standard WorkflowOrchestrator when USE_DURABLE_WORKFLOWS is False.

    Checkpoints are stored in the `workflow_checkpoints` table and persist across
    server restarts, enabling:
    - Recovery from crashes or pod restarts
    - Audit trail replay for compliance
    - Session teleportation between servers

    Usage:
        engine = DurableWorkflowEngine(db, user_id, tenant_id)
        result = await engine.execute_workflow(workflow_spec, input_variables)

        # After crash, recover with:
        result = await engine.recover_workflow(workflow_uuid)
    """

    def __init__(
        self,
        db: AsyncSession,
        user_id: str,
        tenant_id: UUID,
    ):
        """
        Initialize durable workflow engine.

        Args:
            db: Database session
            user_id: User ID executing the workflow
            tenant_id: Tenant ID for multi-tenancy (required for checkpoint storage)

        Raises:
            ValueError: If tenant_id is None or invalid
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for DurableWorkflowEngine - checkpoints require tenant isolation")

        self.db = db
        self.user_id = user_id
        self.tenant_id = tenant_id
        self._base_orchestrator = WorkflowOrchestrator(db, user_id, tenant_id=tenant_id)

    async def _ensure_rls_context(self) -> None:
        """
        Reset the RLS tenant context in the current transaction.

        PostgreSQL's SET LOCAL only persists for the current transaction.
        After each commit(), the transaction ends and context is lost.
        This method re-sets the context for subsequent operations.
        """
        await self.db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(self.tenant_id)}
        )

    async def execute_workflow(
        self,
        workflow_spec: Dict[str, Any],
        input_variables: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[UUID] = None,
        workflow_version_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """
        Execute a workflow with automatic checkpointing.

        When USE_DURABLE_WORKFLOWS is enabled:
        - Each node execution is checkpointed before running
        - Outputs are checkpointed after completion
        - On crash, workflow can resume from last checkpoint

        Args:
            workflow_spec: React Flow graph specification
            input_variables: Initial workflow variables
            workflow_id: Optional workflow ID for tracking
            workflow_version_id: Optional version UUID for audit trail

        Returns:
            Workflow execution results with checkpoint metadata
        """
        if not settings.USE_DURABLE_WORKFLOWS:
            # Fall back to non-durable execution
            return await self._base_orchestrator.execute_workflow(
                workflow_spec, input_variables, workflow_id, workflow_version_id
            )

        # One UUID per run. Checkpoints are keyed by it, so concurrent runs of the
        # same saved workflow never share or delete each other's checkpoints.
        dbos_workflow_uuid = uuid4()
        # Checkpoint only runs of a saved workflow; ad-hoc runs are not recoverable.
        enable_checkpointing = bool(workflow_id)
        if not enable_checkpointing:
            logger.debug("Ad-hoc workflow execution - checkpointing disabled (no saved workflow)")

        logger.info(
            f"Starting durable workflow execution",
            extra={
                "workflow_id": str(workflow_id),
                "dbos_uuid": str(dbos_workflow_uuid),
                "tenant_id": str(self.tenant_id),
                "checkpointing_enabled": enable_checkpointing,
            }
        )

        return await self._execute_durable_workflow(
            workflow_spec=workflow_spec,
            input_variables=input_variables,
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            dbos_workflow_uuid=dbos_workflow_uuid,
            enable_checkpointing=enable_checkpointing,
        )

    @staticmethod
    def _jsonable(value: Any) -> Any:
        """JSON-safe copy of a value for JSONB checkpoint columns."""
        return json.loads(json.dumps(value, default=str))

    async def _execute_durable_workflow(
        self,
        workflow_spec: Dict[str, Any],
        input_variables: Optional[Dict[str, Any]],
        workflow_id: Optional[UUID],
        workflow_version_id: Optional[UUID],
        dbos_workflow_uuid: UUID,
        resume_from_step: int = 0,
        enable_checkpointing: bool = True,
        execution_log: Optional[WorkflowExecution] = None,
    ) -> Dict[str, Any]:
        """
        Internal durable execution with database-backed checkpointing.

        Each step: create a "running" checkpoint, execute the node, then mark the
        checkpoint completed (or failed). A completed checkpoint also stores the
        post-step state (variables, status, chosen branch) so recovery can restore
        it. A node that returns status "failed" fails the run.

        Args:
            resume_from_step: first step to execute; earlier steps are restored
                from their completed checkpoints (recovery).
            enable_checkpointing: False skips checkpoint rows (ad-hoc workflows).
            execution_log: existing execution record to continue (recovery).
        """
        orch = self._base_orchestrator
        nodes = workflow_spec.get("nodes", [])
        edges = workflow_spec.get("edges", [])
        execution_start_time = datetime.utcnow()

        orch._node_map = orch._build_node_map(nodes)
        orch._edges_by_target = orch._build_edges_by_target(edges)

        if execution_log is not None:
            await self._ensure_rls_context()
            execution_log.status = "running"
            execution_log.error = None
            execution_log.completed_at = None
            await self.db.commit()
        elif workflow_id:
            await self._ensure_rls_context()
            execution_log = WorkflowExecution(
                workflow_id=workflow_id,
                user_id=UUID(self.user_id) if isinstance(self.user_id, str) else self.user_id,
                tenant_id=self.tenant_id,
                dbos_workflow_uuid=dbos_workflow_uuid,
                status="running",
                trigger="manual",
                input_data=input_variables or {},
                total_nodes=len(nodes),
                nodes_executed=0,
                execution_trace=[],
                started_at=execution_start_time,
            )
            self.db.add(execution_log)
            await self.db.commit()

        variables = input_variables.copy() if input_variables else {}
        execution_order = orch._topological_sort(nodes, edges)

        results: Dict[str, NodeExecutionResult] = {}
        node_outputs: Dict[str, Any] = {}
        consumed_node_ids: set = set()
        branches = BranchState(edges)
        total_tokens = 0
        checkpoint_count = 0
        final_status = "completed"
        error_message: Optional[str] = None

        if resume_from_step > 0:
            restored = {
                cp.step_number: cp
                for cp in await self._get_all_checkpoints(dbos_workflow_uuid)
                if cp.status == "completed"
            }
            for step_number in range(min(resume_from_step, len(execution_order))):
                cp = restored.get(step_number)
                if cp is None:
                    raise RuntimeError(
                        f"Cannot resume at step {resume_from_step}: step {step_number} has no completed checkpoint"
                    )
                node_id = execution_order[step_number]
                post = (cp.input_data or {}).get("post", {})
                if post.get("skipped"):
                    branches.mark_skipped(node_id)
                    results[node_id] = orch.skipped_result(node_id)
                    continue
                result = NodeExecutionResult(
                    node_id=node_id,
                    status=post.get("status", "success"),
                    output=cp.output_data,
                    next_node_id=post.get("next_node_id"),
                    tokens_used=post.get("tokens_used"),
                )
                results[node_id] = result
                total_tokens += result.tokens_used or 0
                variables = post.get("variables", variables)
                self._absorb(
                    orch._get_node(nodes, node_id), result, edges,
                    node_outputs, consumed_node_ids, branches,
                )

        for step_number, node_id in enumerate(execution_order):
            if step_number < resume_from_step or node_id in consumed_node_ids:
                continue

            node = orch._get_node(nodes, node_id)
            node_type = node.get("type", "").lower()
            skip = branches.should_skip(node_id)
            inputs = {} if skip else orch._get_node_inputs(node_id, edges, node_outputs)

            checkpoint = None
            if enable_checkpointing:
                checkpoint = await self._create_checkpoint(
                    workflow_uuid=dbos_workflow_uuid,
                    step_number=step_number,
                    step_name=f"{node_type}:{node_id}",
                    node_id=node_id,
                    input_data=self._jsonable({
                        "node_data": node.get("data", {}),
                        "inputs": inputs,
                        "variables": variables,
                    }),
                    workflow_execution_id=execution_log.id if execution_log else None,
                )
                checkpoint_count += 1

            if skip:
                branches.mark_skipped(node_id)
                results[node_id] = orch.skipped_result(node_id)
                if checkpoint:
                    await self._complete_checkpoint(checkpoint, output_data={}, post={"skipped": True})
                continue

            try:
                result = await orch._execute_node(
                    node_id=node_id,
                    node_type=node_type,
                    node_data=node.get("data", {}),
                    inputs=inputs,
                    variables=variables,
                    workflow_spec=workflow_spec,
                )
            except Exception as e:
                if checkpoint:
                    await self._fail_checkpoint(checkpoint, str(e))
                logger.error(
                    "Durable workflow step failed",
                    extra={
                        "workflow_uuid": str(dbos_workflow_uuid),
                        "step": step_number,
                        "node_id": node_id,
                        "error": str(e),
                    },
                )
                if execution_log:
                    await self._finalize_log(
                        execution_log, "failed", str(e), results, total_tokens, execution_start_time
                    )
                raise

            results[node_id] = result
            total_tokens += result.tokens_used or 0

            if result.status == "failed":
                # A failed node fails the run; its checkpoint records the error.
                if checkpoint:
                    await self._fail_checkpoint(checkpoint, result.error or "Node failed")
                final_status = "failed"
                error_message = result.error
                break

            self._absorb(node, result, edges, node_outputs, consumed_node_ids, branches)

            if checkpoint:
                await self._complete_checkpoint(
                    checkpoint,
                    output_data=result.output if result.output else {},
                    post={
                        "status": result.status,
                        "next_node_id": result.next_node_id,
                        "tokens_used": result.tokens_used,
                        "variables": self._jsonable(variables),
                    },
                )

        if execution_log:
            await self._finalize_log(
                execution_log, final_status, error_message, results, total_tokens, execution_start_time
            )

        return {
            "workflow_id": str(workflow_id) if workflow_id else None,
            "dbos_workflow_uuid": str(dbos_workflow_uuid),
            "status": final_status,
            "error": error_message,
            "results": {k: v.model_dump() for k, v in results.items()},
            "executed_nodes": orch.count_executed(results),
            "total_nodes": len(nodes),
            "total_tokens": total_tokens,
            "checkpoints": checkpoint_count,
            "durable": True,
        }

    def _absorb(
        self,
        node: Dict[str, Any],
        result: NodeExecutionResult,
        edges: List[Dict[str, Any]],
        node_outputs: Dict[str, Any],
        consumed_node_ids: set,
        branches: BranchState,
    ) -> None:
        """Fold one completed node's effects into the run state."""
        if result.output:
            node_outputs[result.node_id] = result.output
        if node.get("type", "").lower() == "iteration" and result.status == "success":
            target_node_id = node.get("data", {}).get("targetNodeId")
            if target_node_id:
                consumed_node_ids.update(
                    self._base_orchestrator._get_reachable_nodes(target_node_id, edges)
                )
        branches.record(result.node_id, result)

    async def _finalize_log(
        self,
        execution_log: WorkflowExecution,
        status: str,
        error: Optional[str],
        results: Dict[str, NodeExecutionResult],
        total_tokens: int,
        started: datetime,
    ) -> None:
        await self._ensure_rls_context()
        execution_log.status = status
        execution_log.error = error
        execution_log.nodes_executed = self._base_orchestrator.count_executed(results)
        execution_log.results = {k: v.model_dump() for k, v in results.items()}
        execution_log.total_tokens = total_tokens
        execution_log.completed_at = datetime.utcnow()
        execution_log.duration_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
        await self.db.commit()

    async def recover_workflow(
        self,
        dbos_workflow_uuid: UUID,
        workflow_spec: Dict[str, Any],
        input_variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Recover a crashed or failed run from its last completed checkpoint.

        State (node outputs, variables, branch decisions) is restored from the
        completed checkpoints; the first step without one is re-executed and
        its stale checkpoint (and any later ones) discarded.

        Args:
            dbos_workflow_uuid: The run's UUID (returned by execute_workflow)
            workflow_spec: Original workflow specification
            input_variables: Original input variables

        Returns:
            Workflow execution results after recovery
        """
        if not settings.USE_DURABLE_WORKFLOWS:
            raise RuntimeError("Durable workflows not enabled")

        checkpoints = await self._get_all_checkpoints(dbos_workflow_uuid)
        resume_from_step = 0
        for cp in checkpoints:
            if cp.step_number != resume_from_step or cp.status != "completed":
                break
            resume_from_step += 1

        logger.info(
            "Recovering workflow",
            extra={"dbos_uuid": str(dbos_workflow_uuid), "resume_from_step": resume_from_step},
        )

        await self._discard_checkpoints_from(dbos_workflow_uuid, resume_from_step)

        await self._ensure_rls_context()
        found = await self.db.execute(
            select(WorkflowExecution).where(
                WorkflowExecution.dbos_workflow_uuid == dbos_workflow_uuid,
                WorkflowExecution.tenant_id == self.tenant_id,
            )
        )
        execution_log = found.scalar_one_or_none()

        return await self._execute_durable_workflow(
            workflow_spec=workflow_spec,
            input_variables=input_variables,
            workflow_id=execution_log.workflow_id if execution_log else None,
            workflow_version_id=None,
            dbos_workflow_uuid=dbos_workflow_uuid,
            resume_from_step=resume_from_step,
            enable_checkpointing=True,
            execution_log=execution_log,
        )

    async def replay_workflow(
        self,
        dbos_workflow_uuid: UUID,
    ) -> List[Dict[str, Any]]:
        """
        Replay workflow execution for regulatory audit.

        Returns complete history of all checkpoints with inputs/outputs.
        This is a read-only operation for compliance review.

        Args:
            dbos_workflow_uuid: The workflow UUID to replay

        Returns:
            List of checkpoint data for audit trail
        """
        checkpoints = await self._get_all_checkpoints(dbos_workflow_uuid)

        return [
            {
                "step_number": cp.step_number,
                "step_name": cp.step_name,
                "node_id": cp.node_id,
                "status": cp.status,
                "input_data": cp.input_data,
                "output_data": cp.output_data,
                "error_message": cp.error_message,
                "started_at": cp.started_at.isoformat() if cp.started_at else None,
                "completed_at": cp.completed_at.isoformat() if cp.completed_at else None,
                "elapsed_ms": cp.elapsed_ms,
                "created_at": cp.created_at.isoformat() if cp.created_at else None,
            }
            for cp in checkpoints
        ]

    async def get_checkpoints(
        self,
        dbos_workflow_uuid: UUID,
    ) -> List[Dict[str, Any]]:
        """
        Get all checkpoints for a workflow.

        Args:
            dbos_workflow_uuid: The workflow UUID

        Returns:
            List of checkpoint summaries
        """
        checkpoints = await self._get_all_checkpoints(dbos_workflow_uuid)

        return [cp.to_dict() for cp in checkpoints]

    async def execute_workflow_stream(
        self,
        workflow_spec: Dict[str, Any],
        input_variables: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Execute workflow with streaming updates and database checkpointing.

        Combines streaming execution with durable checkpointing.
        """
        if not settings.USE_DURABLE_WORKFLOWS:
            # Fall back to non-durable streaming
            async for event in self._base_orchestrator.execute_workflow_stream(
                workflow_spec, input_variables
            ):
                yield event
            return

        dbos_workflow_uuid = uuid4()
        orch = self._base_orchestrator

        yield {
            "type": "workflow_started",
            "dbos_workflow_uuid": str(dbos_workflow_uuid),
            "durable": True,
            "timestamp": datetime.utcnow().isoformat(),
        }

        nodes = workflow_spec.get("nodes", [])
        edges = workflow_spec.get("edges", [])
        orch._node_map = orch._build_node_map(nodes)
        orch._edges_by_target = orch._build_edges_by_target(edges)

        variables = input_variables.copy() if input_variables else {}
        execution_order = orch._topological_sort(nodes, edges)

        results: Dict[str, NodeExecutionResult] = {}
        node_outputs: Dict[str, Any] = {}
        consumed_node_ids: set = set()
        branches = BranchState(edges)
        total_tokens = 0
        final_status = "completed"
        error_message: Optional[str] = None

        for step_number, node_id in enumerate(execution_order):
            if node_id in consumed_node_ids:
                continue

            node = orch._get_node(nodes, node_id)
            node_type = node.get("type", "").lower()

            if branches.should_skip(node_id):
                branches.mark_skipped(node_id)
                results[node_id] = orch.skipped_result(node_id)
                yield {
                    "type": "node_complete",
                    "node_id": node_id,
                    "result": results[node_id].model_dump(),
                    "checkpoint_status": "skipped",
                    "timestamp": datetime.utcnow().isoformat(),
                }
                continue

            inputs = orch._get_node_inputs(node_id, edges, node_outputs)

            # Create checkpoint in database
            checkpoint = await self._create_checkpoint(
                workflow_uuid=dbos_workflow_uuid,
                step_number=step_number,
                step_name=f"{node_type}:{node_id}",
                node_id=node_id,
                input_data=self._jsonable({"inputs": inputs, "variables": variables}),
            )

            yield {
                "type": "checkpoint_created",
                "step_number": step_number,
                "node_id": node_id,
                "checkpoint_id": str(checkpoint.id),
                "timestamp": datetime.utcnow().isoformat(),
            }

            yield {
                "type": "node_started",
                "node_id": node_id,
                "node_type": node_type,
                "timestamp": datetime.utcnow().isoformat(),
            }

            try:
                result = await orch._execute_node(
                    node_id=node_id,
                    node_type=node_type,
                    node_data=node.get("data", {}),
                    inputs=inputs,
                    variables=variables,
                    workflow_spec=workflow_spec,
                )
                results[node_id] = result
                total_tokens += result.tokens_used or 0

                if result.status == "failed":
                    error_message = result.error or "Node failed"
                    final_status = "failed"
                    await self._fail_checkpoint(checkpoint, error_message)
                    yield {
                        "type": "node_error",
                        "node_id": node_id,
                        "error": error_message,
                        "checkpoint_status": "failed",
                        "recoverable": True,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                    break

                self._absorb(node, result, edges, node_outputs, consumed_node_ids, branches)

                await self._complete_checkpoint(
                    checkpoint,
                    output_data=result.output if result.output else {},
                    post={
                        "status": result.status,
                        "next_node_id": result.next_node_id,
                        "tokens_used": result.tokens_used,
                        "variables": self._jsonable(variables),
                    },
                )

                yield {
                    "type": "node_complete",
                    "node_id": node_id,
                    "result": result.model_dump(),
                    "checkpoint_status": "completed",
                    "timestamp": datetime.utcnow().isoformat(),
                }

            except Exception as e:
                await self._fail_checkpoint(checkpoint, str(e))
                error_message = str(e)
                final_status = "failed"
                yield {
                    "type": "node_error",
                    "node_id": node_id,
                    "error": str(e),
                    "checkpoint_status": "failed",
                    "recoverable": True,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                break

        yield {
            "type": "complete",
            "dbos_workflow_uuid": str(dbos_workflow_uuid),
            "status": final_status,
            "error": error_message,
            "results": {k: v.model_dump() for k, v in results.items()},
            "executed_nodes": orch.count_executed(results),
            "total_nodes": len(nodes),
            "total_tokens": total_tokens,
            "durable": True,
            "timestamp": datetime.utcnow().isoformat(),
        }

    # =========================================================================
    # Database Operations for Checkpoints (PostgreSQL-backed)
    # =========================================================================

    async def _discard_checkpoints_from(self, dbos_workflow_uuid: UUID, from_step: int) -> None:
        """Delete this run's own checkpoints at or after ``from_step`` (never another run's)."""
        from sqlalchemy import delete

        await self._ensure_rls_context()
        await self.db.execute(
            delete(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_uuid == dbos_workflow_uuid,
                WorkflowCheckpoint.tenant_id == self.tenant_id,
                WorkflowCheckpoint.step_number >= from_step,
            )
        )
        await self.db.commit()

    async def _create_checkpoint(
        self,
        workflow_uuid: UUID,
        step_number: int,
        step_name: str,
        node_id: str,
        input_data: Dict[str, Any],
        workflow_execution_id: Optional[UUID] = None,
    ) -> WorkflowCheckpoint:
        """
        Create a new checkpoint in database with "running" status.

        Args:
            workflow_uuid: DBOS workflow UUID
            step_number: Sequential step number
            step_name: Human-readable step name
            node_id: React Flow node ID
            input_data: Input state for this step

        Returns:
            Created WorkflowCheckpoint instance
        """
        # Re-set RLS context - it's lost after each commit()
        await self._ensure_rls_context()

        checkpoint = WorkflowCheckpoint(
            tenant_id=self.tenant_id,
            workflow_uuid=workflow_uuid,
            workflow_execution_id=workflow_execution_id,
            step_number=step_number,
            step_name=step_name,
            node_id=node_id,
            input_data=input_data,
            status="running",
            started_at=datetime.utcnow(),
        )

        self.db.add(checkpoint)
        await self.db.commit()

        logger.debug(
            f"Checkpoint created: workflow={workflow_uuid} step={step_number}",
            extra={
                "checkpoint_id": str(checkpoint.id),
                "workflow_uuid": str(workflow_uuid),
                "step_number": step_number,
                "node_id": node_id,
            }
        )

        return checkpoint

    async def _complete_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
        output_data: Dict[str, Any],
        post: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Mark checkpoint as completed with output data.

        Args:
            checkpoint: The checkpoint to update
            output_data: Output state after completion
            post: Post-step state kept for recovery (variables, status, branch)
        """
        # Re-set RLS context - it's lost after each commit()
        await self._ensure_rls_context()

        if post is not None:
            # Reassign (not mutate) so SQLAlchemy detects the JSONB change
            checkpoint.input_data = {**(checkpoint.input_data or {}), "post": post}
        checkpoint.mark_completed(self._jsonable(output_data))
        await self.db.commit()

        logger.debug(
            f"Checkpoint completed: step={checkpoint.step_number} elapsed={checkpoint.elapsed_ms}ms",
            extra={
                "checkpoint_id": str(checkpoint.id),
                "workflow_uuid": str(checkpoint.workflow_uuid),
                "elapsed_ms": checkpoint.elapsed_ms,
            }
        )

    async def _fail_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
        error_message: str,
    ) -> None:
        """
        Mark checkpoint as failed with error message.

        Args:
            checkpoint: The checkpoint to update
            error_message: Error details
        """
        # Re-set RLS context - it's lost after each commit()
        await self._ensure_rls_context()

        checkpoint.mark_failed(error_message)
        await self.db.commit()

        logger.debug(
            f"Checkpoint failed: step={checkpoint.step_number} error={error_message[:100]}",
            extra={
                "checkpoint_id": str(checkpoint.id),
                "workflow_uuid": str(checkpoint.workflow_uuid),
                "error": error_message,
            }
        )

    async def _get_last_completed_checkpoint(
        self,
        workflow_uuid: UUID,
    ) -> Optional[WorkflowCheckpoint]:
        """
        Get the last successfully completed checkpoint for a workflow.

        Used for crash recovery to determine where to resume execution.

        Args:
            workflow_uuid: The workflow UUID

        Returns:
            Last completed checkpoint or None if no checkpoints exist
        """
        # Re-set RLS context for query
        await self._ensure_rls_context()

        query = (
            select(WorkflowCheckpoint)
            .where(
                and_(
                    WorkflowCheckpoint.workflow_uuid == workflow_uuid,
                    WorkflowCheckpoint.status == "completed",
                )
            )
            .order_by(WorkflowCheckpoint.step_number.desc())
            .limit(1)
        )

        result = await self.db.execute(query)
        checkpoint = result.scalar_one_or_none()

        if checkpoint:
            logger.debug(
                f"Found last checkpoint: step={checkpoint.step_number}",
                extra={
                    "workflow_uuid": str(workflow_uuid),
                    "step_number": checkpoint.step_number,
                }
            )
        else:
            logger.debug(
                f"No completed checkpoints found for workflow",
                extra={"workflow_uuid": str(workflow_uuid)}
            )

        return checkpoint

    async def _get_all_checkpoints(
        self,
        workflow_uuid: UUID,
    ) -> List[WorkflowCheckpoint]:
        """
        Get all checkpoints for a workflow, ordered by step number.

        Used for audit replay and progress monitoring.

        Args:
            workflow_uuid: The workflow UUID

        Returns:
            List of checkpoints ordered by step number
        """
        # Re-set RLS context for query
        await self._ensure_rls_context()

        query = (
            select(WorkflowCheckpoint)
            .where(
                WorkflowCheckpoint.workflow_uuid == workflow_uuid,
                WorkflowCheckpoint.tenant_id == self.tenant_id,
            )
            .order_by(WorkflowCheckpoint.step_number)
        )

        result = await self.db.execute(query)
        checkpoints = list(result.scalars().all())

        logger.debug(
            f"Retrieved {len(checkpoints)} checkpoints for workflow",
            extra={
                "workflow_uuid": str(workflow_uuid),
                "count": len(checkpoints),
            }
        )

        return checkpoints
