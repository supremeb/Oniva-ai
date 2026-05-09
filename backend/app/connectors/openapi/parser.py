"""
OpenAPI Specification Parser

Parses and validates OpenAPI 3.x specifications,
resolving $ref references and extracting operation details.
"""

from typing import Any
from pydantic import BaseModel, Field
import json
import yaml


class OpenAPIInfo(BaseModel):
    """Parsed OpenAPI info section."""
    title: str
    version: str
    description: str | None = None


class OpenAPIServer(BaseModel):
    """Parsed OpenAPI server."""
    url: str
    description: str | None = None


class OpenAPIOperation(BaseModel):
    """Parsed OpenAPI operation."""
    operation_id: str
    path: str
    method: str
    summary: str | None = None
    description: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    request_body: dict[str, Any] | None = None
    responses: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    security: list[dict[str, list[str]]] | None = None


class ParsedSpec(BaseModel):
    """Fully parsed OpenAPI specification."""
    openapi_version: str
    info: OpenAPIInfo
    servers: list[OpenAPIServer]
    operations: list[OpenAPIOperation]
    security_schemes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    raw_spec: dict[str, Any] = Field(default_factory=dict)


class OpenAPIParser:
    """
    Parse and validate OpenAPI 3.x specifications.

    Handles:
    - JSON and YAML formats
    - $ref resolution (inline only)
    - Parameter extraction
    - Security scheme detection
    """

    MAX_SPEC_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_RECURSION_DEPTH = 50

    def __init__(self, spec: dict[str, Any] | str):
        """Initialize parser with spec dict or string."""
        if isinstance(spec, str):
            spec = self._parse_string(spec)
        self.spec = spec
        self._validate_version()

    def _parse_string(self, content: str) -> dict[str, Any]:
        """Parse JSON or YAML string to dict."""
        if len(content) > self.MAX_SPEC_SIZE:
            raise ValueError(f"Spec exceeds max size of {self.MAX_SPEC_SIZE} bytes")

        # Try JSON first, then YAML
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            try:
                return yaml.safe_load(content)
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid spec format: {e}")

    def _validate_version(self) -> None:
        """Validate OpenAPI version is 3.x."""
        version = self.spec.get("openapi", "")
        if not version.startswith("3."):
            raise ValueError(f"Unsupported OpenAPI version: {version}. Only 3.x supported.")

    def parse(self) -> ParsedSpec:
        """Parse the specification into structured data."""
        return ParsedSpec(
            openapi_version=self.spec.get("openapi", "3.0.0"),
            info=self._parse_info(),
            servers=self._parse_servers(),
            operations=self._parse_operations(),
            security_schemes=self._parse_security_schemes(),
            raw_spec=self.spec,
        )

    def _parse_info(self) -> OpenAPIInfo:
        """Parse info section."""
        info = self.spec.get("info", {})
        return OpenAPIInfo(
            title=info.get("title", "Unknown API"),
            version=info.get("version", "1.0.0"),
            description=info.get("description"),
        )

    def _parse_servers(self) -> list[OpenAPIServer]:
        """Parse servers section."""
        servers = self.spec.get("servers", [])
        if not servers:
            return [OpenAPIServer(url="https://api.example.com")]

        return [
            OpenAPIServer(
                url=s.get("url", ""),
                description=s.get("description"),
            )
            for s in servers
        ]

    def _parse_operations(self) -> list[OpenAPIOperation]:
        """Parse all operations from paths."""
        operations = []
        paths = self.spec.get("paths", {})

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            # Get path-level parameters
            path_params = path_item.get("parameters", [])

            for method in ("get", "post", "put", "patch", "delete", "head", "options"):
                if method not in path_item:
                    continue

                operation = path_item[method]
                if not isinstance(operation, dict):
                    continue

                # Merge path and operation parameters
                op_params = operation.get("parameters", [])
                all_params = self._merge_parameters(path_params, op_params)

                # Generate operation_id if missing
                op_id = operation.get("operationId")
                if not op_id:
                    op_id = self._generate_operation_id(method, path)

                operations.append(OpenAPIOperation(
                    operation_id=op_id,
                    path=path,
                    method=method.upper(),
                    summary=operation.get("summary"),
                    description=operation.get("description"),
                    parameters=self._resolve_refs(all_params),
                    request_body=self._resolve_refs(operation.get("requestBody")),
                    responses=operation.get("responses", {}),
                    tags=operation.get("tags", []),
                    security=operation.get("security"),
                ))

        return operations

    def _parse_security_schemes(self) -> dict[str, dict[str, Any]]:
        """Parse security schemes from components."""
        components = self.spec.get("components", {})
        return components.get("securitySchemes", {})

    def _merge_parameters(
        self,
        path_params: list[dict],
        op_params: list[dict]
    ) -> list[dict]:
        """Merge path-level and operation-level parameters."""
        # Operation params override path params with same name+in
        param_key = lambda p: (p.get("name"), p.get("in"))
        merged = {param_key(p): p for p in path_params}
        merged.update({param_key(p): p for p in op_params})
        return list(merged.values())

    def _generate_operation_id(self, method: str, path: str) -> str:
        """Generate operation ID from method and path."""
        # /users/{id}/posts -> users_id_posts
        clean_path = path.replace("{", "").replace("}", "").replace("/", "_").strip("_")
        return f"{method}_{clean_path}"

    def _resolve_refs(self, obj: Any, depth: int = 0) -> Any:
        """Resolve $ref references inline."""
        if depth > self.MAX_RECURSION_DEPTH:
            raise ValueError("Max recursion depth exceeded resolving $refs")

        if obj is None:
            return None

        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_path = obj["$ref"]
                resolved = self._get_ref(ref_path)
                return self._resolve_refs(resolved, depth + 1)
            return {k: self._resolve_refs(v, depth + 1) for k, v in obj.items()}

        if isinstance(obj, list):
            return [self._resolve_refs(item, depth + 1) for item in obj]

        return obj

    def _get_ref(self, ref_path: str) -> Any:
        """Get object at $ref path."""
        if not ref_path.startswith("#/"):
            # External refs not supported
            return {"type": "object", "description": f"External ref: {ref_path}"}

        parts = ref_path[2:].split("/")
        obj = self.spec
        for part in parts:
            # Handle URL-encoded characters
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(obj, dict):
                obj = obj.get(part, {})
            else:
                return {}
        return obj
