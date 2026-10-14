"""
Workflow Orchestrator - Executes workflows using node executors
Updated to use the new node executor registry for Dify-compatible nodes

C2 Enhancement: State machine integration for deterministic tool access.
When USE_STATE_MACHINE is enabled, workflows track state transitions and
pass workflow state to node executors for tool filtering.
"""

from typing import Dict, Any, List, Optional, AsyncGenerator
from uuid import UUID, uuid4
from collections import defaultdict, deque
import asyncio
import time
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.node_executors import (
    get_executor,
    NodeExecutionContext,
    NodeExecutionResult,
)
from app.node_executors.adornments import (
    parse_adornments,
    execute_with_adornments,
)
from app.models.workflow import WorkflowExecution, Workflow
from app.config import get_settings
from app.agents.state_machine import WorkflowState
from app.schemas.classification import DataClassification

import logging

logger = logging.getLogger(__name__)
settings = get_settings()

class BranchState:
    """Tracks which edges/nodes are live after IF/ELSE and classifier decisions.

    An edge with a non-empty ``sourceHandle`` that differs from the branch the
    source node chose is *not taken*. A node whose incoming edges are all dead
    (not taken, or coming from a skipped node) is skipped; a node with at least
    one live incoming edge runs exactly once (join semantics).
    """

    def __init__(self, edges: List[Dict[str, Any]]):
        self._edges = edges
        self._incoming: Dict[str, List[int]] = defaultdict(list)
        for idx, edge in enumerate(edges):
            self._incoming[edge["target"]].append(idx)
        self.not_taken: set = set()
        self.skipped: set = set()

    def record(self, node_id: str, result: NodeExecutionResult) -> None:
        """Register the branch chosen by a node (no-op for non-branching nodes)."""
        if result.status != "success" or result.next_node_id is None:
            return
        for idx, edge in enumerate(self._edges):
            handle = edge.get("sourceHandle")
            if edge["source"] == node_id and handle and handle != result.next_node_id:
                self.not_taken.add(idx)

    def should_skip(self, node_id: str) -> bool:
        incoming = self._incoming.get(node_id)
        if not incoming:
            return False
        return all(
            self._edges[i]["source"] in self.skipped or i in self.not_taken
            for i in incoming
        )

    def mark_skipped(self, node_id: str) -> None:
        self.skipped.add(node_id)


class WorkflowOrchestrator:
    """
    Orchestrates multi-node workflows with support for:
    - All Dify node types (LLM, Code, HTTP, IF/ELSE, etc.)
    - Branching (IF/ELSE, Classifier)
    - Variable passing between nodes
    - Streaming execution updates
    - Execution logging for persistence and monitoring
    """

    def __init__(self, db: AsyncSession, user_id: str, tenant_id: UUID = None, trigger: str = "manual"):
        self.db = db
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.trigger = trigger

        # C2: State machine tracking for deterministic tool access
        self._current_workflow_state: Optional[WorkflowState] = None
        self._session_id: Optional[str] = None

        # Track max classification across workflow
        self._max_classification: DataClassification = DataClassification.INTERNAL
        # Store results for classification propagation
        self._execution_results: Dict[str, NodeExecutionResult] = {}

        # Retry budget: max total retries across all tool calls per workflow run
        from app.config import get_settings
        self._retry_budget: int = get_settings().WORKFLOW_RETRY_BUDGET
        self._retries_used: int = 0

        # Enterprise: Execution cancellation support
        self._running_executions: Dict[UUID, "asyncio.Task"] = {}
        self._cancellation_flags: Dict[UUID, "asyncio.Event"] = {}

    def consume_retry(self) -> bool:
        """Consume one retry from the workflow budget.

        Returns True if a retry is available, False if budget exhausted.
        Called by tool retry logic (tenacity) before each retry attempt.
        """
        if self._retries_used >= self._retry_budget:
            return False
        self._retries_used += 1
        return True

    @property
    def retries_remaining(self) -> int:
        return max(0, self._retry_budget - self._retries_used)

    def _infer_state_from_node(self, node: dict) -> WorkflowState:
        """
        Infer workflow state from node type for state machine (C2 fix).

        Maps node types to appropriate WorkflowState values to enable
        proper tool filtering when USE_STATE_MACHINE is enabled.

        Args:
            node: Node dictionary with 'type' field

        Returns:
            Appropriate WorkflowState for the node type
        """
        node_type = node.get("type", "").lower()

        state_mapping = {
            # Starting nodes
            "start": WorkflowState.INITIALIZED,
            "input": WorkflowState.INITIALIZED,
            "variable_aggregator": WorkflowState.INITIALIZED,

            # Data gathering nodes
            "data_retrieval": WorkflowState.DATA_GATHERING,
            "http_request": WorkflowState.DATA_GATHERING,
            "knowledge_retrieval": WorkflowState.DATA_GATHERING,
            "question_classifier": WorkflowState.DATA_GATHERING,

            # Analysis nodes
            "llm": WorkflowState.ANALYSIS,
            "analysis": WorkflowState.ANALYSIS,
            "code": WorkflowState.ANALYSIS,
            "template": WorkflowState.ANALYSIS,
            "parameter_extractor": WorkflowState.ANALYSIS,

            # Compliance nodes
            "compliance": WorkflowState.COMPLIANCE_CHECK,
            "compliance_check": WorkflowState.COMPLIANCE_CHECK,

            # Conditional nodes (use analysis state)
            "ifelse": WorkflowState.ANALYSIS,
            "classifier": WorkflowState.ANALYSIS,
            "iteration": WorkflowState.ANALYSIS,

            # Output nodes
            "output": WorkflowState.REPORT_GENERATION,
            "answer": WorkflowState.REPORT_GENERATION,
            "end": WorkflowState.COMPLETED,
        }

        return state_mapping.get(node_type, WorkflowState.ANALYSIS)

    async def cancel_execution(self, execution_id: UUID) -> bool:
        """Cancel a running workflow execution.

        Sets cancellation flag that will be checked before each node execution.
        Current node completes gracefully, then execution stops.

        Args:
            execution_id: ID of the execution to cancel

        Returns:
            True if cancellation flag was set, False if execution not found

        Raises:
            None - graceful cancellation
        """
        cancel_event = self._cancellation_flags.get(execution_id)
        if cancel_event:
            logger.info(f"Setting cancellation flag for execution {execution_id}")
            cancel_event.set()

            # Update database status
            from sqlalchemy import select
            query = select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
            result = await self.db.execute(query)
            execution = result.scalar_one_or_none()

            if execution and execution.status == 'running':
                execution.status = 'cancelling'  # Will become 'cancelled' when actually stopped
                await self.db.commit()

                # Re-establish RLS context after commit (commit destroys SET LOCAL)
                if self.tenant_id:
                    await self.db.execute(
                        text("SELECT set_config('app.current_tenant', :tid, true)"),
                        {"tid": str(self.tenant_id)}
                    )

            return True
        else:
            logger.warning(f"Cannot cancel execution {execution_id}: not in running executions")
            return False

    def _is_cancelled(self, execution_id: UUID) -> bool:
        """Check if execution has been cancelled.

        Args:
            execution_id: ID of the execution to check

        Returns:
            True if cancellation flag is set
        """
        cancel_event = self._cancellation_flags.get(execution_id)
        return cancel_event.is_set() if cancel_event else False

    async def execute_workflow(
        self,
        workflow_spec: Dict[str, Any],
        input_variables: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[UUID] = None,
        workflow_version_id: Optional[UUID] = None,
        save_logs: bool = True
    ) -> Dict[str, Any]:
        """
        Execute a workflow from React Flow graph specification

        Args:
            workflow_spec: {
                'nodes': [{id, type, data, position}],
                'edges': [{source, target, sourceHandle?, targetHandle?}]
            }
            input_variables: Initial workflow variables (e.g., from TestRunPanel)
            workflow_id: Optional workflow ID for tracking
            workflow_version_id: Optional version UUID for audit trail
            save_logs: Whether to save execution logs to database

        Returns:
            Execution results for all nodes
        """
        # Pre-execution validation: cycles, complexity limits, node types
        from app.services.workflow_validator import WorkflowValidator
        WorkflowValidator.validate_workflow(workflow_spec)

        nodes = workflow_spec.get("nodes", [])
        edges = workflow_spec.get("edges", [])
        start_time = time.time()

        # Build O(1) lookup maps for nodes and edges
        self._node_map = self._build_node_map(nodes)
        self._edges_by_target = self._build_edges_by_target(edges)

        # Normalize workflow_id: ensure empty strings become None
        normalized_workflow_id = workflow_id if workflow_id else None

        # Create execution log record if tenant_id is available
        execution_log = None
        if save_logs and self.tenant_id:
            # Set tenant context for RLS using parameterized set_config (LOCAL=true)
            await self.db.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(self.tenant_id)}
            )

            execution_log = WorkflowExecution(
                workflow_id=normalized_workflow_id,
                user_id=UUID(self.user_id) if isinstance(self.user_id, str) else self.user_id,
                tenant_id=self.tenant_id,
                status="running",
                trigger=self.trigger,
                input_data=input_variables or {},
                total_nodes=len(nodes),
                nodes_executed=0,
                execution_trace=[]
            )
            self.db.add(execution_log)
            await self.db.commit()
            await self.db.refresh(execution_log)

            # Enterprise: Register execution for cancellation support
            execution_id = execution_log.id
            cancel_event = asyncio.Event()
            self._cancellation_flags[execution_id] = cancel_event
            # Note: asyncio.current_task() would be registered here, but we use Event for simplicity
        else:
            execution_id = None

        # Initialize workflow variables
        variables = input_variables.copy() if input_variables else {}

        # C2: Initialize state machine if enabled
        if settings.USE_STATE_MACHINE:
            self._current_workflow_state = WorkflowState.INITIALIZED
            self._session_id = str(uuid4())
            variables["sys.workflow_state"] = self._current_workflow_state.value
            variables["sys.session_id"] = self._session_id

        # Build dependency graph
        execution_order = self._topological_sort(nodes, edges)

        # Track results per node
        results: Dict[str, NodeExecutionResult] = {}
        node_outputs: Dict[str, Any] = {}
        consumed_node_ids = set()
        execution_trace = []
        total_tokens = 0
        final_status = "completed"
        error_message = None
        branches = BranchState(edges)

        try:
            for node_id in execution_order:
                if node_id in consumed_node_ids:
                    continue

                # Enterprise: Check for cancellation before each node
                if execution_log and execution_log.id:
                    # In-memory flag checked on every node (zero-cost)
                    if self._is_cancelled(execution_log.id):
                        logger.info(f"Execution {execution_log.id} cancelled by user at node {node_id}")
                        final_status = "cancelled"
                        error_message = "Execution cancelled by user"
                        break  # Stop execution gracefully

                    # DB status check every 5th node (reduces 95 SELECTs to ~19)
                    if len(results) % 5 == 0:
                        await self.db.refresh(execution_log)
                        if execution_log.status == 'cancelled':
                            logger.info(f"Execution {execution_log.id} cancelled via API at node {node_id}")
                            final_status = "cancelled"
                            error_message = "Execution cancelled by user"
                            break

                node = self._get_node(nodes, node_id)
                node_type = node.get("type", "").lower()
                node_name = node.get("data", {}).get("label") or node.get("data", {}).get("name") or node_type

                # Untaken IF/ELSE or classifier branch: skip, do not execute
                if branches.should_skip(node_id):
                    branches.mark_skipped(node_id)
                    results[node_id] = self.skipped_result(node_id)
                    continue

                # Get inputs from predecessor nodes
                inputs = self._get_node_inputs(node_id, edges, node_outputs)

                # Track node start time
                node_start_time = time.time()

                # C2: Update workflow state based on node type
                if settings.USE_STATE_MACHINE:
                    node_state = self._infer_state_from_node(node)
                    self._current_workflow_state = node_state
                    variables["sys.workflow_state"] = node_state.value

                # Execute node
                result = await self._execute_node(
                    node_id=node_id,
                    node_type=node_type,
                    node_data=node.get("data", {}),
                    inputs=inputs,
                    variables=variables,
                    workflow_spec=workflow_spec
                )

                # Calculate node duration
                node_duration_ms = int((time.time() - node_start_time) * 1000)

                results[node_id] = result

                # Build execution trace step
                trace_step = {
                    "step_id": f"step_{len(execution_trace) + 1}",
                    "node_id": node_id,
                    "node_type": node_type,
                    "node_name": node_name,
                    "status": result.status,
                    "started_at": datetime.utcnow().isoformat(),
                    "completed_at": datetime.utcnow().isoformat(),
                    "duration_ms": result.elapsed_ms or node_duration_ms,
                    "input": inputs if inputs else None,
                    "output": result.output if result.output else None,
                    "error": result.error,
                    "token_usage": self.token_usage_of(result),
                    "reasoning": result.reasoning,
                }
                execution_trace.append(trace_step)

                # Accumulate token usage
                total_tokens += result.tokens_used or 0

                # Store output for downstream nodes
                if result.output:
                    node_outputs[node_id] = result.output

                # If this is an iteration node, mark all reachable nodes as consumed
                if node_type == "iteration" and result.status == "success":
                    target_node_id = node.get("data", {}).get("targetNodeId")
                    if target_node_id:
                        reachable = self._get_reachable_nodes(target_node_id, edges)
                        consumed_node_ids.update(reachable)

                # Branching (IF/ELSE, Classifier): edges to untaken handles go dead
                branches.record(node_id, result)

                # Check for node failure
                if result.status == "failed":
                    final_status = "failed"
                    error_message = result.error
                    break

        except Exception as e:
            final_status = "failed"
            error_message = str(e)
        finally:
            # Enterprise: Cleanup cancellation tracking
            if execution_log and execution_log.id in self._cancellation_flags:
                self._cancellation_flags.pop(execution_log.id, None)
                self._running_executions.pop(execution_log.id, None)

        # Calculate total duration
        total_duration_ms = int((time.time() - start_time) * 1000)

        # Update execution log with final results
        if execution_log:
            execution_log.status = final_status
            execution_log.results = {k: v.model_dump() for k, v in results.items()}
            execution_log.error = error_message
            execution_log.nodes_executed = self.count_executed(results)
            execution_log.total_tokens = total_tokens
            execution_log.duration_ms = total_duration_ms
            execution_log.execution_trace = execution_trace
            execution_log.completed_at = datetime.utcnow()
            await self.db.commit()

        # Phase 2: Extract entities from workflow outputs (fire-and-forget)
        if final_status == "completed" and workflow_id and self.tenant_id:
            asyncio.create_task(
                self._extract_entities_background(
                    workflow_id=workflow_id,
                    execution_id=execution_log.id if execution_log else None,
                    results=results,
                    node_outputs=node_outputs,
                )
            )

        return {
            "workflow_id": str(workflow_id) if workflow_id else None,
            "execution_id": str(execution_log.id) if execution_log else None,
            "status": final_status,
            "results": {k: v.model_dump() for k, v in results.items()},
            "executed_nodes": self.count_executed(results),
            "total_nodes": len(nodes),
            "total_tokens": total_tokens,
            "duration_ms": total_duration_ms,
            "error": error_message,
        }

    @staticmethod
    def skipped_result(node_id: str) -> NodeExecutionResult:
        """Result recorded for a node on an untaken branch (never executed)."""
        return NodeExecutionResult(
            node_id=node_id,
            status="skipped",
            output=None,
            reasoning="Skipped: not on the branch taken",
        )

    @staticmethod
    def count_executed(results: Dict[str, NodeExecutionResult]) -> int:
        """Nodes that actually ran (skipped-branch nodes do not count)."""
        return sum(1 for r in results.values() if r.status != "skipped")

    @staticmethod
    def token_usage_of(result: NodeExecutionResult) -> Optional[Dict[str, int]]:
        """Trace-friendly token usage built from the executor's ``tokens_used``."""
        return {"total": result.tokens_used} if result.tokens_used else None

    def _get_input_node_ids(self, node_id: str, edges: List[Dict]) -> List[str]:
        """Get list of predecessor node IDs for a given node."""
        return [edge["source"] for edge in edges if edge["target"] == node_id]

    async def _execute_node(
        self,
        node_id: str,
        node_type: str,
        node_data: Dict[str, Any],
        inputs: Dict[str, Any],
        variables: Dict[str, Any],
        workflow_spec: Optional[Dict[str, Any]] = None
    ) -> NodeExecutionResult:
        """Execute a single node using its executor, with optional adornments"""
        # Create execution context
        context = NodeExecutionContext(
            node_id=node_id,
            node_type=node_type,
            node_data=node_data,
            inputs=inputs,
            variables=variables,
            user_id=self.user_id,
            orchestrator=self,
            workflow_spec=workflow_spec,
            tenant_id=str(self.tenant_id) if self.tenant_id else None,
            db=self.db,
        )

        # Get executor for this node type
        executor = get_executor(node_type)

        # Calculate input classification (high-water mark)
        edges = workflow_spec.get("edges", []) if workflow_spec else []
        input_node_ids = self._get_input_node_ids(node_id, edges)

        input_classifications = [
            self._execution_results[input_id].classification
            for input_id in input_node_ids
            if input_id in self._execution_results
        ]
        input_classification = max(
            input_classifications,
            default=DataClassification.INTERNAL
        )

        if executor:
            try:
                # Check for adornments configuration (Vellum-style decorators)
                # Adornments wrap execution with retry, try-catch, timeout, caching behavior
                adornments_config = node_data.get("adornments", [])

                # Use export control wrapper if enabled
                if settings.USE_DATA_CLASSIFICATION and hasattr(executor, 'execute_with_export_control'):
                    if adornments_config:
                        # Adornments with export control
                        adornments = parse_adornments(adornments_config)
                        result = await execute_with_adornments(
                            executor, context, self.db, adornments
                        )
                        # Check export control after adornment execution
                        if executor.makes_external_calls and not input_classification.can_export_to_external():
                            # Block was handled in adornment; just ensure classification is set
                            result.classification = input_classification
                    else:
                        # Export control wrapper
                        result = await executor.execute_with_export_control(
                            context, self.db, input_classification
                        )
                elif adornments_config:
                    # Parse and apply adornments in order (first = outermost wrapper)
                    adornments = parse_adornments(adornments_config)
                    result = await execute_with_adornments(
                        executor, context, self.db, adornments
                    )
                else:
                    # Direct execution without adornments
                    result = await executor.execute(context, self.db)

                # Propagate classification (high-water mark)
                result.classification = max(result.classification, input_classification)

                # Track workflow max classification
                self._max_classification = max(self._max_classification, result.classification)

                # Store result for propagation to downstream nodes
                self._execution_results[node_id] = result

                return result

            except Exception as e:
                result = NodeExecutionResult(
                    node_id=node_id,
                    status="failed",
                    error=str(e),
                    elapsed_ms=0,
                    classification=input_classification,  # Propagate input classification
                )
                self._execution_results[node_id] = result
                return result
        else:
            # Unknown node type - pass through
            result = NodeExecutionResult(
                node_id=node_id,
                status="skipped",
                output={"message": f"No executor for node type: {node_type}"},
                elapsed_ms=0,
                classification=input_classification,  # Propagate input classification
            )
            self._execution_results[node_id] = result
            return result

    async def execute_workflow_stream(
        self,
        workflow_spec: Dict[str, Any],
        input_variables: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[UUID] = None,
        save_logs: bool = True
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Execute workflow with streaming updates
        Yields progress events for each node
        Also persists execution logs to database for monitoring
        """
        nodes = workflow_spec.get("nodes", [])
        edges = workflow_spec.get("edges", [])
        start_time = time.time()

        # Build O(1) lookup maps for nodes and edges
        self._node_map = self._build_node_map(nodes)
        self._edges_by_target = self._build_edges_by_target(edges)

        # Normalize workflow_id: ensure empty strings become None
        normalized_workflow_id = workflow_id if workflow_id else None

        # Create execution log record if tenant_id is available and save_logs is True
        execution_log = None
        if save_logs and self.tenant_id:
            # Set tenant context for RLS using parameterized set_config (LOCAL=true)
            await self.db.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(self.tenant_id)}
            )

            execution_log = WorkflowExecution(
                workflow_id=normalized_workflow_id,
                user_id=UUID(self.user_id) if isinstance(self.user_id, str) else self.user_id,
                tenant_id=self.tenant_id,
                status="running",
                trigger=self.trigger,
                input_data=input_variables or {},
                total_nodes=len(nodes),
                nodes_executed=0,
                execution_trace=[]
            )
            self.db.add(execution_log)
            await self.db.commit()
            await self.db.refresh(execution_log)

        variables = input_variables.copy() if input_variables else {}

        # C2: Initialize state machine if enabled
        if settings.USE_STATE_MACHINE:
            self._current_workflow_state = WorkflowState.INITIALIZED
            self._session_id = str(uuid4())
            variables["sys.workflow_state"] = self._current_workflow_state.value
            variables["sys.session_id"] = self._session_id

        execution_order = self._topological_sort(nodes, edges)

        results: Dict[str, NodeExecutionResult] = {}
        node_outputs: Dict[str, Any] = {}
        consumed_node_ids = set()
        execution_trace = []
        total_tokens = 0
        final_status = "completed"
        error_message = None
        branches = BranchState(edges)

        for node_id in execution_order:
            if node_id in consumed_node_ids:
                continue

            if branches.should_skip(node_id):
                branches.mark_skipped(node_id)
                skipped = self.skipped_result(node_id)
                results[node_id] = skipped
                yield {
                    "type": "node_complete",
                    "node_id": node_id,
                    "result": skipped.model_dump(),
                    "timestamp": datetime.utcnow().isoformat()
                }
                continue

            # Yield progress update
            yield {
                "type": "progress",
                "node_id": node_id,
                "status": "running",
                "timestamp": datetime.utcnow().isoformat()
            }

            node = self._get_node(nodes, node_id)
            node_type = node.get("type", "").lower()
            inputs = self._get_node_inputs(node_id, edges, node_outputs)

            # Yield input received event
            yield {
                "type": "node_input_received",
                "node_id": node_id,
                "inputs": inputs,
                "timestamp": datetime.utcnow().isoformat()
            }

            # Yield node started event
            yield {
                "type": "node_started",
                "node_id": node_id,
                "node_type": node_type,
                "timestamp": datetime.utcnow().isoformat()
            }

            node_name = node.get("data", {}).get("label") or node.get("data", {}).get("name") or node_type
            node_start_time = time.time()

            # C2: Update workflow state based on node type
            if settings.USE_STATE_MACHINE:
                node_state = self._infer_state_from_node(node)
                self._current_workflow_state = node_state
                variables["sys.workflow_state"] = node_state.value

            try:
                result = await self._execute_node(
                    node_id=node_id,
                    node_type=node_type,
                    node_data=node.get("data", {}),
                    inputs=inputs,
                    variables=variables,
                    workflow_spec=workflow_spec
                )

                node_duration_ms = int((time.time() - node_start_time) * 1000)
                results[node_id] = result
                if result.output:
                    node_outputs[node_id] = result.output

                # Build execution trace step
                trace_step = {
                    "step_id": f"step_{len(execution_trace) + 1}",
                    "node_id": node_id,
                    "node_type": node_type,
                    "node_name": node_name,
                    "status": result.status,
                    "started_at": datetime.utcnow().isoformat(),
                    "completed_at": datetime.utcnow().isoformat(),
                    "duration_ms": result.elapsed_ms or node_duration_ms,
                    "input": inputs if inputs else None,
                    "output": result.output if result.output else None,
                    "error": result.error,
                    "token_usage": self.token_usage_of(result),
                }
                execution_trace.append(trace_step)

                # Accumulate token usage
                total_tokens += result.tokens_used or 0

                # Track failed nodes
                if result.status == "failed":
                    final_status = "failed"
                    error_message = result.error

                branches.record(node_id, result)

                # If this is an iteration node, mark all reachable nodes as consumed
                if node_type == "iteration" and result.status == "success":
                    target_node_id = node.get("data", {}).get("targetNodeId")
                    if target_node_id:
                        reachable = self._get_reachable_nodes(target_node_id, edges)
                        consumed_node_ids.update(reachable)

                # Yield node completion
                yield {
                    "type": "node_complete",
                    "node_id": node_id,
                    "result": result.model_dump(),
                    "timestamp": datetime.utcnow().isoformat()
                }

                # A failed node stops the run, as in execute_workflow
                if result.status == "failed":
                    break

            except Exception as e:
                final_status = "failed"
                error_message = str(e)
                # Add failed trace step
                trace_step = {
                    "step_id": f"step_{len(execution_trace) + 1}",
                    "node_id": node_id,
                    "node_type": node_type,
                    "node_name": node_name,
                    "status": "failed",
                    "started_at": datetime.utcnow().isoformat(),
                    "completed_at": datetime.utcnow().isoformat(),
                    "duration_ms": int((time.time() - node_start_time) * 1000),
                    "error": str(e),
                }
                execution_trace.append(trace_step)
                yield {
                    "type": "node_error",
                    "node_id": node_id,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
                break

        # Update execution log with final results
        if execution_log:
            total_duration_ms = int((time.time() - start_time) * 1000)
            execution_log.status = final_status
            execution_log.completed_at = datetime.utcnow()
            execution_log.duration_ms = total_duration_ms
            execution_log.nodes_executed = self.count_executed(results)
            execution_log.execution_trace = execution_trace
            execution_log.total_tokens = total_tokens
            execution_log.error = error_message
            execution_log.results = {k: v.model_dump() for k, v in results.items()}
            await self.db.commit()

        # Yield final completion
        yield {
            "type": "complete",
            "status": final_status,
            "error": error_message,
            "results": {k: v.model_dump() for k, v in results.items()},
            "executed_nodes": self.count_executed(results),
            "total_nodes": len(nodes),
            "timestamp": datetime.utcnow().isoformat()
        }

    def _topological_sort(
        self,
        nodes: List[Dict],
        edges: List[Dict]
    ) -> List[str]:
        """
        Perform topological sort to determine execution order
        """
        # Build adjacency list
        graph = {node["id"]: [] for node in nodes}
        in_degree = {node["id"]: 0 for node in nodes}

        for edge in edges:
            source = edge["source"]
            target = edge["target"]
            if source in graph and target in in_degree:
                graph[source].append(target)
                in_degree[target] += 1

        # Kahn's algorithm with deque for O(1) popleft
        queue = deque(node_id for node_id, degree in in_degree.items() if degree == 0)
        result = []

        while queue:
            node_id = queue.popleft()
            result.append(node_id)

            for neighbor in graph[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(nodes):
            raise ValueError("Workflow contains a cycle")

        return result

    async def _execute_sub_graph(
        self,
        start_node_id: str,
        workflow_spec: Dict[str, Any],
        variables: Dict[str, Any],
        initial_inputs: Dict[str, Any]
    ) -> Dict[str, NodeExecutionResult]:
        """
        Execute a portion of the workflow starting from start_node_id.
        Follows edges downstream.
        """
        nodes = workflow_spec.get("nodes", [])
        edges = workflow_spec.get("edges", [])
        
        # Determine nodes in sub-graph (all reachable from start_node_id)
        reachable_ids = self._get_reachable_nodes(start_node_id, edges)
        sub_nodes = [n for n in nodes if n["id"] in reachable_ids]
        sub_edges = [e for e in edges if e["source"] in reachable_ids and e["target"] in reachable_ids]
        
        # Sort sub-graph
        try:
            execution_order = self._topological_sort(sub_nodes, sub_edges)
        except ValueError:
            # If there's a cycle, we might be in trouble, but let's assume it's a DAG
            raise
            
        results: Dict[str, NodeExecutionResult] = {}
        node_outputs: Dict[str, Any] = initial_inputs.copy()
        
        for node_id in execution_order:
            node = self._get_node(nodes, node_id)
            node_type = node.get("type", "").lower()
            
            # Get inputs
            inputs = self._get_node_inputs(node_id, sub_edges, node_outputs)
            # Merge with initial inputs if this is the start node
            if node_id == start_node_id:
                inputs.update(initial_inputs)
                
            result = await self._execute_node(
                node_id=node_id,
                node_type=node_type,
                node_data=node.get("data", {}),
                inputs=inputs,
                variables=variables,
                workflow_spec=workflow_spec
            )
            
            results[node_id] = result
            if result.output:
                node_outputs[node_id] = result.output
                
        return results

    def _get_reachable_nodes(self, start_id: str, edges: List[Dict]) -> List[str]:
        """Find all nodes reachable from a starting node."""
        # Build forward edge index for BFS
        forward = defaultdict(list)
        for edge in edges:
            forward[edge["source"]].append(edge["target"])

        reachable = {start_id}
        queue = deque([start_id])

        while queue:
            current_id = queue.popleft()
            for target_id in forward[current_id]:
                if target_id not in reachable:
                    reachable.add(target_id)
                    queue.append(target_id)
        return list(reachable)

    @staticmethod
    def _build_node_map(nodes: List[Dict]) -> Dict[str, Dict]:
        """Build O(1) node lookup map."""
        return {node["id"]: node for node in nodes}

    @staticmethod
    def _build_edges_by_target(edges: List[Dict]) -> Dict[str, List[Dict]]:
        """Build edge index keyed by target node ID."""
        index: Dict[str, List[Dict]] = defaultdict(list)
        for edge in edges:
            index[edge["target"]].append(edge)
        return index

    def _get_node(self, nodes: List[Dict], node_id: str) -> Dict:
        """Get node by ID. Uses pre-built map if available, falls back to scan."""
        if hasattr(self, '_node_map') and self._node_map:
            return self._node_map.get(node_id, {})
        for node in nodes:
            if node["id"] == node_id:
                return node
        return {}

    def _get_node_inputs(
        self,
        node_id: str,
        edges: List[Dict],
        node_outputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get inputs for a node from predecessor outputs. O(degree) with index."""
        inputs = {}

        if hasattr(self, '_edges_by_target') and self._edges_by_target:
            for edge in self._edges_by_target.get(node_id, []):
                source_id = edge["source"]
                if source_id in node_outputs:
                    inputs[source_id] = node_outputs[source_id]
        else:
            for edge in edges:
                if edge["target"] == node_id:
                    source_id = edge["source"]
                    if source_id in node_outputs:
                        inputs[source_id] = node_outputs[source_id]

        return inputs

    async def _extract_entities_background(
        self,
        workflow_id: UUID,
        execution_id: Optional[UUID],
        results: Dict[str, Any],
        node_outputs: Dict[str, Any],
    ) -> None:
        """
        Extract entities from workflow outputs in background.

        Phase 2: Workflow Entity Tracking
        Runs as a fire-and-forget task to not block workflow response.
        Only extracts if workflow has extract_entities=True.
        """
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                # Check if workflow has entity extraction enabled
                stmt = select(Workflow).where(Workflow.id == workflow_id)
                result = await db.execute(stmt)
                workflow = result.scalar_one_or_none()

                if not workflow or not getattr(workflow, 'extract_entities', True):
                    logger.debug(f"Entity extraction disabled for workflow {workflow_id}")
                    return

                # Collect text outputs from all nodes
                text_outputs = []
                for node_id, output in node_outputs.items():
                    if isinstance(output, str):
                        text_outputs.append((node_id, output))
                    elif isinstance(output, dict):
                        # Try common output keys
                        for key in ['text', 'content', 'result', 'output', 'answer', 'response']:
                            if key in output and isinstance(output[key], str):
                                text_outputs.append((node_id, output[key]))
                                break

                if not text_outputs:
                    logger.debug(f"No text outputs found for entity extraction in workflow {workflow_id}")
                    return

                # Import entity extraction service
                from app.services.entity_extraction import get_entity_extraction_service
                entity_service = get_entity_extraction_service()

                # Extract entities from each text output
                total_entities = 0
                for source_node_id, text in text_outputs:
                    if len(text.strip()) < 50:  # Skip very short texts
                        continue

                    try:
                        entity_ids = await entity_service.extract_and_store(
                            db=db,
                            tenant_id=self.tenant_id,
                            workflow_id=workflow_id,
                            execution_id=execution_id,
                            text=text,
                            source_node_id=source_node_id,
                            created_by=UUID(self.user_id) if self.user_id else None,
                        )
                        total_entities += len(entity_ids)
                    except Exception as e:
                        logger.warning(f"Entity extraction failed for node {source_node_id}: {e}")
                        continue

                logger.info(f"Extracted {total_entities} entities from workflow {workflow_id}")

        except Exception as e:
            # Log but don't raise - this is a background task
            logger.error(f"Background entity extraction failed: {type(e).__name__}: {e}")
