"""
OpenAPI Connector

Dynamically generated connector from OpenAPI specifications.
Extends BaseConnector to integrate with the existing connector architecture.
"""

from typing import Any
import logging

from app.connectors.base import BaseConnector, ConnectorMetadata
from .parser import OpenAPIParser, ParsedSpec
from .tool_generator import ToolGenerator
from .http_client import SafeHTTPClient
from .auth import AuthHandler, AuthConfig, detect_auth_type

logger = logging.getLogger(__name__)


class OpenAPIConnector(BaseConnector):
    """
    Connector that generates tools dynamically from an OpenAPI specification.

    Usage:
        # From spec dict
        connector = OpenAPIConnector(spec_dict, metadata)
        await connector.authenticate({"api_key": "..."})

        # From URL (async)
        connector = await OpenAPIConnector.from_url("https://api.example.com/openapi.json")

        # From registry
        connector = await OpenAPIConnector.from_registry("stripe")
    """

    def __init__(
        self,
        spec: dict[str, Any],
        metadata: ConnectorMetadata | None = None,
        auth_config: AuthConfig | None = None,
    ):
        """
        Initialize connector from OpenAPI spec.

        Args:
            spec: Parsed OpenAPI specification dict
            metadata: Optional connector metadata (generated from spec if not provided)
            auth_config: Optional auth config (detected from spec if not provided)
        """
        # Parse the spec
        self._parser = OpenAPIParser(spec)
        self._parsed_spec: ParsedSpec = self._parser.parse()

        # Generate metadata from spec if not provided
        if metadata is None:
            info = self._parsed_spec.info
            metadata = ConnectorMetadata(
                id=self._generate_id(info.title),
                name=info.title,
                description=info.description or f"Auto-generated from {info.title}",
                icon="🔌",
                category="custom",
                auth_type="bearer",
                version=info.version,
            )
        self.metadata = metadata

        # Detect auth config from security schemes
        if auth_config is None:
            auth_config = detect_auth_type(self._parsed_spec.security_schemes)
        self._auth_config = auth_config

        # Initialize tool generator
        self._tool_generator = ToolGenerator(self._parsed_spec)
        self._tools: list[dict[str, Any]] | None = None

        # HTTP client and auth (set during authenticate)
        self._http_client: SafeHTTPClient | None = None
        self._auth_handler: AuthHandler | None = None
        self._is_authenticated = False

    def _generate_id(self, title: str) -> str:
        """Generate connector ID from API title."""
        # Sanitize: lowercase, replace spaces with underscores
        return "".join(c if c.isalnum() else "_" for c in title.lower()).strip("_")

    @classmethod
    async def from_url(
        cls,
        spec_url: str,
        metadata: ConnectorMetadata | None = None,
        auth_config: AuthConfig | None = None,
    ) -> "OpenAPIConnector":
        """
        Create connector from OpenAPI spec URL.

        Fetches spec with SSRF protection.
        """
        client = SafeHTTPClient()
        try:
            spec = await client.fetch_spec(spec_url)
            return cls(spec, metadata, auth_config)
        finally:
            await client.close()

    @classmethod
    async def from_registry(
        cls,
        connector_id: str,
        credentials: dict[str, Any] | None = None,
    ) -> "OpenAPIConnector":
        """
        Create connector from curated registry.

        Args:
            connector_id: Registry connector ID (e.g., "stripe")
            credentials: Optional credentials to authenticate immediately
        """
        from .registry import get_registry_connector

        registry_entry = get_registry_connector(connector_id)
        if not registry_entry:
            raise ValueError(f"Connector not found in registry: {connector_id}")

        # Create metadata from registry
        metadata = ConnectorMetadata(
            id=connector_id,
            name=registry_entry["name"],
            description=registry_entry["description"],
            icon=registry_entry.get("icon", "🔌"),
            category=registry_entry.get("category", "other"),
            auth_type=registry_entry.get("auth_type", "bearer"),
            version="1.0.0",
        )

        # Create auth config
        auth_type = registry_entry.get("auth_type", "bearer")
        auth_config = AuthConfig(
            auth_type=auth_type,
            location=registry_entry.get("auth_location", "header"),
            name=registry_entry.get("auth_header", "Authorization"),
        )

        # Fetch spec and create connector
        connector = await cls.from_url(
            registry_entry["spec_url"],
            metadata,
            auth_config,
        )

        # Authenticate if credentials provided
        if credentials:
            await connector.authenticate(credentials)

        return connector

    async def authenticate(self, credentials: dict[str, Any]) -> bool:
        """
        Configure authentication for API calls.

        Args:
            credentials: Dict containing auth credentials based on auth_type:
                - bearer: {"token": "..."} or {"api_key": "..."}
                - api_key: {"api_key": "..."}
                - basic: {"username": "...", "password": "..."}
                - oauth2: {"access_token": "...", "refresh_token": "..."}

        Returns:
            True if authentication configured successfully
        """
        try:
            self._auth_handler = AuthHandler.from_config(self._auth_config, credentials)

            if not self._auth_handler.validate_credentials(credentials):
                logger.warning(f"Invalid credentials for {self.metadata.id}")
                return False

            # Create HTTP client
            self._http_client = SafeHTTPClient()
            self._is_authenticated = True

            logger.info(f"Authenticated connector: {self.metadata.id}")
            return True

        except Exception as e:
            logger.error(f"Authentication failed for {self.metadata.id}: {e}")
            return False

    async def disconnect(self) -> bool:
        """Disconnect and cleanup resources."""
        if self._http_client:
            await self._http_client.close()
            self._http_client = None

        self._auth_handler = None
        self._is_authenticated = False

        logger.info(f"Disconnected connector: {self.metadata.id}")
        return True

    async def health_check(self) -> dict[str, Any]:
        """
        Check connection status.

        Returns dict with 'status' key ('healthy', 'unhealthy', 'unauthenticated').
        """
        if not self._is_authenticated:
            return {
                "status": "unauthenticated",
                "message": "Connector not authenticated",
            }

        # Try a simple request to verify credentials
        try:
            # Use first server URL as health check endpoint
            base_url = self._tool_generator.base_url
            await self._http_client.request("HEAD", base_url)
            return {
                "status": "healthy",
                "message": "Connection successful",
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "message": str(e),
            }

    def get_tools(self) -> list[dict[str, Any]]:
        """
        Return list of available MCP-style tool definitions.

        Tools are generated lazily and cached.
        """
        if self._tools is None:
            self._tools = self._tool_generator.generate_all()
        return self._tools

    def get_tools_for_operations(self, operation_ids: list[str]) -> list[dict[str, Any]]:
        """Get tools for specific operations only."""
        return self._tool_generator.generate_for_operations(operation_ids)

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """
        Execute a tool and return result.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments

        Returns:
            API response data
        """
        if not self._is_authenticated:
            raise RuntimeError(f"Connector {self.metadata.id} not authenticated")

        # Find the tool definition
        tool = self._find_tool(tool_name)
        if not tool:
            raise ValueError(f"Tool not found: {tool_name}")

        # Extract request details from tool metadata
        meta = tool.get("_meta", {})
        path = meta.get("path", "/")
        method = meta.get("method", "GET")
        base_url = meta.get("base_url", self._tool_generator.base_url)

        # Build request
        url = f"{base_url}{path}"
        headers: dict[str, str] = {}
        params: dict[str, Any] = {}
        body: dict[str, Any] | None = None

        # Apply authentication
        self._auth_handler.apply(headers, params)

        # Separate arguments by type
        input_schema = tool.get("input_schema", {})
        properties = input_schema.get("properties", {})

        for arg_name, arg_value in arguments.items():
            if arg_name not in properties:
                continue

            prop = properties[arg_name]
            location = prop.get("x-param-location", "query")

            if location == "path":
                # Replace path parameter
                url = url.replace(f"{{{arg_name}}}", str(arg_value))
            elif location == "header":
                headers[arg_name] = str(arg_value)
            elif location == "query":
                params[arg_name] = arg_value
            else:
                # Body parameter
                if body is None:
                    body = {}
                body[arg_name] = arg_value

        # Handle body parameters that aren't location-tagged
        if body is None and method in ("POST", "PUT", "PATCH"):
            # Check for body-like arguments
            body_args = {k: v for k, v in arguments.items()
                        if k not in params and f"{{{k}}}" not in path}
            if body_args:
                body = body_args

        # Make request
        response = await self._http_client.request(
            method=method,
            url=url,
            headers=headers,
            params=params if params else None,
            json_body=body,
        )

        # Parse response
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        else:
            return response.text

    def _find_tool(self, tool_name: str) -> dict[str, Any] | None:
        """Find tool by name."""
        tools = self.get_tools()
        for tool in tools:
            if tool.get("name") == tool_name:
                return tool
        return None

    @property
    def operation_count(self) -> int:
        """Number of operations/tools available."""
        return len(self.get_tools())

    @property
    def spec_info(self) -> dict[str, Any]:
        """Get parsed spec info for display."""
        return {
            "title": self._parsed_spec.info.title,
            "version": self._parsed_spec.info.version,
            "description": self._parsed_spec.info.description,
            "servers": [s.url for s in self._parsed_spec.servers],
            "operation_count": self.operation_count,
        }
