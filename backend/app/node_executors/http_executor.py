"""
HTTP Node Executor
Makes HTTP requests to external APIs

SECURITY: Includes SSRF protection via ExportControlService.
All URLs are validated before requests are made to prevent
access to internal services.
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import get_settings
from app.models.api_key import APIKey
from app.core.encryption import get_encryption_service
from app.services.api_key_cache import get_api_key_cache
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

    # Mapping of known API domains to their provider names and header configuration
    # Provider names match the API key settings in the database
    KNOWN_API_PROVIDERS = {
        "api.exa.ai": {
            "provider": "exa",
            "header": "x-api-key",
            "env_var": "EXA_API_KEY",
        },
        "api.perplexity.ai": {
            "provider": "perplexity",
            "header": "Authorization",
            "env_var": "PERPLEXITY_API_KEY",
            "format": "Bearer {key}",
        },
        "api.tavily.com": {
            "provider": "tavily",
            "header": "Authorization",
            "env_var": "TAVILY_API_KEY",
            "format": "Bearer {key}",
        },
    }

    async def _get_api_key(
        self,
        provider: str,
        db: AsyncSession,
        user_id: str
    ) -> Optional[str]:
        """Get API key for provider from cache, database, or environment.

        Priority:
        1. Redis cache (fastest)
        2. User's database-stored keys (personalized)
        3. Server environment variables (fallback)
        """
        cache = get_api_key_cache()

        # Check cache first
        cached_key = await cache.get(user_id, provider)
        if cached_key:
            logger.debug(f"Using cached API key for provider={provider}")
            return cached_key

        # Try database - get user's default key for this provider
        try:
            stmt = (
                select(APIKey)
                .where(APIKey.user_id == user_id)
                .where(APIKey.provider == provider)
                .where(APIKey.is_active == True)
                .where(APIKey.is_default == True)
            )
            result = await db.execute(stmt)
            api_key_record = result.scalar_one_or_none()

            if api_key_record:
                encryption = get_encryption_service()
                decrypted_key = encryption.decrypt(api_key_record.encrypted_key)
                # Cache for future requests
                await cache.set(user_id, provider, decrypted_key)
                logger.debug(f"Using user-configured API key for provider={provider}")
                return decrypted_key
        except Exception as e:
            logger.warning(f"Error fetching API key from database: {e}")

        # Fall back to environment
        settings = get_settings()
        env_mapping = {
            "exa": settings.EXA_API_KEY,
            "perplexity": settings.PERPLEXITY_API_KEY,
            "tavily": getattr(settings, 'TAVILY_API_KEY', None),
        }
        env_key = env_mapping.get(provider)
        if env_key:
            logger.debug(f"Using environment API key for provider={provider}")
        return env_key

    async def _inject_api_key(
        self,
        url: str,
        headers: Dict[str, str],
        db: AsyncSession,
        user_id: str
    ) -> Dict[str, str]:
        """Auto-inject API keys for known services.

        Looks up user-configured keys first, then falls back to server environment.
        This allows workflows to use HTTP nodes without requiring users to
        pass API keys in the workflow definition.
        """
        for domain, config in self.KNOWN_API_PROVIDERS.items():
            if domain in url:
                header_name = config["header"]
                provider = config["provider"]

                # Get API key (user's key or fallback to env)
                api_key = await self._get_api_key(provider, db, user_id)

                if api_key:
                    # Check if header needs formatting (e.g., Bearer token)
                    if "format" in config:
                        api_key = config["format"].format(key=api_key)

                    # Only set if not already set or if placeholder wasn't replaced
                    current_value = headers.get(header_name, "")
                    if not current_value or "{{" in current_value:
                        headers[header_name] = api_key
                        logger.debug(f"Injected API key for {domain}")
                else:
                    logger.warning(f"No API key found for {provider} - request may fail")
                break

        return headers

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

            # Auto-inject API keys for known services (user-configured or env fallback)
            headers = await self._inject_api_key(url, headers, db, context.user_id)
            
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
