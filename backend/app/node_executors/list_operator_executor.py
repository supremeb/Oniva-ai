"""
List Operator Node Executor
Performs common array/list operations: filter, map, sort, slice, etc.
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import json
from typing import Any, List


class ListOperatorExecutor(BaseNodeExecutor):
    """Executor for List Operator node - array manipulation operations"""

    node_type = "list_operator"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            operation = node_data.get("operation", "first")
            input_ref = node_data.get("inputArray", "")

            # Resolve input array
            input_array = self._resolve_input_array(input_ref, context.variables, context.inputs)

            if input_array is None:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Input array not found or not an array: {input_ref}",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Execute operation
            result = await self._execute_operation(operation, input_array, node_data, context)

            elapsed_ms = int((time.time() - start_time) * 1000)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": result,
                    "result": result,
                    "operation": operation,
                    "input_length": len(input_array) if isinstance(input_array, list) else 0,
                    "output_length": len(result) if isinstance(result, list) else 1
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

    def _resolve_input_array(self, input_ref: str, variables: dict, inputs: dict) -> Any:
        """Resolve the input array from variables or inputs"""
        # Strip template brackets
        ref = input_ref.replace("{{", "").replace("}}", "").strip()

        if not ref:
            return None

        # Try inputs first
        if ref in inputs:
            val = inputs[ref]
            if isinstance(val, dict) and 'output' in val:
                return val.get('output') if isinstance(val.get('output'), list) else None
            return val if isinstance(val, list) else None

        # Try nested path (node_id.field)
        if "." in ref:
            parts = ref.split(".", 1)
            node_id, field = parts[0], parts[1]
            if node_id in inputs:
                node_output = inputs[node_id]
                if isinstance(node_output, dict):
                    val = node_output.get(field)
                    return val if isinstance(val, list) else None

        # Try variables
        if ref in variables:
            val = variables[ref]
            return val if isinstance(val, list) else None

        return None

    async def _execute_operation(
        self,
        operation: str,
        array: List,
        node_data: dict,
        context: NodeExecutionContext
    ) -> Any:
        """Execute the specified list operation"""

        if operation == "first":
            return array[0] if array else None

        elif operation == "last":
            return array[-1] if array else None

        elif operation == "at_index":
            index = node_data.get("index", 0)
            if 0 <= index < len(array):
                return array[index]
            return None

        elif operation == "slice":
            start = node_data.get("startIndex", 0)
            end = node_data.get("endIndex")
            if end is not None:
                return array[start:end]
            return array[start:]

        elif operation == "filter":
            condition = node_data.get("filterCondition", "")
            if not condition:
                return array
            return self._filter_array(array, condition)

        elif operation == "sort":
            sort_key = node_data.get("sortKey")
            direction = node_data.get("sortDirection", "asc")
            reverse = direction == "desc"

            if sort_key and all(isinstance(item, dict) for item in array):
                try:
                    return sorted(array, key=lambda x: x.get(sort_key, ""), reverse=reverse)
                except TypeError:
                    return sorted(array, key=lambda x: str(x.get(sort_key, "")), reverse=reverse)
            return sorted(array, reverse=reverse)

        elif operation == "reverse":
            return list(reversed(array))

        elif operation == "unique":
            # Handle both primitives and objects
            seen = []
            result = []
            for item in array:
                key = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else item
                if key not in seen:
                    seen.append(key)
                    result.append(item)
            return result

        elif operation == "map":
            expression = node_data.get("mapExpression", "")
            if not expression:
                return array
            return self._map_array(array, expression)

        elif operation == "reduce":
            expression = node_data.get("reduceExpression", "")
            initial = node_data.get("reduceInitial", None)
            if not expression:
                return array
            return self._reduce_array(array, expression, initial)

        elif operation == "flatten":
            depth = node_data.get("flattenDepth", 1)
            return self._flatten_array(array, depth)

        elif operation == "concat":
            other_ref = node_data.get("otherArray", "")
            other_array = self._resolve_input_array(other_ref, context.variables, context.inputs)
            if other_array:
                return array + other_array
            return array

        elif operation == "length":
            return len(array)

        elif operation == "includes":
            search_value = node_data.get("searchValue", "")
            resolved = self.substitute_variables(search_value, context.variables, context.inputs)
            return resolved in array or any(
                isinstance(item, dict) and resolved in item.values() for item in array
            )

        elif operation == "find":
            condition = node_data.get("filterCondition", "")
            if not condition:
                return None
            filtered = self._filter_array(array, condition)
            return filtered[0] if filtered else None

        elif operation == "group_by":
            group_key = node_data.get("groupByKey", "")
            if not group_key:
                return {"default": array}
            return self._group_by(array, group_key)

        return array

    def _filter_array(self, array: List, condition: str) -> List:
        """Filter array based on a simple condition string"""
        # Support simple conditions like "status == 'active'" or "amount > 100"
        result = []
        for item in array:
            try:
                # Create safe evaluation context
                if isinstance(item, dict):
                    # Build context with item fields
                    ctx = {"item": item}
                    ctx.update(item)
                    if self._eval_condition(condition, ctx):
                        result.append(item)
                else:
                    ctx = {"item": item, "value": item}
                    if self._eval_condition(condition, ctx):
                        result.append(item)
            except Exception:
                continue
        return result

    def _eval_condition(self, condition: str, ctx: dict) -> bool:
        """Safely evaluate a condition"""
        # Simple condition parser for common patterns
        condition = condition.strip()

        # Handle == comparison
        if "==" in condition:
            parts = condition.split("==", 1)
            left = self._get_value(parts[0].strip(), ctx)
            right = self._parse_literal(parts[1].strip())
            return left == right

        # Handle != comparison
        if "!=" in condition:
            parts = condition.split("!=", 1)
            left = self._get_value(parts[0].strip(), ctx)
            right = self._parse_literal(parts[1].strip())
            return left != right

        # Handle > comparison
        if ">" in condition and ">=" not in condition:
            parts = condition.split(">", 1)
            left = self._get_value(parts[0].strip(), ctx)
            right = self._parse_literal(parts[1].strip())
            try:
                return float(left) > float(right)
            except (ValueError, TypeError):
                return False

        # Handle >= comparison
        if ">=" in condition:
            parts = condition.split(">=", 1)
            left = self._get_value(parts[0].strip(), ctx)
            right = self._parse_literal(parts[1].strip())
            try:
                return float(left) >= float(right)
            except (ValueError, TypeError):
                return False

        # Handle < comparison
        if "<" in condition and "<=" not in condition:
            parts = condition.split("<", 1)
            left = self._get_value(parts[0].strip(), ctx)
            right = self._parse_literal(parts[1].strip())
            try:
                return float(left) < float(right)
            except (ValueError, TypeError):
                return False

        # Handle <= comparison
        if "<=" in condition:
            parts = condition.split("<=", 1)
            left = self._get_value(parts[0].strip(), ctx)
            right = self._parse_literal(parts[1].strip())
            try:
                return float(left) <= float(right)
            except (ValueError, TypeError):
                return False

        return False

    def _get_value(self, key: str, ctx: dict) -> Any:
        """Get value from context"""
        if key in ctx:
            return ctx[key]
        if "." in key:
            parts = key.split(".", 1)
            if parts[0] in ctx and isinstance(ctx[parts[0]], dict):
                return ctx[parts[0]].get(parts[1])
        return None

    def _parse_literal(self, value: str) -> Any:
        """Parse a literal value from condition string"""
        value = value.strip()
        # Handle quoted strings
        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            return value[1:-1]
        # Handle numbers
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        # Handle booleans
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
        return value

    def _map_array(self, array: List, expression: str) -> List:
        """Map/transform array items"""
        # Simple field extraction like "item.name" or transformations
        result = []
        for item in array:
            if isinstance(item, dict) and expression in item:
                result.append(item[expression])
            elif isinstance(item, dict) and expression.startswith("item."):
                field = expression.replace("item.", "")
                result.append(item.get(field))
            else:
                result.append(item)
        return result

    def _reduce_array(self, array: List, expression: str, initial: Any) -> Any:
        """Reduce array to single value"""
        # Support simple reductions like sum, concat
        if expression == "sum":
            return sum(float(x) if not isinstance(x, (int, float)) else x for x in array)
        if expression == "concat":
            return "".join(str(x) for x in array)
        if expression == "min":
            return min(array)
        if expression == "max":
            return max(array)
        if expression == "avg":
            return sum(array) / len(array) if array else 0
        return array

    def _flatten_array(self, array: List, depth: int = 1) -> List:
        """Flatten nested arrays"""
        result = []
        for item in array:
            if isinstance(item, list) and depth > 0:
                result.extend(self._flatten_array(item, depth - 1))
            else:
                result.append(item)
        return result

    def _group_by(self, array: List, key: str) -> dict:
        """Group array items by a key"""
        result = {}
        for item in array:
            if isinstance(item, dict):
                group_val = str(item.get(key, "undefined"))
            else:
                group_val = "undefined"
            if group_val not in result:
                result[group_val] = []
            result[group_val].append(item)
        return result
