"""
Iteration Node Executor
Loops over arrays and executes sub-workflow with parallel/sequential modes

Enhanced with Vellum-style features:
- Configurable parallel execution (up to 96 concurrent, default 10)
- Error handling modes (terminate, continue, skip)
- Output aggregation and flattening
- Progress tracking
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import asyncio
from typing import List, Any, Dict
import logging

logger = logging.getLogger(__name__)


class IterationExecutor(BaseNodeExecutor):
    """Executor for Iteration node - loops over arrays with parallel support"""

    node_type = "iteration"

    # Safety limits to prevent cost attacks and DoS
    MAX_ITERATION_ITEMS = 1000   # Maximum items to iterate over
    MAX_PARALLEL_GLOBAL = 96    # Vellum-style max parallel (up to 96)
    DEFAULT_PARALLEL = 10       # Default parallel executions

    def _get_array_value(self, path: str, context: NodeExecutionContext) -> List[Any]:
        """Get array value from inputs or variables"""
        parts = path.split(".")

        if len(parts) >= 2:
            node_id = parts[0]
            key = ".".join(parts[1:])

            if node_id in context.inputs:
                output = context.inputs[node_id]
                if isinstance(output, dict) and key in output:
                    value = output[key]
                    if isinstance(value, list):
                        return value
                    return [value]
                if isinstance(output, list):
                    return output

        # Check variables
        if path in context.variables:
            value = context.variables[path]
            if isinstance(value, list):
                return value
            return [value]

        # Try to parse as JSON array
        import json
        try:
            parsed = json.loads(path)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        return []

    def _get_value_from_path(self, data: Dict[str, Any], path: str) -> Any:
        """Extract value from nested dict using dot notation"""
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    async def _execute_item_step(
        self,
        index: int,
        item: Any,
        input_array_len: int,
        context: NodeExecutionContext,
        target_node_id: str,
        error_handling: str
    ) -> Dict[str, Any]:
        """
        Execute a single item iteration with error handling

        Error handling modes:
        - terminate: Stop on first error (raises exception)
        - continue: Continue with error in result
        - skip: Skip failed items entirely
        """
        try:
            # Create iteration context for this item
            iter_context = {
                "index": index,
                "item": item,
                "total": input_array_len
            }

            # Prepare variables for the sub-node
            sub_variables = context.variables.copy()
            sub_variables[f"{context.node_id}.item"] = item
            sub_variables[f"{context.node_id}.index"] = index

            # Prepare inputs for the sub-node
            sub_inputs = context.inputs.copy()
            sub_inputs[context.node_id] = iter_context

            # Execute target node or sub-graph
            if hasattr(context.orchestrator, "_execute_sub_graph"):
                sub_results = await context.orchestrator._execute_sub_graph(
                    start_node_id=target_node_id,
                    workflow_spec=context.workflow_spec,
                    variables=sub_variables,
                    initial_inputs=sub_inputs
                )
                target_result = sub_results.get(target_node_id)
                return {
                    "index": index,
                    "item": item,
                    "result": target_result.output if target_result and target_result.status == "success" else None,
                    "status": target_result.status if target_result else "failed",
                    "error": target_result.error if target_result else "Sub-graph execution failed",
                    "sub_results": {k: v.output for k, v in sub_results.items()}
                }
            else:
                sub_result = await context.orchestrator._execute_node(
                    node_id=target_node_id,
                    node_type=context.workflow_spec.get("nodes", [])[0].get("type", ""),
                    node_data={},
                    inputs=sub_inputs,
                    variables=sub_variables,
                    workflow_spec=context.workflow_spec
                )
                return {
                    "index": index,
                    "item": item,
                    "result": sub_result.output if sub_result.status == "success" else None,
                    "status": sub_result.status,
                    "error": sub_result.error
                }

        except Exception as e:
            logger.error(f"Iteration item {index} failed: {e}")
            if error_handling == "terminate":
                raise
            return {
                "index": index,
                "item": item,
                "result": None,
                "status": "failed",
                "error": str(e)
            }

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data

            # Get iteration configuration
            input_array_path = node_data.get("inputArray", "")
            parallel_mode = node_data.get("parallel", False)
            error_handling = node_data.get("errorHandling", "terminate")
            flatten_output = node_data.get("flattenOutput", False)
            batch_size = node_data.get("batchSize")  # Optional: process in batches

            # Parallel execution settings (Vellum-style up to 96)
            max_parallel = min(
                node_data.get("maxParallel", self.DEFAULT_PARALLEL),
                self.MAX_PARALLEL_GLOBAL
            )

            # Get the array to iterate over
            input_array = self._get_array_value(input_array_path, context)

            # Validate array size to prevent cost attacks and DoS
            if len(input_array) > self.MAX_ITERATION_ITEMS:
                elapsed_ms = int((time.time() - start_time) * 1000)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Input array size ({len(input_array)}) exceeds maximum allowed ({self.MAX_ITERATION_ITEMS})",
                    elapsed_ms=elapsed_ms
                )

            if not input_array:
                elapsed_ms = int((time.time() - start_time) * 1000)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output={"items": [], "count": 0, "success_count": 0, "error_count": 0},
                    elapsed_ms=elapsed_ms
                )

            # The actual sub-workflow execution
            target_node_id = node_data.get("targetNodeId")

            aggregated_results: List[Dict[str, Any]] = []

            if target_node_id and context.orchestrator and context.workflow_spec:
                if parallel_mode:
                    # Parallel execution with semaphore
                    semaphore = asyncio.Semaphore(max_parallel)

                    async def sem_execute_item(index: int, item: Any) -> Dict[str, Any]:
                        async with semaphore:
                            return await self._execute_item_step(
                                index, item, len(input_array), context, target_node_id, error_handling
                            )

                    # Handle batch processing if specified
                    if batch_size and batch_size > 0:
                        # Process in batches for very large arrays
                        for batch_start in range(0, len(input_array), batch_size):
                            batch_end = min(batch_start + batch_size, len(input_array))
                            batch = input_array[batch_start:batch_end]
                            batch_tasks = [
                                sem_execute_item(batch_start + i, item)
                                for i, item in enumerate(batch)
                            ]
                            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

                            # Handle exceptions in batch results
                            for result in batch_results:
                                if isinstance(result, Exception):
                                    if error_handling == "terminate":
                                        raise result
                                    aggregated_results.append({
                                        "index": -1,
                                        "item": None,
                                        "result": None,
                                        "status": "failed",
                                        "error": str(result)
                                    })
                                else:
                                    aggregated_results.append(result)
                    else:
                        # Standard parallel execution
                        tasks = [sem_execute_item(i, item) for i, item in enumerate(input_array)]
                        results = await asyncio.gather(*tasks, return_exceptions=True)

                        # Handle exceptions in results
                        for i, result in enumerate(results):
                            if isinstance(result, Exception):
                                if error_handling == "terminate":
                                    raise result
                                aggregated_results.append({
                                    "index": i,
                                    "item": input_array[i],
                                    "result": None,
                                    "status": "failed",
                                    "error": str(result)
                                })
                            else:
                                aggregated_results.append(result)
                else:
                    # Sequential execution
                    for i, item in enumerate(input_array):
                        try:
                            res = await self._execute_item_step(
                                i, item, len(input_array), context, target_node_id, error_handling
                            )
                            aggregated_results.append(res)
                        except Exception as e:
                            if error_handling == "terminate":
                                raise
                            aggregated_results.append({
                                "index": i,
                                "item": item,
                                "result": None,
                                "status": "failed",
                                "error": str(e)
                            })
            else:
                # Original pass-through behavior if no target node
                for i, item in enumerate(input_array):
                    aggregated_results.append({
                        "index": i,
                        "item": item,
                        "total": len(input_array),
                        "status": "success"
                    })

            elapsed_ms = int((time.time() - start_time) * 1000)

            # Filter out skipped items if error handling is "skip"
            if error_handling == "skip":
                aggregated_results = [r for r in aggregated_results if r.get("status") == "success"]

            # Calculate statistics
            success_count = sum(1 for r in aggregated_results if r.get("status") == "success")
            error_count = sum(1 for r in aggregated_results if r.get("status") != "success")

            output = {
                "items": aggregated_results,
                "count": len(aggregated_results),
                "input_array": input_array,
                "aggregated_results": aggregated_results,
                "success_count": success_count,
                "error_count": error_count,
                "parallel": parallel_mode,
                "max_parallel": max_parallel if parallel_mode else 1,
            }

            # Aggregate specific variable if requested
            output_variable_path = node_data.get("outputVariable")
            if output_variable_path:
                results_only = []
                for res in aggregated_results:
                    val = self._get_value_from_path(res.get("sub_results", {}), output_variable_path)
                    results_only.append(val)
                output["results"] = results_only

            if flatten_output and aggregated_results:
                # Flatten nested arrays in results
                flattened = []
                for r in aggregated_results:
                    result_val = r.get("result")
                    if isinstance(result_val, list):
                        flattened.extend(result_val)
                    elif result_val is not None:
                        flattened.append(result_val)
                output["flattened"] = flattened

            # Extract just the results for convenience
            output["output"] = [r.get("result") for r in aggregated_results if r.get("status") == "success"]

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output,
                elapsed_ms=elapsed_ms
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Iteration failed: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )
