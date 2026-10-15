"""
Connector Tools Skills

Provides skills that enable agents to use configured connectors.
Wraps connector functionality as invokable skills.

Token-efficient pattern: Instead of expanding N tool definitions per connector
into the LLM context (~13,000-18,000 tokens), agents use these 3 generic skills
to discover and invoke connectors on demand (~225 tokens).
"""

import logging
from typing import Dict, Any, List, Optional
from app.agents.skills.registry import skill

logger = logging.getLogger(__name__)


def _get_connectors(ctx) -> Dict[str, Dict[str, Any]]:
    """Get connectors dict from SkillContext (or fallback for RunContext).

    Also includes auto-discovered native connectors from the registry
    so agents always have access to native connectors like BankConnector
    without requiring explicit UI toggle.
    """
    connectors = {}

    # Session-specific connectors (from active_connector_ids toggle)
    if hasattr(ctx, 'connectors'):
        connectors.update(ctx.connectors or {})
    elif hasattr(ctx, 'deps'):
        connectors.update(getattr(ctx.deps, 'connectors', {}))

    # Auto-discovered native connectors from registry
    from app.connectors.registry import ConnectorRegistry
    for meta in ConnectorRegistry.list_available():
        provider_id = meta.id
        # Skip if already in session connectors (avoid duplicates)
        if any(c.get("provider") == provider_id for c in connectors.values()):
            continue
        if getattr(meta, 'auth_type', '') == 'none':
            # Auto-include auth-free native connectors (test fixtures, built-in data)
            connectors[provider_id] = {
                "provider": provider_id,
                "name": meta.name,
                "type": "native",
                "is_verified": True,
            }

    return connectors


@skill(
    name="list_available_connectors",
    description="List all connectors configured by the user and their available tools",
    category="connector",
    version="1.0.0",
    author="oniva",
    requires=[],
    mcp_dependencies=[],
    retryable=True,
)
async def list_available_connectors(ctx) -> Dict[str, Any]:
    """
    List all connectors available to the current user.

    Returns information about each configured connector and its available tools.
    Includes auto-discovered native connectors (e.g., Synthetic Bank).
    Use this to discover what external services the user has connected.
    """
    from app.connectors.registry import ConnectorRegistry

    connectors = _get_connectors(ctx)
    logger.warning(f"[CONNECTOR_SKILL] list_available_connectors called. Found {len(connectors)} connectors: {list(connectors.keys())}")

    result = []
    for conn_id, conn_info in connectors.items():
        # Load tools dynamically from the connector registry
        provider = conn_info.get("provider", "")
        tools_summary = []

        connector_instance = ConnectorRegistry.get_connector(provider)
        if connector_instance:
            try:
                raw_tools = connector_instance.get_tools()
                tools_summary = [
                    {"name": t.get("name", ""), "description": (t.get("description", "") or "")[:80]}
                    for t in raw_tools
                ]
            except Exception:
                pass

        result.append({
            "connector_id": conn_id,
            "name": conn_info.get("name", "Unknown"),
            "type": conn_info.get("type", "unknown"),
            "provider": provider,
            "is_verified": conn_info.get("is_verified", False),
            "tools": tools_summary
        })

    return {
        "connectors": result,
        "total_count": len(result)
    }


@skill(
    name="invoke_connector_tool",
    description="Execute a tool from a configured connector (e.g., search ServiceNow, create incidents)",
    category="connector",
    version="1.0.0",
    author="oniva",
    requires=[],
    mcp_dependencies=[]
)
async def invoke_connector_tool(
    ctx,
    connector_id: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Execute a tool from a configured connector.

    Args:
        connector_id: The ID of the user's connector instance (from list_available_connectors)
        tool_name: The name of the tool to execute (e.g., "search_issues", "create_incident")
        arguments: Arguments to pass to the tool (tool-specific)

    Returns:
        The result from the connector tool execution

    Example:
        # Search Linear issues
        result = await invoke_connector_tool(
            connector_id="abc123",
            tool_name="search_issues",
            arguments={"query": "status:todo", "limit": 10}
        )
    """
    from app.connectors.registry import ConnectorRegistry

    connectors = _get_connectors(ctx)

    # Validate connector exists
    if connector_id not in connectors:
        return {
            "success": False,
            "error": f"Connector '{connector_id}' not found. Use list_available_connectors to see available connectors."
        }

    conn_info = connectors[connector_id]

    # Get connector instance and execute
    try:
        connector_instance = ConnectorRegistry.get_connector(conn_info["provider"])
        if not connector_instance:
            return {
                "success": False,
                "error": f"Connector provider '{conn_info['provider']}' not available"
            }

        # Validate tool exists on the connector
        raw_tools = connector_instance.get_tools()
        tool_info = next((t for t in raw_tools if t.get("name") == tool_name), None)
        if not tool_info:
            available = [t.get("name", "") for t in raw_tools]
            return {
                "success": False,
                "error": f"Tool '{tool_name}' not found. Available tools: {available}"
            }

        # Mark write-method tools as non-retryable (HTTP method safeguard)
        http_method = (tool_info.get("method") or "GET").upper()
        _write_methods = {"POST", "PUT", "DELETE", "PATCH"}
        is_write = http_method in _write_methods or tool_name.startswith(("create_", "delete_", "update_"))
        if is_write:
            # Attach flag for observability and retry gating
            tool_info["_is_write"] = True

        # Bind THIS tenant's stored credentials to a per-call instance (R2).
        # The registry never hands out a shared, credential-holding client.
        tenant_id = getattr(ctx, "tenant_id", None) or (
            getattr(getattr(ctx, "deps", None), "tenant_id", None)
        )
        authed = await ConnectorRegistry.get_authenticated_connector(
            conn_info["provider"], tenant_id, db=getattr(ctx, "db", None)
        )
        if authed is None:
            return {
                "success": False,
                "error": f"Connector '{conn_info.get('name', conn_info['provider'])}' is not connected for this workspace."
            }
        try:
            result = await authed.call_tool(tool_name, arguments or {})
        finally:
            try:
                await authed.disconnect()
            except Exception:
                pass

        return {
            "success": True,
            "connector": conn_info.get("name", conn_info["provider"]),
            "tool": tool_name,
            "result": result
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Tool execution failed: {str(e)}"
        }


@skill(
    name="get_connector_schema",
    description="Get the input schema for a specific connector tool before invoking it",
    category="connector",
    version="1.0.0",
    author="oniva",
    requires=[],
    mcp_dependencies=[],
    retryable=True,
)
async def get_connector_schema(
    ctx,
    connector_id: str,
    tool_name: str
) -> Dict[str, Any]:
    """
    Get the input schema for a connector tool.

    Useful for understanding what arguments a tool expects before invoking it.

    Args:
        connector_id: The ID of the user's connector instance
        tool_name: The name of the tool

    Returns:
        The JSON schema for the tool's input parameters
    """
    from app.connectors.registry import ConnectorRegistry

    connectors = _get_connectors(ctx)

    if connector_id not in connectors:
        return {
            "success": False,
            "error": f"Connector '{connector_id}' not found. Use list_available_connectors to see available connectors."
        }

    conn_info = connectors[connector_id]

    connector_instance = ConnectorRegistry.get_connector(conn_info.get("provider", ""))
    if not connector_instance:
        return {
            "success": False,
            "error": f"Connector provider '{conn_info.get('provider')}' not available"
        }

    raw_tools = connector_instance.get_tools()
    tool_info = next((t for t in raw_tools if t.get("name") == tool_name), None)

    if not tool_info:
        available = [t.get("name", "") for t in raw_tools]
        return {
            "success": False,
            "error": f"Tool '{tool_name}' not found. Available: {available}"
        }

    return {
        "success": True,
        "tool_name": tool_info.get("name"),
        "description": tool_info.get("description", ""),
        "input_schema": tool_info.get("input_schema", tool_info.get("inputSchema", {}))
    }


# Provider-specific convenience skills

@skill(
    name="linear_search_issues",
    description="Search for issues in Linear (requires Linear connector)",
    category="connector",
    version="1.0.0",
    author="oniva",
    requires=["invoke_connector_tool"],
    mcp_dependencies=[],
    retryable=True,
)
async def linear_search_issues(
    ctx,
    query: str,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Search for issues in Linear.

    Args:
        query: Search query using Linear's search syntax
            Examples:
            - "status:todo priority:high"
            - "assignee:@me"
            - "project:Q1-Goals"
        limit: Maximum number of results (default 50)

    Returns:
        Search results with matching issues
    """
    connectors = _get_connectors(ctx)
    linear_conn = None

    for conn_id, conn_info in connectors.items():
        if conn_info.get("provider") == "linear":
            linear_conn = conn_id
            break

    if not linear_conn:
        return {
            "success": False,
            "error": "No Linear connector configured. Please add a Linear connector in Settings."
        }

    return await invoke_connector_tool(
        ctx,
        connector_id=linear_conn,
        tool_name="search_issues",
        arguments={"query": query, "limit": limit}
    )


@skill(
    name="linear_create_issue",
    description="Create a new issue in Linear (requires Linear connector)",
    category="connector",
    version="1.0.0",
    author="oniva",
    requires=["invoke_connector_tool"],
    mcp_dependencies=[]
)
async def linear_create_issue(
    ctx,
    title: str,
    description: Optional[str] = None,
    team_id: Optional[str] = None,
    priority: Optional[str] = None,
    assignee_id: Optional[str] = None,
    labels: Optional[List[str]] = None,
    project_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new issue in Linear.

    Args:
        title: Issue title
        description: Issue description (markdown supported)
        team_id: Team ID to assign issue to
        priority: Priority level (urgent, high, medium, low)
        assignee_id: User ID to assign issue to
        labels: List of label IDs or names
        project_id: Project ID to add issue to

    Returns:
        Created issue details
    """
    connectors = _get_connectors(ctx)
    linear_conn = None

    for conn_id, conn_info in connectors.items():
        if conn_info.get("provider") == "linear":
            linear_conn = conn_id
            break

    if not linear_conn:
        return {
            "success": False,
            "error": "No Linear connector configured."
        }

    args = {"title": title}
    if description:
        args["description"] = description
    if team_id:
        args["teamId"] = team_id
    if priority:
        args["priority"] = priority
    if assignee_id:
        args["assigneeId"] = assignee_id
    if labels:
        args["labels"] = labels
    if project_id:
        args["projectId"] = project_id

    return await invoke_connector_tool(
        ctx,
        connector_id=linear_conn,
        tool_name="create_issue",
        arguments=args
    )


@skill(
    name="servicenow_create_incident",
    description="Create a new incident in ServiceNow (requires ServiceNow connector)",
    category="connector",
    version="1.0.0",
    author="oniva",
    requires=["invoke_connector_tool"],
    mcp_dependencies=[]
)
async def servicenow_create_incident(
    ctx,
    short_description: str,
    description: Optional[str] = None,
    urgency: Optional[str] = None,
    impact: Optional[str] = None,
    assignment_group: Optional[str] = None,
    category: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new incident in ServiceNow.

    Args:
        short_description: Brief summary of the incident
        description: Detailed description
        urgency: Urgency level (1=High, 2=Medium, 3=Low)
        impact: Impact level (1=High, 2=Medium, 3=Low)
        assignment_group: Group to assign the incident to
        category: Incident category

    Returns:
        Created incident details
    """
    connectors = _get_connectors(ctx)
    snow_conn = None

    for conn_id, conn_info in connectors.items():
        if conn_info.get("provider") == "servicenow":
            snow_conn = conn_id
            break

    if not snow_conn:
        return {
            "success": False,
            "error": "No ServiceNow connector configured."
        }

    args = {"short_description": short_description}
    if description:
        args["description"] = description
    if urgency:
        args["urgency"] = urgency
    if impact:
        args["impact"] = impact
    if assignment_group:
        args["assignment_group"] = assignment_group
    if category:
        args["category"] = category

    return await invoke_connector_tool(
        ctx,
        connector_id=snow_conn,
        tool_name="create_incident",
        arguments=args
    )


@skill(
    name="servicenow_search_incidents",
    description="Search for incidents in ServiceNow (requires ServiceNow connector)",
    category="connector",
    version="1.0.0",
    author="oniva",
    requires=["invoke_connector_tool"],
    mcp_dependencies=[],
    retryable=True,
)
async def servicenow_search_incidents(
    ctx,
    query: Optional[str] = None,
    state: Optional[str] = None,
    assignment_group: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Search for incidents in ServiceNow.

    Args:
        query: Text search query
        state: Filter by state (new, in_progress, resolved, closed)
        assignment_group: Filter by assignment group
        limit: Maximum number of results

    Returns:
        Search results with matching incidents
    """
    connectors = _get_connectors(ctx)
    snow_conn = None

    for conn_id, conn_info in connectors.items():
        if conn_info.get("provider") == "servicenow":
            snow_conn = conn_id
            break

    if not snow_conn:
        return {
            "success": False,
            "error": "No ServiceNow connector configured."
        }

    args = {"limit": limit}
    if query:
        args["query"] = query
    if state:
        args["state"] = state
    if assignment_group:
        args["assignment_group"] = assignment_group

    return await invoke_connector_tool(
        ctx,
        connector_id=snow_conn,
        tool_name="search_incidents",
        arguments=args
    )
