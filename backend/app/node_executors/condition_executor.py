"""
Condition (IF/ELSE) Node Executor
Evaluates conditions and returns branch to follow
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
from typing import Any, List, Dict


class ConditionExecutor(BaseNodeExecutor):
    """Executor for IF/ELSE node - evaluates conditions and determines branch"""
    
    node_type = "ifelse"
    
    def _get_value(self, path: str, context: NodeExecutionContext) -> Any:
        """Get value from node outputs or variables by path (e.g., 'node_1.text')"""
        parts = path.split(".")
        
        if len(parts) >= 2:
            node_id = parts[0]
            key = ".".join(parts[1:])
            
            # Check inputs (predecessor outputs)
            if node_id in context.inputs:
                output = context.inputs[node_id]
                if isinstance(output, dict) and key in output:
                    return output[key]
                return output
        
        # Check variables
        if path in context.variables:
            return context.variables[path]
        
        # Return as literal if not found
        return path
    
    def _evaluate_condition(
        self,
        variable: str,
        operator: str,
        value: str,
        context: NodeExecutionContext
    ) -> bool:
        """Evaluate a single condition"""
        # Get actual value
        actual = self._get_value(variable, context)
        expected = value
        
        # Convert to string for comparison if needed
        actual_str = str(actual) if actual is not None else ""
        expected_str = str(expected) if expected is not None else ""
        
        # Handle numeric comparisons
        actual_num = None
        expected_num = None
        try:
            actual_num = float(actual_str)
            expected_num = float(expected_str)
        except (ValueError, TypeError):
            pass
        
        if operator == "equals":
            return actual_str == expected_str
        elif operator == "not_equals":
            return actual_str != expected_str
        elif operator == "contains":
            return expected_str.lower() in actual_str.lower()
        elif operator == "not_contains":
            return expected_str.lower() not in actual_str.lower()
        elif operator == "starts_with":
            return actual_str.lower().startswith(expected_str.lower())
        elif operator == "ends_with":
            return actual_str.lower().endswith(expected_str.lower())
        elif operator == "is_empty":
            return actual is None or actual_str == "" or actual_str == "[]" or actual_str == "{}"
        elif operator == "is_not_empty":
            return actual is not None and actual_str != "" and actual_str != "[]" and actual_str != "{}"
        elif operator == "greater_than":
            if actual_num is not None and expected_num is not None:
                return actual_num > expected_num
            return actual_str > expected_str
        elif operator == "less_than":
            if actual_num is not None and expected_num is not None:
                return actual_num < expected_num
            return actual_str < expected_str
        elif operator == "greater_equal":
            if actual_num is not None and expected_num is not None:
                return actual_num >= expected_num
            return actual_str >= expected_str
        elif operator == "less_equal":
            if actual_num is not None and expected_num is not None:
                return actual_num <= expected_num
            return actual_str <= expected_str
        else:
            # Unknown operator, default to false
            return False
    
    def _evaluate_condition_group(
        self,
        conditions: List[Dict],
        logic: str,
        context: NodeExecutionContext
    ) -> bool:
        """Evaluate a group of conditions with AND/OR logic"""
        if not conditions:
            return True
        
        results = []
        for condition in conditions:
            result = self._evaluate_condition(
                variable=condition.get("variable", ""),
                operator=condition.get("operator", "equals"),
                value=condition.get("value", ""),
                context=context
            )
            results.append(result)
        
        if logic.lower() == "or":
            return any(results)
        else:  # default to AND
            return all(results)
    
    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()
        
        try:
            node_data = context.node_data
            
            # Get conditions - can be either flat list or grouped
            conditions = node_data.get("conditions", [])
            
            # Check if using new format (ConditionGroup) or old format (flat list)
            if conditions and isinstance(conditions, list):
                if conditions and isinstance(conditions[0], dict):
                    # Check if it's a ConditionGroup (has 'conditions' key)
                    if "conditions" in conditions[0]:
                        # New ConditionGroup format
                        for group in conditions:
                            group_conditions = group.get("conditions", [])
                            group_logic = group.get("logic", "and")
                            
                            if self._evaluate_condition_group(group_conditions, group_logic, context):
                                elapsed_ms = int((time.time() - start_time) * 1000)
                                return NodeExecutionResult(
                                    node_id=context.node_id,
                                    status="success",
                                    output={"branch": "true", "matched_group": group.get("id")},
                                    elapsed_ms=elapsed_ms,
                                    next_node_id="true"  # Signal to follow IF branch
                                )
                    else:
                        # Old flat format
                        logic = node_data.get("logicalOperator", "and")
                        if self._evaluate_condition_group(conditions, logic, context):
                            elapsed_ms = int((time.time() - start_time) * 1000)
                            return NodeExecutionResult(
                                node_id=context.node_id,
                                status="success",
                                output={"branch": "true"},
                                elapsed_ms=elapsed_ms,
                                next_node_id="true"
                            )
            
            # No conditions matched, return ELSE branch
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={"branch": "false"},
                elapsed_ms=elapsed_ms,
                next_node_id="false"  # Signal to follow ELSE branch
            )
            
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )
