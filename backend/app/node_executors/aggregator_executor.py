"""
Variable Aggregator Node Executor
Combines variables from multiple sources into a single output
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
from typing import Any, Dict, List


class AggregatorExecutor(BaseNodeExecutor):
    """Executor for Variable Aggregator node - combines variables"""
    
    node_type = "aggregator"
    
    def _get_value_by_path(self, path: str, context: NodeExecutionContext) -> Any:
        """Get value from inputs or variables by path"""
        parts = path.split(".")
        
        if len(parts) >= 2:
            node_id = parts[0]
            key = ".".join(parts[1:])
            
            if node_id in context.inputs:
                output = context.inputs[node_id]
                if isinstance(output, dict):
                    if key in output:
                        return output[key]
                    # Try nested access
                    current = output
                    for part in parts[1:]:
                        if isinstance(current, dict) and part in current:
                            current = current[part]
                        else:
                            return None
                    return current
                return output
        
        if path in context.variables:
            return context.variables[path]
        
        return None
    
    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()
        
        try:
            node_data = context.node_data
            
            # Get configuration
            variable_paths: List[str] = node_data.get("variables", [])
            group_mode = node_data.get("group", True)
            output_name = node_data.get("outputName", "aggregated")
            
            # Collect values
            aggregated: Dict[str, Any] = {}
            values_list: List[Any] = []
            
            for path in variable_paths:
                if not path:
                    continue
                    
                value = self._get_value_by_path(path, context)
                if value is not None:
                    # Use last part of path as key
                    key = path.split(".")[-1] if "." in path else path
                    aggregated[key] = value
                    values_list.append(value)
            
            # Also aggregate all inputs if no specific paths given
            if not variable_paths:
                for node_id, node_output in context.inputs.items():
                    aggregated[node_id] = node_output
                    values_list.append(node_output)
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            output = {
                output_name: aggregated if group_mode else values_list,
                "aggregated": aggregated,
                "count": len(aggregated),
            }
            
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output,
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
