from pydantic import BaseModel
from pydantic_ai import RunContext
from typing import List, Dict, Any, Optional
import httpx

from app.agents.base import BaseAgent, AgentDependencies
from app.agents.tool_results import VolumeFileResult, ConnectorFetchResult, RestApiFetchResult


class DataRetrievalResult(BaseModel):
    """Result from data retrieval agent"""
    success: bool
    data: Dict[str, Any] = {}
    sources: List[str] = []
    error: Optional[str] = None
    artifact_uri: Optional[str] = None  # URI to saved data snapshot


class DataRetrievalAgent(BaseAgent):
    """
    Agent for retrieving data from external sources.
    Supports MCP connectors, REST APIs, and custom integrations.

    Storage Pattern:
    - Reads from mounted volumes (market_data, sec_filings, etc.)
    - Saves data snapshots as artifacts for compliance
    - Logs retrieval actions to session history
    """

    def __init__(self):
        super().__init__(
            name="Data Retrieval Agent",
            description="Fetches data from configured connectors and external APIs",
            output_type=DataRetrievalResult,
            system_prompt="""You are a data retrieval specialist for financial intelligence.
Your job is to fetch data from various sources using the available connectors.

When a user requests data:
1. Identify which connector(s) to use
2. Call the appropriate tool to fetch the data
3. Return structured data with source attribution

Available data sources will be provided in the context.""",
            model="openai/gpt-4o-mini",  # Use fast, cheap model for data tasks
        )

        # Register tools
        @self.agent.tool
        async def read_volume_file(
            ctx: RunContext[AgentDependencies],
            volume_name: str,
            file_path: str
        ) -> VolumeFileResult:
            """
            Read a file from a mounted volume.

            Args:
                volume_name: Name of the volume (e.g., 'market_data', 'sec_filings')
                file_path: Path to file within the volume

            Returns:
                File content or error
            """
            from app.services.volumes import get_volume_manager
            from app.config import get_settings

            settings = get_settings()
            if not getattr(settings, 'USE_VOLUMES', False):
                return {"error": "Volumes feature not enabled"}

            try:
                volume_mgr = get_volume_manager()
                content = await volume_mgr.read_file(str(ctx.deps.tenant_id), volume_name, file_path)
                return {
                    "success": True,
                    "volume": volume_name,
                    "file_path": file_path,
                    "content": content.decode('utf-8') if isinstance(content, bytes) else content,
                    "size_bytes": len(content)
                }
            except Exception as e:
                return {"error": str(e), "success": False}

        @self.agent.tool
        async def fetch_from_connector(
            ctx: RunContext[AgentDependencies],
            connector_id: str,
            query: str
        ) -> ConnectorFetchResult:
            """
            Fetch data from a configured connector

            Args:
                connector_id: ID of the connector to use
                query: Search query or data request

            Returns:
                Retrieved data from the connector
            """
            return {
                "error": "fetch_from_connector not implemented. Use the connector system directly.",
                "connector_id": connector_id,
                "query": query,
                "data": None,
            }

        @self.agent.tool
        async def fetch_from_rest_api(
            ctx: RunContext[AgentDependencies],
            url: str,
            method: str = "GET",
            headers: Optional[Dict[str, str]] = None,
            params: Optional[Dict[str, Any]] = None
        ) -> RestApiFetchResult:
            """
            Fetch data from a REST API endpoint

            Args:
                url: API endpoint URL
                method: HTTP method (GET, POST, etc.)
                headers: Optional HTTP headers
                params: Optional query parameters

            Returns:
                API response data
            """
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    if method == "GET":
                        response = await client.get(url, headers=headers, params=params)
                    elif method == "POST":
                        response = await client.post(url, headers=headers, json=params)
                    else:
                        return {"error": f"Unsupported method: {method}"}

                    response.raise_for_status()
                    return {
                        "status_code": response.status_code,
                        "data": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
                        "headers": dict(response.headers)
                    }
                except Exception as e:
                    return {
                        "error": str(e),
                        "status": "failed"
                    }


# Create singleton instance
data_retrieval_agent = DataRetrievalAgent()
