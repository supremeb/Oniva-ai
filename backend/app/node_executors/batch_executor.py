"""The ``batch`` node (plan 003, U18): the single fan-out node of a ``/execute-batch`` run.

It exists only in the synthetic graph ``workflow_batch_engine`` builds. The gateway's fan-out ``prepare`` and
``finish`` do all of its work (``internal_api/workflow_nodes.py``), so ``execute`` is never a real path.
"""
from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult


class BatchExecutor(BaseNodeExecutor):
    node_type = "batch"

    async def execute(self, context: NodeExecutionContext, db) -> NodeExecutionResult:
        return NodeExecutionResult(
            node_id=context.node_id,
            status="failed",
            error="A batch node runs only as an engine fan-out",
        )
