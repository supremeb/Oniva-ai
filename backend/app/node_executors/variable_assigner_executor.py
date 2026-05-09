"""
Variable Assigner Node Executor
Sets or modifies workflow variables with type coercion
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import json
from typing import Any


class VariableAssignerExecutor(BaseNodeExecutor):
    """Executor for Variable Assigner node - sets workflow variables"""

    node_type = "variable_assigner"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            assignments = node_data.get("assignments", [])
            output_mode = node_data.get("outputMode", "merge")

            if not assignments:
                elapsed_ms = int((time.time() - start_time) * 1000)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output={"output": {}, "variables_set": []},
                    elapsed_ms=elapsed_ms
                )

            assigned_vars = {}
            variables_set = []

            for assignment in assignments:
                var_name = assignment.get("name", "")
                var_value = assignment.get("value", "")
                var_type = assignment.get("type", "string")

                if not var_name:
                    continue

                # Substitute variables in the value
                resolved_value = self.substitute_variables(
                    str(var_value),
                    context.variables,
                    context.inputs
                )

                # Type coercion
                final_value = self._coerce_type(resolved_value, var_type)

                assigned_vars[var_name] = final_value
                variables_set.append({
                    "name": var_name,
                    "type": var_type,
                    "value": final_value
                })

            elapsed_ms = int((time.time() - start_time) * 1000)

            # Return output based on mode
            if output_mode == "replace":
                output = assigned_vars
            else:
                # Merge with inputs
                output = {**context.inputs}
                for key, value in assigned_vars.items():
                    output[key] = value

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": output,
                    "variables": assigned_vars,
                    "variables_set": variables_set,
                    "mode": output_mode
                },
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

    def _coerce_type(self, value: str, target_type: str) -> Any:
        """Convert value to specified type"""
        if target_type == "string":
            return str(value)

        elif target_type == "number":
            try:
                if "." in str(value):
                    return float(value)
                return int(value)
            except (ValueError, TypeError):
                return 0

        elif target_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return bool(value)

        elif target_type == "json":
            try:
                if isinstance(value, (dict, list)):
                    return value
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {}

        elif target_type == "array":
            try:
                if isinstance(value, list):
                    return value
                if isinstance(value, str):
                    # Try JSON array first
                    try:
                        parsed = json.loads(value)
                        if isinstance(parsed, list):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    # Fall back to comma-separated
                    return [v.strip() for v in value.split(",") if v.strip()]
                return [value]
            except Exception:
                return []

        elif target_type == "reference":
            # Reference type - value is already substituted
            return value

        return value
