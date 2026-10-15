"""
Security tests for CodeExecutor: workflow code nodes never run in-process.
See tests/security/test_no_inprocess_exec.py for the AE2 escape-payload coverage.
"""

from unittest.mock import MagicMock, patch

from app.node_executors import NodeExecutionContext
from app.node_executors.code_executor import CodeExecutor


def _ctx(code: str) -> NodeExecutionContext:
    return NodeExecutionContext(
        node_id="n", node_type="code", node_data={"code": code, "timeout": 1},
        inputs={}, variables={}, user_id="u", tenant_id="t", session_id="s",
    )


async def test_code_executor_refuses_without_sandbox():
    settings = MagicMock(ENABLE_SANDBOX=False, E2B_API_KEY="")
    with patch("app.node_executors.code_executor.get_settings", return_value=settings):
        result = await CodeExecutor().execute(_ctx("import os\nresult = os.getcwd()"), MagicMock())
    assert result.status == "failed"
    assert "sandbox" in result.error.lower()


async def test_code_executor_rejects_empty_code():
    result = await CodeExecutor().execute(_ctx(""), MagicMock())
    assert result.status == "failed"
    assert "No code" in result.error
