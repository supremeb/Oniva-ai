"""
Bridge SandboxConnector tools into plain async functions for BaseAgent.

BaseAgent.run() wraps extra_tools with PydanticTool(func, takes_ctx=True),
so we return plain async functions with ctx as first param and __name__ set.
"""

from __future__ import annotations

from typing import Any

from app.connectors.openapi.sandbox_connector import SandboxConnector


def build_pydantic_tools(
    connector: SandboxConnector,
    tenant_id: str,
    user_id: str,
    user_session_id: str,
    credentials: dict[str, Any],
) -> list:
    """
    Build plain async tool functions for a SandboxConnector.

    Returns functions compatible with BaseAgent.run(extra_tools=[...]).
    Each function accepts ctx as first param (takes_ctx=True convention)
    and has __name__ and __doc__ set for tool registration.
    """
    tools = []
    cid = connector.metadata.id
    tool_defs = connector.get_tools()

    # Search tool (pure in-memory, no I/O)
    async def search_fn(ctx, query: str) -> list[dict[str, Any]]:
        return connector._search_operations(query)

    search_fn.__name__ = f"search_{cid}_api"
    search_fn.__doc__ = tool_defs[0]["description"]
    tools.append(search_fn)

    # Execute tool (async — E2B sandbox call)
    async def execute_fn(ctx, code: str) -> dict[str, Any]:
        result_str = await connector._execute_in_sandbox(
            code=code,
            tenant_id=tenant_id,
            user_id=user_id,
            user_session_id=user_session_id,
            credentials=credentials,
        )
        # Return structured dict so the frontend can render stdout in ToolCallResult
        is_error = result_str.startswith("Error") or result_str.startswith("ERROR")
        return {
            "success": not is_error,
            "stdout": result_str if not is_error else "",
            "stderr": result_str if is_error else "",
            "connector": cid,
        }

    execute_fn.__name__ = f"execute_{cid}_api"
    execute_fn.__doc__ = tool_defs[1]["description"]
    tools.append(execute_fn)

    return tools
