"""
Shared URL and path validation for SSRF protection.

Used by browse_url, browser_navigate, and any skill that fetches external URLs.
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Blocked hostnames (cloud metadata, localhost variants)
_BLOCKED_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",
    "[fd00:ec2::254]",
})


def is_url_safe(url: str) -> str | None:
    """Validate URL is safe to fetch.

    Returns error message string if blocked, or None if safe.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL"

    if parsed.scheme not in ("http", "https"):
        return f"Scheme '{parsed.scheme}' not allowed (only http/https)"

    hostname = parsed.hostname
    if not hostname:
        return "No hostname in URL"

    # Block known metadata endpoints
    if hostname.lower() in _BLOCKED_HOSTS:
        return f"Blocked host: {hostname}"

    # Resolve hostname and check IP
    try:
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        for _family, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return f"Blocked: resolved to non-public IP {ip}"
    except socket.gaierror:
        return f"Cannot resolve hostname: {hostname}"

    return None


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
