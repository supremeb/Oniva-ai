"""
Output Node Executor
Collects and formats final workflow output
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time


class OutputExecutor(BaseNodeExecutor):
    """Executor for Output node - collects final output"""
    
    node_type = "output"
    
    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()
        
        try:
            node_data = context.node_data
            
            # Get output configuration
            output_name = node_data.get("outputName", "output")
            output_value_path = node_data.get("outputValue", "")
            stream_output = node_data.get("streamOutput", False)
            
            # Get output value with variable substitution
            if output_value_path:
                output_value = self.substitute_variables(
                    output_value_path, context.variables, context.inputs
                )
            else:
                # Default: collect all inputs
                output_value = {}
                for node_id, node_output in context.inputs.items():
                    if isinstance(node_output, dict):
                        # Get the most relevant output
                        if "text" in node_output:
                            output_value[node_id] = node_output["text"]
                        elif "output" in node_output:
                            output_value[node_id] = node_output["output"]
                        elif "result" in node_output:
                            output_value[node_id] = node_output["result"]
                        else:
                            output_value[node_id] = node_output
                    else:
                        output_value[node_id] = node_output
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    output_name: output_value,
                    "final": True,
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
