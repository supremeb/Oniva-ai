"""
Start Node Executor
Initializes workflow variables from user input
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time


class StartExecutor(BaseNodeExecutor):
    """Executor for Start node - initializes workflow variables"""
    
    node_type = "start"
    
    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()
        
        try:
            # Start node passes through input variables
            # The variables are already in context.variables from workflow execution
            output = {
                "variables": context.variables.copy(),
                "query": context.variables.get("sys.query", ""),
            }
            
            # Also expose any defined input variables
            node_variables = context.node_data.get("variables", [])
            for var in node_variables:
                var_name = var.get("name", "")
                if var_name and var_name in context.variables:
                    output[var_name] = context.variables[var_name]
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
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
