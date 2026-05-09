"""
Complex Step Executor - Handles multi-action and conditional steps.

Multi-action steps execute a sequence of actions in order, passing
data between them. Conditional steps evaluate conditions and execute
the appropriate branch.

This supports the AI Workflow Builder's complex step types.
"""

from typing import Dict, Any, List
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.node_executors import (
    BaseNodeExecutor,
    NodeExecutionContext,
    NodeExecutionResult,
    get_executor,
    register_executor,
)
from app.schemas.classification import DataClassification

logger = logging.getLogger(__name__)


class MultiActionExecutor(BaseNodeExecutor):
    """
    Executes a sequence of actions within a single step.

    Multi-action steps contain an ordered list of actions that execute
    sequentially. Each action's output is available to subsequent actions.

    Example config:
    {
        "label": "Process and Notify",
        "actions": [
            {"id": "a1", "type": "llm", "config": {...}},
            {"id": "a2", "type": "template", "config": {...}},
            {"id": "a3", "type": "notification", "config": {...}}
        ]
    }
    """

    node_type: str = "multi_action"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession,
    ) -> NodeExecutionResult:
        """
        Execute all actions in sequence.

        Args:
            context: Execution context with node data containing actions
            db: Database session

        Returns:
            Combined result from all actions
        """
        node_data = context.node_data
        actions = node_data.get("actions", [])

        if not actions:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error="No actions defined in multi-action step",
            )

        # Track results from each action
        action_results: List[Dict[str, Any]] = []
        combined_output: Dict[str, Any] = {}
        total_tokens = 0
        max_classification = DataClassification.INTERNAL
        start_time = datetime.utcnow()

        # Variables available to actions (starts with inputs, accumulates)
        action_variables = {**context.variables, **context.inputs}

        for i, action in enumerate(actions):
            action_id = action.get("id", f"action_{i}")
            action_type = action.get("type")
            action_config = action.get("config", {})

            if not action_type:
                action_results.append({
                    "action_id": action_id,
                    "status": "failed",
                    "error": "Action type not specified",
                })
                continue

            # Get executor for this action type
            executor = get_executor(action_type)
            if not executor:
                action_results.append({
                    "action_id": action_id,
                    "status": "failed",
                    "error": f"Unknown action type: {action_type}",
                })
                continue

            # Create context for this action
            action_context = NodeExecutionContext(
                node_id=f"{context.node_id}_{action_id}",
                node_type=action_type,
                node_data=action_config,
                inputs=action_variables,
                variables=context.variables,
                user_id=context.user_id,
                orchestrator=context.orchestrator,
                workflow_spec=context.workflow_spec,
                tenant_id=context.tenant_id,
                session_id=context.session_id,
                workflow_id=context.workflow_id,
                workflow_run_id=context.workflow_run_id,
                db=context.db,
            )

            try:
                # Execute the action
                result = await executor.execute(action_context, db)

                # Track result
                action_results.append({
                    "action_id": action_id,
                    "type": action_type,
                    "status": result.status,
                    "output": result.output,
                    "error": result.error,
                    "elapsed_ms": result.elapsed_ms,
                })

                # Accumulate metrics
                if result.tokens_used:
                    total_tokens += result.tokens_used

                # Update max classification
                if result.classification.value > max_classification.value:
                    max_classification = result.classification

                # Make output available to subsequent actions
                if result.status == "success" and result.output:
                    action_variables[action_id] = result.output
                    combined_output[action_id] = result.output

                # Stop on failure (unless configured to continue)
                if result.status == "failed" and not action_config.get("continue_on_error", False):
                    elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        output={"action_results": action_results, "partial_output": combined_output},
                        error=f"Action {action_id} failed: {result.error}",
                        elapsed_ms=elapsed_ms,
                        tokens_used=total_tokens if total_tokens > 0 else None,
                        classification=max_classification,
                    )

            except Exception as e:
                logger.error(f"Error executing action {action_id}: {e}")
                action_results.append({
                    "action_id": action_id,
                    "type": action_type,
                    "status": "failed",
                    "error": str(e),
                })

                if not action_config.get("continue_on_error", False):
                    elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        output={"action_results": action_results, "partial_output": combined_output},
                        error=f"Action {action_id} failed: {str(e)}",
                        elapsed_ms=elapsed_ms,
                        tokens_used=total_tokens if total_tokens > 0 else None,
                        classification=max_classification,
                    )

        elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        return NodeExecutionResult(
            node_id=context.node_id,
            status="success",
            output={
                "action_results": action_results,
                "combined_output": combined_output,
                # Return last action's output as primary output
                "output": combined_output.get(actions[-1].get("id", f"action_{len(actions)-1}")),
            },
            elapsed_ms=elapsed_ms,
            tokens_used=total_tokens if total_tokens > 0 else None,
            classification=max_classification,
        )


class ConditionalExecutor(BaseNodeExecutor):
    """
    Executes one of multiple branches based on conditions.

    Conditional steps evaluate conditions in order and execute the first
    matching branch. If no conditions match, the default branch is executed.

    Example config:
    {
        "label": "Route by Type",
        "default_branch_id": "else",
        "branches": [
            {
                "id": "high_value",
                "condition": {"field": "amount", "operator": "gt", "value": 10000},
                "steps": [{"id": "s1", "type": "llm", "config": {...}}]
            },
            {
                "id": "else",
                "condition": null,  # Default branch
                "steps": [{"id": "s2", "type": "template", "config": {...}}]
            }
        ]
    }
    """

    node_type: str = "conditional"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession,
    ) -> NodeExecutionResult:
        """
        Evaluate conditions and execute matching branch.

        Args:
            context: Execution context with node data containing branches
            db: Database session

        Returns:
            Result from the executed branch
        """
        node_data = context.node_data
        branches = node_data.get("branches", [])
        default_branch_id = node_data.get("default_branch_id")

        if not branches:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error="No branches defined in conditional step",
            )

        start_time = datetime.utcnow()

        # Evaluate conditions to find matching branch
        matched_branch = None
        default_branch = None

        for branch in branches:
            branch_id = branch.get("id")
            condition = branch.get("condition")

            # Track default branch
            if not condition or branch_id == default_branch_id:
                default_branch = branch
                continue

            # Evaluate condition
            if self._evaluate_condition(condition, context.inputs, context.variables):
                matched_branch = branch
                break

        # Use matched branch or fall back to default
        branch_to_execute = matched_branch or default_branch

        if not branch_to_execute:
            elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error="No matching branch and no default branch defined",
                elapsed_ms=elapsed_ms,
            )

        branch_id = branch_to_execute.get("id", "unknown")
        branch_steps = branch_to_execute.get("steps", [])

        if not branch_steps:
            elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "branch_id": branch_id,
                    "branch_output": None,
                    "message": "Branch has no steps",
                },
                elapsed_ms=elapsed_ms,
                next_node_id=branch_id,  # Signal which branch was taken
            )

        # Execute branch steps (similar to multi-action)
        step_results: List[Dict[str, Any]] = []
        step_variables = {**context.variables, **context.inputs}
        total_tokens = 0
        max_classification = DataClassification.INTERNAL

        for i, step in enumerate(branch_steps):
            step_id = step.get("id", f"step_{i}")
            step_type = step.get("type")
            step_config = step.get("config", {})

            if not step_type:
                continue

            executor = get_executor(step_type)
            if not executor:
                step_results.append({
                    "step_id": step_id,
                    "status": "failed",
                    "error": f"Unknown step type: {step_type}",
                })
                continue

            step_context = NodeExecutionContext(
                node_id=f"{context.node_id}_{branch_id}_{step_id}",
                node_type=step_type,
                node_data=step_config,
                inputs=step_variables,
                variables=context.variables,
                user_id=context.user_id,
                orchestrator=context.orchestrator,
                workflow_spec=context.workflow_spec,
                tenant_id=context.tenant_id,
                session_id=context.session_id,
                workflow_id=context.workflow_id,
                workflow_run_id=context.workflow_run_id,
                db=context.db,
            )

            try:
                result = await executor.execute(step_context, db)

                step_results.append({
                    "step_id": step_id,
                    "type": step_type,
                    "status": result.status,
                    "output": result.output,
                    "error": result.error,
                })

                if result.tokens_used:
                    total_tokens += result.tokens_used

                if result.classification.value > max_classification.value:
                    max_classification = result.classification

                if result.status == "success" and result.output:
                    step_variables[step_id] = result.output

                if result.status == "failed":
                    elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        output={"branch_id": branch_id, "step_results": step_results},
                        error=f"Branch step {step_id} failed: {result.error}",
                        elapsed_ms=elapsed_ms,
                        tokens_used=total_tokens if total_tokens > 0 else None,
                        next_node_id=branch_id,
                        classification=max_classification,
                    )

            except Exception as e:
                logger.error(f"Error executing branch step {step_id}: {e}")
                elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    output={"branch_id": branch_id, "step_results": step_results},
                    error=f"Branch step {step_id} failed: {str(e)}",
                    elapsed_ms=elapsed_ms,
                    next_node_id=branch_id,
                    classification=max_classification,
                )

        elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        # Get final output from last step
        final_output = None
        if step_results and step_results[-1].get("status") == "success":
            final_output = step_results[-1].get("output")

        return NodeExecutionResult(
            node_id=context.node_id,
            status="success",
            output={
                "branch_id": branch_id,
                "branch_output": final_output,
                "step_results": step_results,
            },
            elapsed_ms=elapsed_ms,
            tokens_used=total_tokens if total_tokens > 0 else None,
            next_node_id=branch_id,
            classification=max_classification,
        )

    def _evaluate_condition(
        self,
        condition: Dict[str, Any],
        inputs: Dict[str, Any],
        variables: Dict[str, Any],
    ) -> bool:
        """
        Evaluate a condition against inputs and variables.

        Supported operators:
        - eq: Equal
        - ne: Not equal
        - gt: Greater than
        - gte: Greater than or equal
        - lt: Less than
        - lte: Less than or equal
        - contains: String contains
        - startswith: String starts with
        - endswith: String ends with
        - in: Value in list
        - exists: Field exists and is not None
        - empty: Field is empty (None, "", [], {})

        Args:
            condition: {field, operator, value}
            inputs: Node inputs
            variables: Workflow variables

        Returns:
            True if condition matches
        """
        if not condition:
            return False

        field = condition.get("field", "")
        operator = condition.get("operator", "eq")
        expected_value = condition.get("value")

        # Resolve field value from inputs or variables
        actual_value = self._resolve_field_value(field, inputs, variables)

        try:
            if operator == "eq":
                return actual_value == expected_value
            elif operator == "ne":
                return actual_value != expected_value
            elif operator == "gt":
                return float(actual_value) > float(expected_value)
            elif operator == "gte":
                return float(actual_value) >= float(expected_value)
            elif operator == "lt":
                return float(actual_value) < float(expected_value)
            elif operator == "lte":
                return float(actual_value) <= float(expected_value)
            elif operator == "contains":
                return str(expected_value) in str(actual_value)
            elif operator == "startswith":
                return str(actual_value).startswith(str(expected_value))
            elif operator == "endswith":
                return str(actual_value).endswith(str(expected_value))
            elif operator == "in":
                return actual_value in expected_value
            elif operator == "exists":
                return actual_value is not None
            elif operator == "empty":
                if actual_value is None:
                    return True
                if isinstance(actual_value, str) and actual_value == "":
                    return True
                if isinstance(actual_value, (list, dict)) and len(actual_value) == 0:
                    return True
                return False
            else:
                logger.warning(f"Unknown condition operator: {operator}")
                return False

        except (ValueError, TypeError) as e:
            logger.warning(f"Condition evaluation error: {e}")
            return False

    def _resolve_field_value(
        self,
        field: str,
        inputs: Dict[str, Any],
        variables: Dict[str, Any],
    ) -> Any:
        """
        Resolve a field reference to its value.

        Supports dot notation: "node_id.field" or just "field"

        Args:
            field: Field reference
            inputs: Node inputs
            variables: Workflow variables

        Returns:
            Resolved value or None
        """
        if not field:
            return None

        # Check if it's a nested reference (node_id.field)
        if "." in field:
            parts = field.split(".", 1)
            node_id, sub_field = parts[0], parts[1]

            # Check inputs first
            if node_id in inputs:
                node_output = inputs[node_id]
                if isinstance(node_output, dict) and sub_field in node_output:
                    return node_output[sub_field]

            # Check variables
            if node_id in variables:
                var_value = variables[node_id]
                if isinstance(var_value, dict) and sub_field in var_value:
                    return var_value[sub_field]

            return None

        # Simple field - check inputs then variables
        if field in inputs:
            return inputs[field]
        if field in variables:
            return variables[field]

        return None


# Register executors
register_executor(MultiActionExecutor())
register_executor(ConditionalExecutor())
