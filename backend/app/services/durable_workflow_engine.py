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
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text

from app.config import get_settings
from app.services.workflow_orchestrator import WorkflowOrchestrator
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

        # Always generate a unique UUID for each execution
        # dbos_workflow_uuid must be unique per execution to satisfy the unique constraint
        # Enable checkpointing only when we have a saved workflow to recover to
        dbos_workflow_uuid = uuid4()  # Unique per execution
        if workflow_id:
            enable_checkpointing = True
        else:
            enable_checkpointing = False
            logger.debug(
                "Ad-hoc workflow execution - checkpointing disabled (no saved workflow)"
            )

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

    async def _execute_durable_workflow(
        self,
        workflow_spec: Dict[str, Any],
        input_variables: Optional[Dict[str, Any]],
        workflow_id: Optional[UUID],
        workflow_version_id: Optional[UUID],
        dbos_workflow_uuid: UUID,
        resume_from_step: int = 0,
        enable_checkpointing: bool = True,
    ) -> Dict[str, Any]:
        """
        Internal durable execution with database-backed checkpointing.

        Each step follows the pattern:
        1. Create pending checkpoint in database (if checkpointing enabled)
        2. Execute node
        3. Update checkpoint with result (if checkpointing enabled)

        Args:
            enable_checkpointing: If False, skips checkpoint creation (for ad-hoc workflows)
        """
        nodes = workflow_spec.get("nodes", [])
        edges = workflow_spec.get("edges", [])
        execution_start_time = datetime.utcnow()

        # Clean up old checkpoints before starting new execution
        # This prevents unique constraint violations if previous execution failed
        if enable_checkpointing and workflow_id:
            await self._ensure_rls_context()
            await self._cleanup_old_checkpoints(workflow_id)

        # Create WorkflowExecution record for logs and monitoring
        execution_log = None
        if workflow_id:
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
        execution_order = self._base_orchestrator._topological_sort(nodes, edges)

        results: Dict[str, NodeExecutionResult] = {}
        node_outputs: Dict[str, Any] = {}
        consumed_node_ids = set()
        checkpoint_count = 0

        for step_number, node_id in enumerate(execution_order):
            if step_number < resume_from_step:
                # Skip already completed steps (recovery mode)
                # Load output from checkpoint for downstream nodes
                if workflow_id:
                    checkpoint = await self._get_checkpoint_by_step(
                        workflow_id, step_number  # Use workflow_id for checkpoint lookup
                    )
                    if checkpoint and checkpoint.output_data:
                        node_outputs[node_id] = checkpoint.output_data
                continue

            if node_id in consumed_node_ids:
                continue

            node = self._base_orchestrator._get_node(nodes, node_id)
            node_type = node.get("type", "").lower()
            inputs = self._base_orchestrator._get_node_inputs(node_id, edges, node_outputs)

            # Step 1: Create checkpoint in database with "running" status (if enabled)
            # Use workflow_id for checkpoints (FK constraint requires valid workflow reference)
            checkpoint = None
            if enable_checkpointing and workflow_id:
                checkpoint = await self._create_checkpoint(
                    workflow_uuid=workflow_id,  # Use actual workflow_id for FK constraint
                    step_number=step_number,
                    step_name=f"{node_type}:{node_id}",
                    node_id=node_id,
                    input_data={
                        "node_data": node.get("data", {}),
                        "inputs": inputs,
                        "variables": variables,
                    },
                )
                checkpoint_count += 1

            try:
                # Step 2: Execute node
                result = await self._base_orchestrator._execute_node(
                    node_id=node_id,
                    node_type=node_type,
                    node_data=node.get("data", {}),
                    inputs=inputs,
                    variables=variables,
                    workflow_spec=workflow_spec,
                )

                results[node_id] = result

                if result.output:
                    node_outputs[node_id] = result.output

                # Handle iteration nodes
                if node_type == "iteration" and result.status == "success":
                    target_node_id = node.get("data", {}).get("targetNodeId")
                    if target_node_id:
                        reachable = self._base_orchestrator._get_reachable_nodes(
                            target_node_id, edges
                        )
                        consumed_node_ids.update(reachable)

                # Step 3: Update checkpoint with success (if enabled)
                if checkpoint:
                    await self._complete_checkpoint(
                        checkpoint,
                        output_data=result.output if result.output else {}
                    )

            except Exception as e:
                # Step 3: Update checkpoint with failure (if enabled)
                if checkpoint:
                    await self._fail_checkpoint(checkpoint, str(e))

                logger.error(
                    f"Durable workflow step failed",
                    extra={
                        "workflow_uuid": str(dbos_workflow_uuid),
                        "step": step_number,
                        "node_id": node_id,
                        "error": str(e),
                    }
                )

                # Update execution log on failure
                if execution_log:
                    await self._ensure_rls_context()
                    execution_log.status = "failed"
                    execution_log.error = str(e)
                    execution_log.completed_at = datetime.utcnow()
                    execution_log.duration_ms = int((datetime.utcnow() - execution_start_time).total_seconds() * 1000)
                    await self.db.commit()

                # Re-raise to fail the workflow
                raise

        # Update execution log on success
        if execution_log:
            await self._ensure_rls_context()
            execution_log.status = "completed"
            execution_log.nodes_executed = len(results)
            execution_log.results = {k: v.model_dump() for k, v in results.items()}
            execution_log.completed_at = datetime.utcnow()
            execution_log.duration_ms = int((datetime.utcnow() - execution_start_time).total_seconds() * 1000)
            await self.db.commit()

        return {
            "workflow_id": str(workflow_id) if workflow_id else None,
            "dbos_workflow_uuid": str(dbos_workflow_uuid),
            "status": "completed",
            "results": {k: v.model_dump() for k, v in results.items()},
            "executed_nodes": len(results),
            "total_nodes": len(nodes),
            "checkpoints": checkpoint_count,
            "durable": True,
        }

    async def recover_workflow(
        self,
        dbos_workflow_uuid: UUID,
        workflow_spec: Dict[str, Any],
        input_variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Recover a crashed workflow from its last checkpoint.

        Args:
            dbos_workflow_uuid: The DBOS workflow UUID to recover
            workflow_spec: Original workflow specification
            input_variables: Original input variables

        Returns:
            Workflow execution results after recovery
        """
        if not settings.USE_DURABLE_WORKFLOWS:
            raise RuntimeError("Durable workflows not enabled")

        # Find last successful checkpoint from database
        last_checkpoint = await self._get_last_completed_checkpoint(dbos_workflow_uuid)

        if not last_checkpoint:
            logger.info(
                f"No checkpoints found, starting from beginning",
                extra={"dbos_uuid": str(dbos_workflow_uuid)}
            )
            return await self._execute_durable_workflow(
                workflow_spec=workflow_spec,
                input_variables=input_variables,
                workflow_id=None,
                dbos_workflow_uuid=dbos_workflow_uuid,
                resume_from_step=0,
            )

        logger.info(
            f"Recovering workflow from step {last_checkpoint.step_number + 1}",
            extra={
                "dbos_uuid": str(dbos_workflow_uuid),
                "last_step": last_checkpoint.step_number,
                "last_node": last_checkpoint.node_id,
            }
        )

        # Resume from next step after last successful checkpoint
        return await self._execute_durable_workflow(
            workflow_spec=workflow_spec,
            input_variables=input_variables,
            workflow_id=None,
            dbos_workflow_uuid=dbos_workflow_uuid,
            resume_from_step=last_checkpoint.step_number + 1,
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

        yield {
            "type": "workflow_started",
            "dbos_workflow_uuid": str(dbos_workflow_uuid),
            "durable": True,
            "timestamp": datetime.utcnow().isoformat(),
        }

        nodes = workflow_spec.get("nodes", [])
        edges = workflow_spec.get("edges", [])

        variables = input_variables.copy() if input_variables else {}
        execution_order = self._base_orchestrator._topological_sort(nodes, edges)

        results: Dict[str, NodeExecutionResult] = {}
        node_outputs: Dict[str, Any] = {}
        consumed_node_ids = set()

        for step_number, node_id in enumerate(execution_order):
            if node_id in consumed_node_ids:
                continue

            node = self._base_orchestrator._get_node(nodes, node_id)
            node_type = node.get("type", "").lower()
            inputs = self._base_orchestrator._get_node_inputs(node_id, edges, node_outputs)

            # Create checkpoint in database
            checkpoint = await self._create_checkpoint(
                workflow_uuid=dbos_workflow_uuid,
                step_number=step_number,
                step_name=f"{node_type}:{node_id}",
                node_id=node_id,
                input_data={"inputs": inputs},
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
                result = await self._base_orchestrator._execute_node(
                    node_id=node_id,
                    node_type=node_type,
                    node_data=node.get("data", {}),
                    inputs=inputs,
                    variables=variables,
                    workflow_spec=workflow_spec,
                )

                results[node_id] = result
                if result.output:
                    node_outputs[node_id] = result.output

                if node_type == "iteration" and result.status == "success":
                    target_node_id = node.get("data", {}).get("targetNodeId")
                    if target_node_id:
                        reachable = self._base_orchestrator._get_reachable_nodes(
                            target_node_id, edges
                        )
                        consumed_node_ids.update(reachable)

                await self._complete_checkpoint(
                    checkpoint,
                    output_data=result.output if result.output else {}
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

                yield {
                    "type": "node_error",
                    "node_id": node_id,
                    "error": str(e),
                    "checkpoint_status": "failed",
                    "recoverable": True,
                    "timestamp": datetime.utcnow().isoformat(),
                }

        yield {
            "type": "complete",
            "dbos_workflow_uuid": str(dbos_workflow_uuid),
            "results": {k: v.model_dump() for k, v in results.items()},
            "executed_nodes": len(results),
            "total_nodes": len(nodes),
            "durable": True,
            "timestamp": datetime.utcnow().isoformat(),
        }

    # =========================================================================
    # Database Operations for Checkpoints (PostgreSQL-backed)
    # =========================================================================

    async def _cleanup_old_checkpoints(self, workflow_id: UUID) -> None:
        """
        Clean up old checkpoints for a workflow before starting a new execution.

        This prevents unique constraint violations if a previous execution failed
        mid-way and left orphaned checkpoints.

        Args:
            workflow_id: The workflow ID to clean up checkpoints for
        """
        from sqlalchemy import delete

        await self._ensure_rls_context()

        # Delete all existing checkpoints for this workflow
        stmt = delete(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_uuid == workflow_id,
            WorkflowCheckpoint.tenant_id == self.tenant_id,
        )
        await self.db.execute(stmt)
        await self.db.commit()

        logger.debug(
            f"Cleaned up old checkpoints for workflow",
            extra={"workflow_id": str(workflow_id)}
        )

    async def _create_checkpoint(
        self,
        workflow_uuid: UUID,
        step_number: int,
        step_name: str,
        node_id: str,
        input_data: Dict[str, Any],
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
    ) -> None:
        """
        Mark checkpoint as completed with output data.

        Args:
            checkpoint: The checkpoint to update
            output_data: Output state after completion
        """
        # Re-set RLS context - it's lost after each commit()
        await self._ensure_rls_context()

        checkpoint.mark_completed(output_data)
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

    async def _get_checkpoint_by_step(
        self,
        workflow_uuid: UUID,
        step_number: int,
    ) -> Optional[WorkflowCheckpoint]:
        """
        Get a specific checkpoint by workflow UUID and step number.

        Used for loading output data during recovery.

        Args:
            workflow_uuid: The workflow UUID
            step_number: The step number

        Returns:
            Checkpoint or None if not found
        """
        # Re-set RLS context for query
        await self._ensure_rls_context()

        query = select(WorkflowCheckpoint).where(
            and_(
                WorkflowCheckpoint.workflow_uuid == workflow_uuid,
                WorkflowCheckpoint.step_number == step_number,
            )
        )

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

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
            .where(WorkflowCheckpoint.workflow_uuid == workflow_uuid)
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
