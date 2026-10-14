"""
Shared URL and path validation for SSRF protection.

Used by browse_url, browser_navigate, and any skill that fetches external URLs.
"""

import asyncio
import ipaddress
import os
import socket
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

# Blocked hostnames (cloud metadata, localhost variants)
_BLOCKED_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",
    "[fd00:ec2::254]",
    "fd00:ec2::254",
})

# Ranges that are not publicly routable even where ``ipaddress`` says otherwise.
_EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.64.0.0/10",   # carrier-grade NAT
        "192.0.0.0/24",    # IETF protocol assignments
        "198.18.0.0/15",   # benchmarking
        "224.0.0.0/4",     # multicast
        "240.0.0.0/4",     # reserved
        "fc00::/7",        # unique local
        "fe80::/10",       # link-local
        "fec0::/10",       # deprecated site-local
        "ff00::/8",        # multicast
    )
)

MAX_REDIRECTS = 5


class SecurityError(Exception):
    """Raised when a security check fails."""


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 address smuggled inside an IPv6 one (mapped, NAT64, 6to4, compat)."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    packed = ip.packed
    if packed[:12] == bytes.fromhex("0064ff9b0000000000000000"):  # 64:ff9b::/96
        return ipaddress.IPv4Address(packed[12:])
    if packed[:12] == bytes(12):  # ::a.b.c.d (IPv4-compatible)
        return ipaddress.IPv4Address(packed[12:])
    return None


def is_ip_blocked(ip_str: str) -> bool:
    """True if the address is not safe to connect to (non-public or unparseable)."""
    try:
        ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_v4(ip)
        if inner is not None:
            return is_ip_blocked(str(inner))
    if not ip.is_global:
        return True
    return any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version)


def _check_url_shape(url: str, schemes: tuple[str, ...] = ("http", "https")) -> tuple[Any, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        raise SecurityError("Invalid URL")
    if parsed.scheme not in schemes:
        raise SecurityError(f"Scheme '{parsed.scheme}' not allowed (only {'/'.join(schemes)})")
    hostname = parsed.hostname
    if not hostname:
        raise SecurityError("No hostname in URL")
    if hostname.lower().rstrip(".") in _BLOCKED_HOSTS:
        raise SecurityError(f"Blocked host: {hostname}")
    return parsed, hostname


async def resolve_hostname(hostname: str) -> list[str]:
    """Resolve a hostname without blocking the event loop."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        raise SecurityError(f"Cannot resolve hostname: {hostname} ({e})")
    return list(dict.fromkeys(info[4][0] for info in infos))


Resolver = Callable[[str], Awaitable[list[str]]]


async def resolve_public_ips(
    url: str,
    resolver: Resolver | None = None,
    schemes: tuple[str, ...] = ("http", "https"),
) -> list[str]:
    """Validate a URL and return the public IPs its host resolves to.

    Raises SecurityError if the URL is malformed or any resolved IP is non-public.
    """
    _, hostname = _check_url_shape(url, schemes)
    try:
        ipaddress.ip_address(hostname)
        ips = [hostname]
    except ValueError:
        ips = await (resolver or resolve_hostname)(hostname)
    if not ips:
        raise SecurityError(f"No IP addresses found for {hostname}")
    for ip in ips:
        if is_ip_blocked(ip):
            raise SecurityError(f"Blocked: {hostname} resolved to non-public IP {ip}")
    return ips


def is_url_safe(url: str) -> str | None:
    """Validate URL is safe to fetch (synchronous check).

    Returns error message string if blocked, or None if safe. Callers that
    actually fetch should use ``safe_request`` so redirects are validated and
    the connection is pinned to the validated IP.
    """
    try:
        parsed, hostname = _check_url_shape(url)
    except SecurityError as e:
        return str(e)
    try:
        ipaddress.ip_address(hostname)
        ips = [hostname]
    except ValueError:
        try:
            ips = list(
                dict.fromkeys(
                    i[4][0]
                    for i in socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
                )
            )
        except socket.gaierror:
            return f"Cannot resolve hostname: {hostname}"
    for ip in ips:
        if is_ip_blocked(ip):
            return f"Blocked: resolved to non-public IP {ip}"
    return None


def _pin(url: str, ip: str) -> tuple[str, str]:
    """Rewrite url to connect to ``ip``; returns (pinned_url, original_host)."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    ip_host = f"[{ip}]" if ":" in ip else ip
    netloc = ip_host + (f":{parsed.port}" if parsed.port else "")
    return urlunparse(parsed._replace(netloc=netloc)), host


async def safe_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    resolver: Resolver | None = None,
    schemes: tuple[str, ...] = ("http", "https"),
    max_redirects: int = MAX_REDIRECTS,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request with SSRF protection.

    - every hop (including redirects) is validated;
    - the connection is pinned to the validated IP (Host header and TLS SNI keep
      the original hostname), defeating DNS rebinding;
    - redirects are followed manually; credentials are dropped when a redirect
      leaves the original origin.
    The client must be created with ``follow_redirects=False``.
    """
    headers = dict(kwargs.pop("headers", None) or {})
    origin = urlparse(url)
    origin_key = (origin.scheme, origin.hostname, origin.port)
    current_url, current_method = url, method.upper()

    for _hop in range(max_redirects + 1):
        ips = await resolve_public_ips(current_url, resolver, schemes)
        pinned_url, host = _pin(current_url, ips[0])
        parsed = urlparse(current_url)
        send_headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        send_headers["Host"] = parsed.netloc.rsplit("@", 1)[-1]
        extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
        response = await client.request(
            current_method,
            pinned_url,
            headers=send_headers,
            extensions=extensions,
            **kwargs,
        )
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("location")
        if not location:
            return response
        next_url = urljoin(current_url, location)
        nxt = urlparse(next_url)
        if (nxt.scheme, nxt.hostname, nxt.port) != origin_key:
            headers = {
                k: v for k, v in headers.items()
                if k.lower() not in ("authorization", "cookie", "proxy-authorization")
            }
            kwargs.pop("auth", None)
        # Query params only apply to the first hop; the redirect target carries its own.
        kwargs.pop("params", None)
        if response.status_code == 303 or (
            response.status_code in (301, 302) and current_method not in ("GET", "HEAD")
        ):
            current_method = "GET" if current_method != "HEAD" else "HEAD"
            for k in ("json", "data", "content", "files"):
                kwargs.pop(k, None)
        current_url = next_url

    raise SecurityError(f"Too many redirects (>{max_redirects})")


def is_sandbox_path_safe(path: str) -> str | None:
    """Validate a file path is safe for sandbox download.

    Rejects path traversal, absolute paths outside /workspace,
    null bytes, and excessively long paths.
    Returns error message string if blocked, or None if safe.
    """
    if "\x00" in path:
        return "Null bytes not allowed in path"

    if len(path) > 255:
        return "Path too long (max 255 characters)"

    normalized = os.path.normpath(path)

    if ".." in normalized.split(os.sep):
        return f"Path traversal detected: {path}"

    if os.path.isabs(normalized) and not normalized.startswith("/workspace"):
        return f"Path must be under /workspace: {path}"

    return None
