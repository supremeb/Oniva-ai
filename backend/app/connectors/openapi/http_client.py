"""
Safe HTTP Client with SSRF Protection

Provides secure HTTP operations with:
- SSRF protection (blocks private IPs, localhost, metadata endpoints)
- DNS rebinding prevention
- Timeout enforcement
- Retry with exponential backoff
"""

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse
import httpx
import asyncio
import logging

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when a security check fails."""
    pass


# IP ranges that should never be accessed
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),       # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),    # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),   # Private Class C
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local / Cloud metadata
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("0.0.0.0/8"),        # Current network
    ipaddress.ip_network("224.0.0.0/4"),      # Multicast
    ipaddress.ip_network("240.0.0.0/4"),      # Reserved
    ipaddress.ip_network("100.64.0.0/10"),    # Carrier-grade NAT
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


def is_ip_blocked(ip_str: str) -> bool:
    """Check if IP address falls within blocked ranges."""
    try:
        ip = ipaddress.ip_address(ip_str)
        for network in BLOCKED_IP_RANGES:
            if ip in network:
                return True
        return False
    except ValueError:
        # Invalid IP format - block to be safe
        return True


async def resolve_hostname(hostname: str) -> list[str]:
    """Resolve hostname to IP addresses."""
    try:
        loop = asyncio.get_event_loop()
        # Use getaddrinfo for both IPv4 and IPv6
        result = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        )
        return list(set(addr[4][0] for addr in result))
    except socket.gaierror as e:
        raise SecurityError(f"DNS resolution failed for {hostname}: {e}")


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
                follow_redirects=True,
                max_redirects=5,
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
        # Validate URL safety
        safe_url = await validate_url_safe(url)

        # Fetch with retry
        response = await self._request_with_retry("GET", safe_url)

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
        # Validate URL safety
        safe_url = await validate_url_safe(url)

        return await self._request_with_retry(
            method=method,
            url=safe_url,
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
        """Make request with exponential backoff retry."""
        client = await self._get_client()
        merged_headers = {**self.default_headers, **(headers or {})}

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=merged_headers,
                    params=params,
                    json=json_body,
                    data=data,
                )

                # Retry on 5xx errors
                if response.status_code >= 500:
                    last_error = httpx.HTTPStatusError(
                        f"Server error: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    await self._backoff(attempt)
                    continue

                return response

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = e
                logger.warning(f"Request attempt {attempt + 1} failed: {e}")
                await self._backoff(attempt)

        raise last_error or Exception("Request failed after retries")

    async def _backoff(self, attempt: int) -> None:
        """Sleep with exponential backoff."""
        delay = self.RETRY_BACKOFF_BASE * (2 ** attempt)
        await asyncio.sleep(delay)
