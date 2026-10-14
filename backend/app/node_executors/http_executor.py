"""
HTTP Node Executor
Makes HTTP requests to external APIs

SECURITY: Includes SSRF protection via ExportControlService.
All URLs are validated before requests are made to prevent
access to internal services.
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.export_control import ExportControlService
import time
import httpx
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class HTTPExecutor(BaseNodeExecutor):
    """Executor for HTTP node - makes external API calls"""

    node_type = "http"

    # HTTP node makes arbitrary external API calls
    makes_external_calls = True

    # SECURITY: no provider-key injection. Credentials are sent only when the
    # workflow explicitly configures them in headers; matching substrings of the
    # URL (e.g. "api.exa.ai" in a path) must never attach server/user keys.

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            # Support both flat (data.url) and nested (data.config.url) formats
            config = node_data.get("config", {})

            # Get HTTP configuration — prefer config nested, fallback to flat
            method = (config.get("method") or node_data.get("method", "GET")).upper()
            url = config.get("url") or node_data.get("url", "")
            headers = config.get("headers") or node_data.get("headers", {})
            body = config.get("body") or node_data.get("body", "")
            timeout = config.get("timeout") or node_data.get("timeout", 30)
            params = config.get("params", {})

            # Substitute variables in params and append to URL
            if params and isinstance(params, dict):
                resolved_params = {
                    k: self.substitute_variables(str(v), context.variables, context.inputs)
                    for k, v in params.items()
                    if v is not None
                }
                if resolved_params:
                    from urllib.parse import urlencode, urlparse, urlunparse, parse_qs
                    parsed = urlparse(url)
                    existing = parse_qs(parsed.query)
                    existing.update({k: [v] for k, v in resolved_params.items()})
                    new_query = urlencode({k: v[0] for k, v in existing.items()})
                    url = urlunparse(parsed._replace(query=new_query))

            # Substitute variables in URL, headers, and body
            url = self.substitute_variables(url, context.variables, context.inputs)
            body = self.substitute_variables(body, context.variables, context.inputs)

            # Substitute variables in headers
            if isinstance(headers, dict):
                headers = {
                    k: self.substitute_variables(str(v), context.variables, context.inputs)
                    for k, v in headers.items()
                }

            # Validate URL
            if not url:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error="URL is required",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # SECURITY: SSRF Protection - validate URL is not internal
            # Allow specific internal URLs for dev/test (e.g., synthetic bank API)
            import os
            allowed_prefixes = [
                p.strip() for p in os.getenv("HTTP_NODE_ALLOWED_URLS", "").split(",") if p.strip()
            ]
            is_allowed = any(url.startswith(prefix) for prefix in allowed_prefixes)

            if not is_allowed:
                export_control = ExportControlService(db)
                is_internal = await export_control.is_internal_endpoint(url)

                if is_internal:
                    logger.warning(f"SSRF attempt blocked: {url[:100]} (user={context.user_id})")
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error="Access to internal endpoints is not allowed. Only external APIs are permitted.",
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

            # Make HTTP request
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Determine content type
                content_type = headers.get("Content-Type", "application/json")
                
                if method == "GET":
                    response = await client.get(url, headers=headers, follow_redirects=True)
                elif method == "POST":
                    if "json" in content_type.lower():
                        try:
                            json_body = json.loads(body) if body else {}
                            response = await client.post(url, headers=headers, json=json_body)
                        except json.JSONDecodeError:
                            response = await client.post(url, headers=headers, content=body)
                    else:
                        response = await client.post(url, headers=headers, content=body)
                elif method == "PUT":
                    if "json" in content_type.lower():
                        try:
                            json_body = json.loads(body) if body else {}
                            response = await client.put(url, headers=headers, json=json_body)
                        except json.JSONDecodeError:
                            response = await client.put(url, headers=headers, content=body)
                    else:
                        response = await client.put(url, headers=headers, content=body)
                elif method == "DELETE":
                    response = await client.delete(url, headers=headers)
                elif method == "PATCH":
                    if "json" in content_type.lower():
                        try:
                            json_body = json.loads(body) if body else {}
                            response = await client.patch(url, headers=headers, json=json_body)
                        except json.JSONDecodeError:
                            response = await client.patch(url, headers=headers, content=body)
                    else:
                        response = await client.patch(url, headers=headers, content=body)
                else:
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error=f"Unsupported HTTP method: {method}",
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            # Parse response
            try:
                response_body = response.json()
            except Exception:
                response_body = response.text
            
            output = {
                "status": response.status_code,
                "body": response_body,
                "headers": dict(response.headers),
            }
            
            # Check for error status codes
            if response.status_code >= 400:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    output=output,
                    error=f"HTTP {response.status_code}: {response.reason_phrase}",
                    elapsed_ms=elapsed_ms
                )
            
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output,
                elapsed_ms=elapsed_ms
            )
            
        except httpx.TimeoutException:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=f"Request timed out after {timeout}s",
                elapsed_ms=elapsed_ms
            )
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )
