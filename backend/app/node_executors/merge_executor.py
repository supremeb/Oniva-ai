"""
Merge Node Executor
Vellum-inspired parallel branch synchronization

The Merge node synchronizes multiple parallel execution paths:
- Waits for all or any incoming branches to complete
- Combines outputs from parallel branches
- Supports different merge strategies (all, any, n-of-m)

Usage:
{
    "type": "merge",
    "data": {
        "mergeStrategy": "all",  // all, any, n_of_m
        "requiredCount": 2,  // For n_of_m strategy
        "timeout": 300,  // Timeout in seconds (default: 5 minutes)
        "combineMethod": "object",  // object, array, first, last
        "failOnError": true,  // Whether to fail if any branch failed
        "defaultValue": null  // Value for branches that haven't completed (for 'any' strategy)
    }
}

Merge strategies:
- all: Wait for all incoming branches to complete (default)
- any: Proceed when any single branch completes
- n_of_m: Proceed when N of M branches complete
"""

import logging
import time
from typing import Any, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult

logger = logging.getLogger(__name__)


class BranchResult(BaseModel):
    """Result from a single incoming branch"""
    source_node_id: str
    status: str  # completed, pending, failed, timeout
    output: Optional[Any] = None
    error: Optional[str] = None


class MergeOutput(BaseModel):
    """Output schema for Merge node"""
    merged_data: Any  # Combined data from branches
    branch_count: int
    completed_count: int
    failed_count: int
    pending_count: int
    branches: List[BranchResult] = Field(default_factory=list)
    merge_strategy: str
    combine_method: str
    elapsed_ms: int = 0


class MergeExecutor(BaseNodeExecutor):
    """
    Executor for Merge node - synchronizes parallel branches

    This node collects outputs from multiple upstream branches and
    combines them according to the specified strategy. Essential for
    workflows with parallel execution paths.
    """

    node_type = "merge"

    def _combine_outputs(
        self,
        branches: List[BranchResult],
        combine_method: str,
        default_value: Any = None
    ) -> Any:
        """Combine branch outputs according to method"""

        # Get completed outputs
        completed = [b for b in branches if b.status == "completed"]

        if not completed:
            return default_value

        if combine_method == "array":
            # Return as array of outputs
            return [b.output for b in completed]

        elif combine_method == "object":
            # Return as object keyed by source node ID
            return {b.source_node_id: b.output for b in completed}

        elif combine_method == "first":
            # Return first completed output
            return completed[0].output if completed else default_value

        elif combine_method == "last":
            # Return last completed output
            return completed[-1].output if completed else default_value

        elif combine_method == "flatten":
            # Flatten arrays from all branches
            result = []
            for b in completed:
                if isinstance(b.output, list):
                    result.extend(b.output)
                else:
                    result.append(b.output)
            return result

        elif combine_method == "merge_objects":
            # Deep merge all object outputs
            result = {}
            for b in completed:
                if isinstance(b.output, dict):
                    result.update(b.output)
            return result

        else:
            # Default: object format
            return {b.source_node_id: b.output for b in completed}

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        """Execute merge synchronization"""
        start_time = time.time()

        try:
            node_data = context.node_data

            # Get configuration
            merge_strategy = node_data.get("mergeStrategy", "all")
            required_count = node_data.get("requiredCount", 1)
            combine_method = node_data.get("combineMethod", "object")
            fail_on_error = node_data.get("failOnError", True)
            default_value = node_data.get("defaultValue")

            # Collect branch results from inputs
            branches: List[BranchResult] = []

            for source_node_id, node_output in context.inputs.items():
                if isinstance(node_output, dict):
                    # Check if this is a node execution result
                    status = node_output.get("status", "completed")
                    error = node_output.get("error")

                    if status == "failed":
                        branches.append(BranchResult(
                            source_node_id=source_node_id,
                            status="failed",
                            output=node_output,
                            error=error
                        ))
                    else:
                        branches.append(BranchResult(
                            source_node_id=source_node_id,
                            status="completed",
                            output=node_output
                        ))
                else:
                    branches.append(BranchResult(
                        source_node_id=source_node_id,
                        status="completed",
                        output=node_output
                    ))

            # Count statuses
            completed_count = sum(1 for b in branches if b.status == "completed")
            failed_count = sum(1 for b in branches if b.status == "failed")
            pending_count = sum(1 for b in branches if b.status == "pending")

            # Check merge conditions based on strategy
            should_proceed = False
            status_message = ""

            if merge_strategy == "all":
                # All branches must complete
                should_proceed = completed_count == len(branches) or (
                    completed_count + failed_count == len(branches)
                )
                if not should_proceed:
                    status_message = f"Waiting for all branches: {completed_count}/{len(branches)} completed"
                elif failed_count > 0 and fail_on_error:
                    status_message = f"{failed_count} branches failed"

            elif merge_strategy == "any":
                # Any single branch completing is enough
                should_proceed = completed_count >= 1
                if not should_proceed:
                    status_message = "Waiting for any branch to complete"

            elif merge_strategy == "n_of_m":
                # N of M branches must complete
                should_proceed = completed_count >= required_count
                if not should_proceed:
                    status_message = f"Waiting for {required_count} branches: {completed_count} completed"

            else:
                # Default to all
                should_proceed = completed_count == len(branches)

            # Check for failures
            if failed_count > 0 and fail_on_error:
                failed_branches = [b for b in branches if b.status == "failed"]
                error_msgs = [f"{b.source_node_id}: {b.error}" for b in failed_branches]

                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Branch failures: {'; '.join(error_msgs)}",
                    output=MergeOutput(
                        merged_data=None,
                        branch_count=len(branches),
                        completed_count=completed_count,
                        failed_count=failed_count,
                        pending_count=pending_count,
                        branches=branches,
                        merge_strategy=merge_strategy,
                        combine_method=combine_method,
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    ).model_dump(),
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            if not should_proceed:
                # This shouldn't happen in normal execution since
                # the orchestrator should have waited, but handle it
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="pending",
                    output=MergeOutput(
                        merged_data=None,
                        branch_count=len(branches),
                        completed_count=completed_count,
                        failed_count=failed_count,
                        pending_count=pending_count,
                        branches=branches,
                        merge_strategy=merge_strategy,
                        combine_method=combine_method,
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    ).model_dump(),
                    error=status_message,
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Combine outputs
            merged_data = self._combine_outputs(branches, combine_method, default_value)

            output = MergeOutput(
                merged_data=merged_data,
                branch_count=len(branches),
                completed_count=completed_count,
                failed_count=failed_count,
                pending_count=pending_count,
                branches=branches,
                merge_strategy=merge_strategy,
                combine_method=combine_method,
                elapsed_ms=int((time.time() - start_time) * 1000)
            )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output.model_dump(),
                elapsed_ms=int((time.time() - start_time) * 1000)
            )

        except Exception as e:
            logger.exception(f"Merge execution failed: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=f"Merge execution failed: {str(e)}",
                elapsed_ms=int((time.time() - start_time) * 1000)
            )
