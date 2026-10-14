"""
Safe HTTP Client with SSRF Protection

Provides secure HTTP operations with:
- SSRF protection (blocks private IPs, localhost, metadata endpoints)
- DNS rebinding prevention
- Timeout enforcement
- Retry with exponential backoff
"""

import asyncio
import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.security import url_validator
from app.security.url_validator import SecurityError, is_ip_blocked, safe_request

logger = logging.getLogger(__name__)

# Only these methods are safe to repeat automatically.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def resolve_hostname(hostname: str) -> list[str]:
    """Resolve hostname to IP addresses (async, non-blocking)."""
    return await url_validator.resolve_hostname(hostname)


async def validate_url_safe(url: str) -> str:
    """
    Validate URL is safe to fetch.

    Checks:
    1. HTTPS only (for production URLs)
    2. Resolved IPs not in blocked ranges
    3. No DNS rebinding potential

    Returns the validated URL.
    """
    parsed = urlparse(url)

    # Allow HTTP only for localhost in development
    if parsed.scheme not in ("https", "http"):
        raise SecurityError(f"Invalid URL scheme: {parsed.scheme}. Only HTTPS allowed.")

    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise SecurityError("HTTP only allowed for localhost. Use HTTPS for external URLs.")

    hostname = parsed.hostname
    if not hostname:
        raise SecurityError("Invalid URL: no hostname")

    # Check if hostname is already an IP
    try:
        ip = ipaddress.ip_address(hostname)
        if is_ip_blocked(str(ip)):
            raise SecurityError(f"Blocked IP address: {hostname}")
        return url
    except ValueError:
        # Not an IP, resolve hostname
        pass

    # Resolve DNS and check all IPs
    resolved_ips = await resolve_hostname(hostname)
    if not resolved_ips:
        raise SecurityError(f"No IP addresses found for {hostname}")

    for ip in resolved_ips:
        if is_ip_blocked(ip):
            raise SecurityError(f"URL resolves to blocked IP: {hostname} -> {ip}")

    return url


class SafeHTTPClient:
    """
    HTTP client with security protections and retry logic.

    Features:
    - SSRF protection on all requests
    - Configurable timeouts
    - Exponential backoff retry
    - Response size limits
    """

    DEFAULT_TIMEOUT = 30.0
    DEFAULT_CONNECT_TIMEOUT = 5.0
    MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 1.0

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        headers: dict[str, str] | None = None,
    ):
        self.timeout = httpx.Timeout(
            timeout=timeout,
            connect=connect_timeout,
        )
        self.max_retries = max_retries
        self.default_headers = headers or {}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,  # redirects are followed (and validated) by safe_request
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def fetch_spec(self, url: str) -> dict[str, Any]:
        """
        Fetch OpenAPI spec from URL with SSRF protection.

        Returns parsed JSON/YAML spec.
        """
        # Every hop is validated and pinned inside _request_with_retry
        response = await self._request_with_retry("GET", url)

        # Parse response
        content_type = response.headers.get("content-type", "")
        text = response.text

        if len(text) > self.MAX_RESPONSE_SIZE:
            raise SecurityError(f"Response exceeds max size of {self.MAX_RESPONSE_SIZE} bytes")

        import json
        import yaml

        if "yaml" in content_type or url.endswith((".yaml", ".yml")):
            return yaml.safe_load(text)
        else:
            return json.loads(text)

    async def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """
        Make HTTP request with SSRF protection.

        For API calls to external services.
        """
        return await self._request_with_retry(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json_body=json_body,
            data=data,
        )

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make a validated request; only idempotent methods are retried."""
        client = await self._get_client()
        method = method.upper()
        attempts = self.max_retries if method in IDEMPOTENT_METHODS else 1
        merged_headers = {**self.default_headers, **(headers or {})}

        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = await safe_request(
                    client,
                    method,
                    url,
                    resolver=resolve_hostname,
                    schemes=("https",),
                    headers=merged_headers,
                    params=params,
                    json=json_body,
                    data=data,
                )

                # Retry on 5xx errors (idempotent methods only)
                if response.status_code >= 500 and attempts > 1:
                    last_error = httpx.HTTPStatusError(
                        f"Server error: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    await self._backoff(attempt)
                    continue

                return response

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempts == 1:
                    raise
                last_error = e
                logger.warning(f"Request attempt {attempt + 1} failed: {e}")
                await self._backoff(attempt)

        raise last_error or Exception("Request failed after retries")

    async def _backoff(self, attempt: int) -> None:
        """Sleep with exponential backoff."""
        delay = self.RETRY_BACKOFF_BASE * (2 ** attempt)
        await asyncio.sleep(delay)
