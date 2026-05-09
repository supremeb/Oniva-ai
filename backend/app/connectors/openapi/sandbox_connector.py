"""
Sandbox Connector — OpenAPI via E2B Code Mode.

Exposes 2 tools per connector (search + execute) instead of N endpoint tools.
Agent writes Python code that runs in an E2B Firecracker microVM with
credentials injected as env vars at sandbox creation time.

Industry pattern: Cloudflare Code Mode, OpenAI Code Interpreter, CodeAct (ICML 2024).
"""

from __future__ import annotations

import logging
from typing import Any

from app.connectors.base import BaseConnector, ConnectorMetadata
from app.connectors.openapi.parser import ParsedSpec

logger = logging.getLogger(__name__)


class SandboxConnector(BaseConnector):
    """
    OpenAPI connector that executes via E2B sandbox (Code Mode pattern).

    Exposes 2 tools (search + execute) instead of N endpoint tools.
    Credentials injected as sandbox env vars at creation, never passed to LLM.
    """

    def __init__(
        self,
        parsed_spec: ParsedSpec,
        metadata: ConnectorMetadata,
        auth_config: dict[str, Any] | None = None,
    ):
        self.metadata = metadata
        self._parsed_spec = parsed_spec
        self._auth_config = auth_config or {}
        self._base_url: str = (
            parsed_spec.servers[0].url if parsed_spec.servers else ""
        )
        self._tags: list[str] = sorted(
            {tag for op in parsed_spec.operations for tag in (op.tags or [])}
        )

    @classmethod
    def from_db_connector(cls, db_row: Any, config: dict[str, Any]) -> "SandboxConnector":
        """
        Build SandboxConnector from a DB Connector row.

        Reconstructs a minimal ParsedSpec from the stored openapi_operations
        tool dicts (which include _meta with path, method, operation_id, tags).
        """
        from app.connectors.openapi.parser import (
            OpenAPIInfo,
            OpenAPIServer,
            OpenAPIOperation,
        )

        operations: list[OpenAPIOperation] = []
        base_url = ""

        for tool_dict in (db_row.openapi_operations or []):
            meta = tool_dict.get("_meta", {})
            if not base_url and meta.get("base_url"):
                base_url = meta["base_url"]

            # Reconstruct parameters from input_schema
            params: list[dict[str, Any]] = []
            input_schema = tool_dict.get("input_schema", {})
            required_names = input_schema.get("required", [])
            for prop_name, prop_schema in input_schema.get("properties", {}).items():
                params.append({
                    "name": prop_name,
                    "in": "query",
                    "schema": {"type": prop_schema.get("type", "string")},
                    "required": prop_name in required_names,
                    "description": prop_schema.get("description", ""),
                })

            operations.append(OpenAPIOperation(
                operation_id=meta.get("operation_id", tool_dict.get("name", "")),
                path=meta.get("path", ""),
                method=meta.get("method", "GET"),
                summary=(tool_dict.get("description", "") or "")[:200],
                description=tool_dict.get("description", ""),
                parameters=params,
                tags=meta.get("tags", []),
            ))

        if not base_url:
            base_url = config.get("base_url", "")

        parsed_spec = ParsedSpec(
            openapi_version=db_row.openapi_spec_version or "3.0.0",
            info=OpenAPIInfo(
                title=db_row.name or db_row.provider,
                version="1.0.0",
                description=db_row.description or "",
            ),
            servers=[OpenAPIServer(url=base_url)] if base_url else [],
            operations=operations,
            security_schemes={},
            raw_spec={},
        )

        conn_metadata = ConnectorMetadata(
            id=db_row.provider,
            name=db_row.name,
            description=db_row.description or f"{db_row.name} API",
            icon=config.get("icon", "🔌"),
            category=config.get("category", "custom"),
            auth_type=config.get("auth_type", "bearer"),
            version="1.0.0",
        )

        return cls(
            parsed_spec=parsed_spec,
            metadata=conn_metadata,
            auth_config={"type": config.get("auth_type", "bearer")},
        )

    # ── BaseConnector abstract interface ─────────────────────

    async def authenticate(self, credentials: dict[str, Any]) -> bool:
        # SandboxConnector doesn't hold credentials — they're passed per-call.
        return True

    async def disconnect(self) -> bool:
        return True

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy", "message": "Sandbox connector ready"}

    def get_tools(self) -> list[dict[str, Any]]:
        """Return 2 dynamically-described tools instead of N endpoint tools."""
        cid = self.metadata.id
        name = self.metadata.name
        tag_list = ", ".join(self._tags[:15]) or "general"

        return [
            {
                "name": f"search_{cid}_api",
                "description": (
                    f"Search the {name} API to discover available endpoints. "
                    f"Returns matching operations with path, method, parameters "
                    f"(with types), and descriptions.\n\n"
                    f"This API has {len(self._parsed_spec.operations)} operations "
                    f"organized by tags: {tag_list}.\n\n"
                    f"Use this before execute to find the right endpoint and parameters."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of what you want to do",
                        }
                    },
                    "required": ["query"],
                },
            },
            self._build_execute_tool_definition(),
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        """
        Route tool calls. Extra kwargs: tenant_id, user_id, user_session_id, credentials.
        """
        cid = self.metadata.id
        if tool_name == f"search_{cid}_api":
            return self._search_operations(arguments.get("query", ""))
        elif tool_name == f"execute_{cid}_api":
            return await self._execute_in_sandbox(
                code=arguments.get("code", ""),
                tenant_id=kwargs.get("tenant_id", ""),
                user_id=kwargs.get("user_id", ""),
                user_session_id=kwargs.get("user_session_id", ""),
                credentials=kwargs.get("credentials", {}),
            )
        raise ValueError(f"Unknown tool: {tool_name}")

    # ── Tool description builder ─────────────────────────────

    def _build_execute_tool_definition(self) -> dict[str, Any]:
        """
        Build a dynamically-generated execute tool description.

        Includes base URL, env vars, auth pattern, and a concrete example
        so the agent can write correct code on the first attempt.
        """
        cid = self.metadata.id
        name = self.metadata.name
        auth_type = self._auth_config.get("type", "bearer") if self._auth_config else "bearer"

        if auth_type in ("bearer", "oauth2"):
            auth_example = (
                '    headers={"Authorization": f"Bearer {os.environ[\'CONNECTOR_API_KEY\']}"}'
            )
        elif auth_type == "basic":
            auth_example = (
                '    auth=(os.environ["CONNECTOR_USERNAME"], os.environ["CONNECTOR_PASSWORD"])'
            )
        else:
            auth_example = (
                '    headers={"Authorization": f"Bearer {os.environ[\'CONNECTOR_API_KEY\']}"}'
            )

        # Pick a representative endpoint for the example
        sample_path = "/v1/example"
        if self._parsed_spec.operations:
            get_ops = [
                op for op in self._parsed_spec.operations
                if op.method.upper() == "GET"
            ]
            sample_op = get_ops[0] if get_ops else self._parsed_spec.operations[0]
            sample_path = sample_op.path

        return {
            "name": f"execute_{cid}_api",
            "description": (
                f"Execute Python code against the {name} API inside a secure E2B sandbox.\n\n"
                f"Base URL: {self._base_url}\n"
                f"Auth type: {auth_type}\n"
                f"Available env vars:\n"
                f"  - CONNECTOR_API_KEY — API key or Bearer token\n"
                f"  - CONNECTOR_BASE_URL — {self._base_url}\n"
                f"  - CONNECTOR_NAME — {cid}\n\n"
                f"httpx is pre-installed. Print results to stdout (stdout is returned to you).\n"
                f"For large responses, print only the fields you need.\n\n"
                f"Example:\n"
                f"```python\n"
                f"import httpx, os, json\n"
                f"r = httpx.get(\n"
                f'    f"{{os.environ[\'CONNECTOR_BASE_URL\']}}{sample_path}",\n'
                f"{auth_example},\n"
                f'    params={{"limit": 10}}\n'
                f")\n"
                f"print(json.dumps(r.json(), indent=2))\n"
                f"```"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute. Use httpx for HTTP calls. Print results to stdout.",
                    }
                },
                "required": ["code"],
            },
        }

    # ── Search ───────────────────────────────────────────────

    def _search_operations(self, query: str) -> list[dict[str, Any]]:
        """Keyword + fuzzy search over OpenAPI operations."""
        if not query.strip():
            return [
                self._format_operation(op)
                for op in self._parsed_spec.operations[:8]
            ]

        query_lower = query.lower()
        scored: list[tuple[int, dict]] = []

        for op in self._parsed_spec.operations:
            score = 0
            summary = (op.summary or "").lower()
            description = (op.description or "").lower()
            path = op.path.lower()
            tags = " ".join(op.tags or []).lower()

            if query_lower in summary:
                score += 10
            if query_lower in description:
                score += 5
            for word in query_lower.split():
                if word in summary:
                    score += 3
                if word in description:
                    score += 2
                if word in path:
                    score += 1
                if word in tags:
                    score += 2

            if score > 0:
                scored.append((score, self._format_operation(op)))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:8]]

    @staticmethod
    def _format_operation(op: Any) -> dict[str, Any]:
        """Format an operation with full parameter types."""
        return {
            "operation_id": op.operation_id,
            "method": op.method.upper(),
            "path": op.path,
            "description": op.summary or op.description or f"{op.method.upper()} {op.path}",
            "tags": op.tags or [],
            "parameters": [
                {
                    "name": p.get("name"),
                    "type": p.get("schema", {}).get("type", "string"),
                    "in": p.get("in"),
                    "required": p.get("required", False),
                    "description": p.get("description", ""),
                }
                for p in (op.parameters or [])[:12]
            ],
        }

    # ── Sandbox execution ────────────────────────────────────

    async def _execute_in_sandbox(
        self,
        code: str,
        tenant_id: str,
        user_id: str,
        user_session_id: str,
        credentials: dict[str, Any],
    ) -> str:
        """Run code in E2B sandbox with credentials injected at creation."""
        from app.services.sandbox_executor import get_sandbox_executor, resolve_e2b_key

        executor = get_sandbox_executor()

        # Per-tenant, per-user session — never shared across users
        session_id = f"connector:{self.metadata.id}:{tenant_id}:{user_session_id}"

        # Resolve E2B platform key
        e2b_key = await resolve_e2b_key(tenant_id=tenant_id, user_id=user_id)

        # Build env vars for sandbox creation (no shell parsing)
        env_vars = self._build_env_vars(credentials)

        handle = await executor.get_or_create(
            session_id=session_id,
            worker_id="default",
            api_key=e2b_key,
            env_vars=env_vars,
        )

        try:
            result = await handle.execute_code(code)
            if result.success:
                return result.stdout or "(no output)"
            return self._format_error(result)
        finally:
            await handle.extend_timeout()

    def _build_env_vars(self, credentials: dict[str, Any]) -> dict[str, str]:
        """Build env var dict for sandbox creation. No shell parsing."""
        env_vars = {
            "CONNECTOR_BASE_URL": self._base_url,
            "CONNECTOR_NAME": self.metadata.id,
        }
        if token := (
            credentials.get("token")
            or credentials.get("api_key")
            or credentials.get("access_token")
        ):
            env_vars["CONNECTOR_API_KEY"] = token
        if username := credentials.get("username"):
            env_vars["CONNECTOR_USERNAME"] = username
        if password := credentials.get("password"):
            env_vars["CONNECTOR_PASSWORD"] = password
        return env_vars

    @staticmethod
    def _format_error(result: Any) -> str:
        """Return structured errors so agent can distinguish failure types."""
        stderr = result.stderr or ""
        error = result.error or ""
        combined = f"{stderr}\n{error}".strip()

        if "401" in combined or "Unauthorized" in combined:
            return f"AUTH_ERROR: API returned 401 Unauthorized. Check that CONNECTOR_API_KEY is valid.\n{combined}"
        if "403" in combined or "Forbidden" in combined:
            return f"AUTH_ERROR: API returned 403 Forbidden. The API key may lack required permissions.\n{combined}"
        if "TimeoutError" in combined or "timed out" in combined.lower():
            return f"TIMEOUT_ERROR: Request timed out. Try reducing the data requested.\n{combined}"
        if "SyntaxError" in combined or "IndentationError" in combined:
            return f"CODE_ERROR: Python syntax error in your code. Fix and retry.\n{combined}"
        if "ModuleNotFoundError" in combined:
            return f"IMPORT_ERROR: Missing module. httpx is pre-installed; other packages may need `pip install`.\n{combined}"
        return f"EXECUTION_ERROR: {combined}"
