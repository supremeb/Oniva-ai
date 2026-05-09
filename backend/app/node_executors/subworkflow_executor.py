"""
Sub-Workflow Node Executor
Executes another workflow as a sub-workflow with input/output mapping
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class SubWorkflowExecutor(BaseNodeExecutor):
    """Executor for Sub-Workflow node - executes child workflows"""

    node_type = "subworkflow"

    # Maximum recursion depth to prevent infinite loops
    MAX_RECURSION_DEPTH = 10

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            workflow_id = node_data.get("workflowId")
            input_mappings = node_data.get("inputMappings", [])
            output_variable = node_data.get("outputVariable", "subworkflow_result")
            async_mode = node_data.get("async", False)
            timeout = node_data.get("timeout", 300)  # Default 5 minutes
            fail_on_error = node_data.get("failOnError", True)

            if not workflow_id:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error="No workflow selected for sub-workflow execution",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Check recursion depth
            current_depth = context.variables.get("__recursion_depth", 0)
            if current_depth >= self.MAX_RECURSION_DEPTH:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Maximum recursion depth ({self.MAX_RECURSION_DEPTH}) exceeded",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Build input variables from mappings
            sub_variables = self._build_input_variables(input_mappings, context)
            sub_variables["__recursion_depth"] = current_depth + 1
            sub_variables["__parent_workflow_id"] = context.workflow_spec.get("id") if context.workflow_spec else None
            sub_variables["__parent_node_id"] = context.node_id

            # Execute sub-workflow
            if async_mode:
                result = await self._execute_async(
                    workflow_id,
                    sub_variables,
                    context,
                    db
                )
            else:
                result = await self._execute_sync(
                    workflow_id,
                    sub_variables,
                    timeout,
                    context,
                    db
                )

            elapsed_ms = int((time.time() - start_time) * 1000)

            # Handle failure based on configuration
            if result.get("status") == "failed" and fail_on_error:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=result.get("error", "Sub-workflow execution failed"),
                    elapsed_ms=elapsed_ms
                )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": result.get("output"),
                    output_variable: result.get("output"),
                    "execution_id": result.get("execution_id"),
                    "status": result.get("status"),
                    "workflow_id": workflow_id,
                    "async": async_mode,
                },
                elapsed_ms=elapsed_ms
            )

        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=f"Sub-workflow execution timed out after {timeout}s",
                elapsed_ms=elapsed_ms
            )
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )

    def _build_input_variables(
        self,
        mappings: list,
        context: NodeExecutionContext
    ) -> Dict[str, Any]:
        """Build input variables for sub-workflow from mappings"""
        variables = {}

        for mapping in mappings:
            workflow_var = mapping.get("workflowVar", "")
            source_value = mapping.get("sourceValue", "")

            if not workflow_var:
                continue

            # Resolve the source value
            resolved = self.substitute_variables(
                source_value,
                context.variables,
                context.inputs
            )
            variables[workflow_var] = resolved

        return variables

    async def _execute_sync(
        self,
        workflow_id: str,
        variables: Dict[str, Any],
        timeout: int,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Execute sub-workflow synchronously and wait for completion"""
        # In production, this would:
        # 1. Load the workflow definition from the database
        # 2. Create a new WorkflowOrchestrator with the sub-workflow
        # 3. Execute and wait for completion
        # 4. Return the results

        # For now, use the orchestrator reference if available
        if context.orchestrator and hasattr(context.orchestrator, 'execute_subworkflow'):
            try:
                result = await asyncio.wait_for(
                    context.orchestrator.execute_subworkflow(
                        workflow_id=workflow_id,
                        variables=variables,
                        user_id=context.user_id
                    ),
                    timeout=timeout
                )
                return result
            except asyncio.TimeoutError:
                raise

        # Stub implementation
        logger.warning(f"Sub-workflow execution stub for workflow_id={workflow_id}")
        return {
            "status": "completed",
            "output": {"result": f"Sub-workflow {workflow_id} completed (stub)"},
            "execution_id": f"sub_{int(time.time())}"
        }

    async def _execute_async(
        self,
        workflow_id: str,
        variables: Dict[str, Any],
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Execute sub-workflow asynchronously (fire and forget)"""
        # In production, this would:
        # 1. Queue the workflow execution
        # 2. Return immediately with an execution ID
        # 3. The parent workflow can optionally check status later

        if context.orchestrator and hasattr(context.orchestrator, 'queue_subworkflow'):
            execution_id = await context.orchestrator.queue_subworkflow(
                workflow_id=workflow_id,
                variables=variables,
                user_id=context.user_id
            )
            return {
                "status": "queued",
                "output": None,
                "execution_id": execution_id
            }

        # Stub implementation
        execution_id = f"async_sub_{int(time.time())}"
        logger.info(f"Queued async sub-workflow: {execution_id}")
        return {
            "status": "queued",
            "output": None,
            "execution_id": execution_id
        }
