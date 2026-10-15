"""
Code Node Executor

Runs workflow code nodes in the E2B sandbox only. Code is never executed in the
API process; with the sandbox disabled the node fails with a clear refusal.
"""

import json
import time
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult

# Server-side max timeout cap (seconds)
_MAX_TIMEOUT = 60


def _build_script(code: str, local_vars: Dict[str, Any]) -> str:
    """Prefix the user code with its inputs, injected as a JSON literal."""
    payload = json.dumps(local_vars, default=str)
    preamble = f"import json as _json\nglobals().update(_json.loads({payload!r}))\n"
    return preamble + code


class CodeExecutor(BaseNodeExecutor):
    """Executor for Code node - runs Python code in the E2B sandbox."""

    node_type = "code"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        def _elapsed() -> int:
            return int((time.time() - start_time) * 1000)

        try:
            node_data = context.node_data
            code = node_data.get("code", "")
            timeout = min(node_data.get("timeout", 30), _MAX_TIMEOUT)

            if not code:
                return NodeExecutionResult(
                    node_id=context.node_id, status="failed", error="No code provided"
                )

            settings = get_settings()
            from app.agents.skills.code import SANDBOX_REFUSAL, run_in_sandbox

            if not (settings.ENABLE_SANDBOX and settings.E2B_API_KEY):
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=SANDBOX_REFUSAL,
                    elapsed_ms=_elapsed(),
                )

            local_vars: Dict[str, Any] = {}
            for node_id, node_output in context.inputs.items():
                if isinstance(node_output, dict):
                    for key, value in node_output.items():
                        local_vars[f"{node_id}_{key}"] = value
                    local_vars[node_id] = node_output
                else:
                    local_vars[node_id] = node_output
            for key, value in context.variables.items():
                local_vars[key.replace(".", "_")] = value

            out = await run_in_sandbox(
                _build_script(code, local_vars),
                tenant_id=context.tenant_id,
                session_id=context.session_id or context.workflow_run_id or context.node_id,
                timeout=timeout,
            )
            if not out["success"]:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=out["error"],
                    elapsed_ms=_elapsed(),
                )
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "result": out["result"],
                    "stdout": out["stdout"],
                    "stderr": out["stderr"],
                },
                elapsed_ms=_elapsed(),
            )

        except Exception as e:
            return NodeExecutionResult(
                node_id=context.node_id, status="failed", error=str(e), elapsed_ms=_elapsed()
            )
