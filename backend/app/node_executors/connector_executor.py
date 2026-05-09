"""
Connector Node Executor
Executes data retrieval from configured connectors
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Dict, Any
from uuid import UUID
import time
import httpx

from app.models.connector import Connector
from app.core.encryption import get_encryption_service
from app.security.url_validator import is_url_safe


class ConnectorOutput(BaseModel):
    """Output schema for Connector node"""
    data: Any
    connector_name: str
    connector_type: str
    execution_time: float


class ConnectorExecutor(BaseNodeExecutor):
    """Executor for Connector node - fetches data from connectors"""

    node_type = "connector"

    # Connectors call external MCP services
    makes_external_calls = True

    async def _execute_rest_connector(
        self,
        connector: Connector,
        query: str,
        inputs: Dict[str, Any]
    ) -> Any:
        """Execute REST API connector"""
        config = connector.config

        base_url = config.get("base_url", "")
        method = config.get("method", "GET")
        headers = config.get("headers", {})

        # Decrypt credentials if present
        if connector.encrypted_credentials:
            encryption = get_encryption_service()
            credentials = encryption.decrypt(connector.encrypted_credentials)
            # Add authorization header if credentials exist
            if credentials:
                headers["Authorization"] = f"Bearer {credentials}"

        # Build full URL
        url = f"{base_url}{query}" if query else base_url

        # SSRF validation on resolved URL
        ssrf_error = is_url_safe(url)
        if ssrf_error:
            raise ValueError(f"URL validation failed: {ssrf_error}")

        # Make HTTP request
        async with httpx.AsyncClient() as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers, params=inputs)
            elif method.upper() == "POST":
                response = await client.post(url, headers=headers, json=inputs)
            elif method.upper() == "PUT":
                response = await client.put(url, headers=headers, json=inputs)
            elif method.upper() == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()

    async def _execute_graphql_connector(
        self,
        connector: Connector,
        query: str,
        inputs: Dict[str, Any]
    ) -> Any:
        """Execute GraphQL connector"""
        config = connector.config

        endpoint = config.get("endpoint", "")
        headers = config.get("headers", {})

        # Decrypt credentials if present
        if connector.encrypted_credentials:
            encryption = get_encryption_service()
            credentials = encryption.decrypt(connector.encrypted_credentials)
            if credentials:
                headers["Authorization"] = f"Bearer {credentials}"

        # SSRF validation on endpoint URL
        ssrf_error = is_url_safe(endpoint)
        if ssrf_error:
            raise ValueError(f"URL validation failed: {ssrf_error}")

        # Build GraphQL request
        graphql_request = {
            "query": query,
            "variables": inputs
        }

        # Make HTTP request
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=graphql_request
            )
            response.raise_for_status()
            result = response.json()

            # Check for GraphQL errors
            if "errors" in result:
                raise Exception(f"GraphQL errors: {result['errors']}")

            return result.get("data", {})

    async def _execute_mcp_connector(
        self,
        connector: Connector,
        query: str,
        inputs: Dict[str, Any]
    ) -> Any:
        """Execute MCP (Model Context Protocol) connector"""
        raise NotImplementedError(
            f"MCP connector execution not implemented for '{connector.name}'. "
            "Use REST or GraphQL connector types instead."
        )

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data

            # Get connector ID
            connector_id_str = node_data.get("connectorId")
            if not connector_id_str:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error="Connector ID not specified",
                    execution_time=time.time() - start_time
                )

            try:
                connector_id = UUID(connector_id_str)
            except ValueError:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Invalid connector ID: {connector_id_str}",
                    execution_time=time.time() - start_time
                )

            # Get connector from database
            stmt = select(Connector).where(Connector.id == connector_id)
            result = await db.execute(stmt)
            connector = result.scalar_one_or_none()

            if not connector:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Connector not found: {connector_id}",
                    execution_time=time.time() - start_time
                )

            if not connector.is_active:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Connector '{connector.name}' is not active",
                    execution_time=time.time() - start_time
                )

            # Get query and inputs with variable substitution
            query = node_data.get("query", "")
            query = self.substitute_variables(
                query,
                context.variables,
                context.inputs
            )

            # Process inputs
            connector_inputs = node_data.get("inputs", {})
            processed_inputs: Dict[str, Any] = {}
            for key, value in connector_inputs.items():
                if isinstance(value, str):
                    processed_inputs[key] = self.substitute_variables(
                        value,
                        context.variables,
                        context.inputs
                    )
                else:
                    processed_inputs[key] = value

            # Execute based on connector type
            connector_type = connector.connector_type

            if connector_type == "rest_api":
                data = await self._execute_rest_connector(
                    connector, query, processed_inputs
                )
            elif connector_type == "graphql":
                data = await self._execute_graphql_connector(
                    connector, query, processed_inputs
                )
            elif connector_type == "mcp":
                data = await self._execute_mcp_connector(
                    connector, query, processed_inputs
                )
            else:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Unsupported connector type: {connector_type}",
                    execution_time=time.time() - start_time
                )

            execution_time = time.time() - start_time

            # Prepare output
            output = ConnectorOutput(
                data=data,
                connector_name=connector.name,
                connector_type=connector_type,
                execution_time=execution_time
            )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output.model_dump(),
                execution_time=execution_time
            )

        except httpx.HTTPError as e:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="error",
                error=f"HTTP error: {str(e)}",
                execution_time=time.time() - start_time
            )
        except Exception as e:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="error",
                error=f"Connector execution failed: {str(e)}",
                execution_time=time.time() - start_time
            )
