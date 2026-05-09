"""
Tool Generator

Converts OpenAPI operations to MCP-compatible tool definitions
that can be used by AI agents.
"""

from typing import Any
from .parser import ParsedSpec, OpenAPIOperation


class ToolGenerator:
    """
    Convert OpenAPI operations to MCP-compatible tool definitions.

    Each operation becomes a tool with:
    - name: operationId (sanitized)
    - description: summary + description
    - input_schema: JSON Schema from parameters + requestBody
    - _meta: Internal metadata (path, method, etc.)
    """

    def __init__(self, parsed_spec: ParsedSpec):
        self.spec = parsed_spec
        self.base_url = self._get_base_url()

    def _get_base_url(self) -> str:
        """Get the base URL from servers."""
        if self.spec.servers:
            return self.spec.servers[0].url.rstrip("/")
        return "https://api.example.com"

    def generate_all(self) -> list[dict[str, Any]]:
        """Generate tools for all operations."""
        return [
            self._operation_to_tool(op)
            for op in self.spec.operations
        ]

    def generate_for_operations(self, operation_ids: list[str]) -> list[dict[str, Any]]:
        """Generate tools for specific operations only."""
        return [
            self._operation_to_tool(op)
            for op in self.spec.operations
            if op.operation_id in operation_ids
        ]

    def _operation_to_tool(self, operation: OpenAPIOperation) -> dict[str, Any]:
        """Convert single OpenAPI operation to MCP tool definition."""
        # Build description from summary and/or description
        description = self._build_description(operation)

        # Build input schema from parameters and requestBody
        input_schema = self._build_input_schema(operation)

        return {
            "name": self._sanitize_name(operation.operation_id),
            "description": description,
            "input_schema": input_schema,
            "_meta": {
                "path": operation.path,
                "method": operation.method,
                "base_url": self.base_url,
                "operation_id": operation.operation_id,
                "tags": operation.tags,
            }
        }

    def _sanitize_name(self, operation_id: str) -> str:
        """Sanitize operation ID to be a valid tool name."""
        # Replace non-alphanumeric with underscore
        name = "".join(c if c.isalnum() else "_" for c in operation_id)
        # Remove consecutive underscores
        while "__" in name:
            name = name.replace("__", "_")
        # Remove leading/trailing underscores
        return name.strip("_").lower()

    def _build_description(self, operation: OpenAPIOperation) -> str:
        """Build tool description from operation details."""
        parts = []

        if operation.summary:
            parts.append(operation.summary)

        if operation.description and operation.description != operation.summary:
            # Truncate long descriptions
            desc = operation.description
            if len(desc) > 500:
                desc = desc[:497] + "..."
            parts.append(desc)

        if not parts:
            # Generate from method and path
            parts.append(f"{operation.method} {operation.path}")

        return " ".join(parts)

    def _build_input_schema(self, operation: OpenAPIOperation) -> dict[str, Any]:
        """Build JSON Schema for tool input from parameters and requestBody."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        # Add parameters
        for param in operation.parameters:
            name = param.get("name", "")
            if not name:
                continue

            prop_schema = self._param_to_schema(param)
            properties[name] = prop_schema

            if param.get("required", False):
                required.append(name)

        # Add requestBody
        if operation.request_body:
            body_schema = self._extract_body_schema(operation.request_body)
            if body_schema:
                # Flatten body properties into main schema
                body_props = body_schema.get("properties", {})
                body_required = body_schema.get("required", [])

                for prop_name, prop_schema in body_props.items():
                    # Prefix with body_ if conflicts with param
                    final_name = prop_name
                    if prop_name in properties:
                        final_name = f"body_{prop_name}"
                    properties[final_name] = prop_schema

                    if prop_name in body_required:
                        required.append(final_name)

                # If body is not object type, add as 'body' parameter
                if not body_props and body_schema.get("type"):
                    properties["body"] = body_schema
                    if operation.request_body.get("required", False):
                        required.append("body")

        return {
            "type": "object",
            "properties": properties,
            "required": required if required else [],
        }

    def _param_to_schema(self, param: dict[str, Any]) -> dict[str, Any]:
        """Convert OpenAPI parameter to JSON Schema property."""
        schema = param.get("schema", {})
        result: dict[str, Any] = {}

        # Copy basic schema properties
        for key in ("type", "format", "enum", "default", "minimum", "maximum",
                    "minLength", "maxLength", "pattern", "items"):
            if key in schema:
                result[key] = schema[key]

        # Default to string if no type
        if "type" not in result:
            result["type"] = "string"

        # Add description
        description = param.get("description", schema.get("description", ""))
        if description:
            result["description"] = description

        # Add location hint
        location = param.get("in", "query")
        if location != "query":
            result["x-param-location"] = location

        return result

    def _extract_body_schema(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Extract schema from requestBody."""
        content = request_body.get("content", {})

        # Prefer JSON content types
        for content_type in ("application/json", "application/x-www-form-urlencoded"):
            if content_type in content:
                return content[content_type].get("schema", {})

        # Fall back to first content type
        for media_type in content.values():
            return media_type.get("schema", {})

        return {}
