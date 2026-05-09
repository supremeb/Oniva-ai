"""
Template Node Executor
Formats strings with variable substitution
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time


class TemplateExecutor(BaseNodeExecutor):
    """Executor for Template node - string formatting with variables"""
    
    node_type = "template"
    
    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()
        
        try:
            node_data = context.node_data
            
            # Get template
            template = node_data.get("template", "")
            
            if not template:
                elapsed_ms = int((time.time() - start_time) * 1000)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output={"output": "", "text": ""},
                    elapsed_ms=elapsed_ms
                )
            
            # Substitute variables
            result = self.substitute_variables(template, context.variables, context.inputs)
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": result,
                    "text": result,
                    "template": template,
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
