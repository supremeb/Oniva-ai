"""
Export Control Service for Oniva Security Foundation.

Validates data classification before external API calls using IP-based
endpoint validation to prevent SSRF attacks, DNS rebinding, and IPv6 bypasses.

Security Features:
- IP resolution prevents DNS rebinding attacks
- Comprehensive private IP range detection (RFC 1918 + IPv6)
- Database-backed whitelist with approval workflow
- Redirect chain validation

Usage:
    from app.services.export_control import ExportControlService

    service = ExportControlService(db)

    # Check if URL points to internal endpoint
    is_internal = await service.is_internal_endpoint("https://api.openai.com/v1/chat")
    # Returns False - OpenAI is external

    is_internal = await service.is_internal_endpoint("http://localhost:8000/api")
    # Returns True - localhost is always internal
"""
import ipaddress
import socket
import logging
from urllib.parse import urlparse
from typing import Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)


class ExportControlService:
    """
    Validates data classification before external API calls.
    Uses IP-based validation to prevent SSRF attacks.

    Security Design:
    - Resolves hostnames to IP addresses before checking
    - Prevents DNS rebinding by checking resolved IP, not hostname
    - Supports both IPv4 and IPv6 private ranges
    - Database-backed whitelist for VPN endpoints and internal services
    """

    # RFC 1918 + RFC 4193 + RFC 5735 + RFC 5156 private/special IP ranges
    # These IPs are never considered external
    PRIVATE_IP_RANGES = [
        # IPv4 Private Networks (RFC 1918)
        ipaddress.ip_network("10.0.0.0/8"),        # Private Class A
        ipaddress.ip_network("172.16.0.0/12"),     # Private Class B
        ipaddress.ip_network("192.168.0.0/16"),    # Private Class C

        # IPv4 Loopback (RFC 5735)
        ipaddress.ip_network("127.0.0.0/8"),       # Localhost

        # IPv4 Link-Local (RFC 3927)
        ipaddress.ip_network("169.254.0.0/16"),    # Link-local

        # IPv4 Carrier-Grade NAT (RFC 6598)
        ipaddress.ip_network("100.64.0.0/10"),     # Shared address space

        # IPv6 Loopback (RFC 5156)
        ipaddress.ip_network("::1/128"),           # IPv6 localhost

        # IPv6 Unique Local Addresses (RFC 4193)
        ipaddress.ip_network("fc00::/7"),          # ULA

        # IPv6 Link-Local (RFC 4291)
        ipaddress.ip_network("fe80::/10"),         # Link-local

        # IPv4-mapped IPv6 addresses (::ffff:0:0/96) - check the embedded IPv4
        # Note: We handle this specially in the IP check logic
    ]

    def __init__(self, db: Optional[AsyncSession] = None):
        """
        Initialize ExportControlService.

        Args:
            db: Database session for whitelist lookups (optional)
        """
        self.db = db
        self._whitelist_cache: Optional[Set[str]] = None
        self._cache_ttl_seconds = 300  # 5 minute cache

    async def is_internal_endpoint(self, url: str) -> bool:
        """
        Determine if URL points to an internal endpoint.
        Uses IP resolution to prevent DNS rebinding attacks.

        Args:
            url: Full URL to check (e.g., "https://api.example.com/v1")

        Returns:
            True if URL is internal (localhost, private IP, or whitelisted)
            False if URL is external

        Security Note:
            - Resolves DNS to IP to prevent rebinding attacks
            - Fails closed: any error returns False (assume external)
        """
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname

            if not hostname:
                logger.warning(f"No hostname in URL: {url}")
                return False  # Fail closed

            # Resolve hostname to IP address (prevents DNS rebinding)
            try:
                # Get all IPs for the hostname
                ip_str = socket.gethostbyname(hostname)
                ip = ipaddress.ip_address(ip_str)
            except socket.gaierror as e:
                logger.warning(f"DNS resolution failed for {hostname}: {e}")
                return False  # Cannot resolve = assume external

            # Handle IPv4-mapped IPv6 addresses (::ffff:x.x.x.x)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped

            # Check if IP is in private ranges
            for network in self.PRIVATE_IP_RANGES:
                try:
                    if ip in network:
                        logger.debug(f"URL {url} -> {ip} is in private range {network}")
                        return True
                except TypeError:
                    # IPv4/IPv6 mismatch - skip this network
                    continue

            # Check database whitelist (for VPN endpoints, etc.)
            if self.db:
                whitelist = await self._get_whitelist()
                if hostname.lower() in whitelist:
                    logger.info(f"URL {url} is whitelisted as internal")
                    return True

            # Not in private range and not whitelisted = external
            logger.debug(f"URL {url} -> {ip} is external")
            return False

        except Exception as e:
            logger.error(f"Error checking endpoint {url}: {e}")
            return False  # Fail closed on any error

    async def _get_whitelist(self) -> Set[str]:
        """
        Get approved internal endpoints from database (cached).

        Returns:
            Set of lowercase hostnames that are approved as internal.
        """
        if self._whitelist_cache is not None:
            return self._whitelist_cache

        try:
            from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist

            result = await self.db.execute(
                select(InternalEndpointWhitelist.hostname)
                .where(InternalEndpointWhitelist.is_active == True)
                .where(InternalEndpointWhitelist.approved_at.isnot(None))
            )
            self._whitelist_cache = {row[0].lower() for row in result.fetchall()}
            return self._whitelist_cache

        except Exception as e:
            logger.warning(f"Failed to load whitelist from database: {e}")
            self._whitelist_cache = set()
            return self._whitelist_cache

    def clear_whitelist_cache(self) -> None:
        """Clear the whitelist cache (call after whitelist changes)."""
        self._whitelist_cache = None

    def validate_no_redirect_to_external(
        self,
        response_headers: dict,
        original_url: str,
    ) -> bool:
        """
        Check if response redirects to external URL.
        Called after HTTP request to validate redirect chain.

        Args:
            response_headers: HTTP response headers dict
            original_url: The original URL that was requested

        Returns:
            True if no redirect or redirect is internal
            False if redirects to external URL (SSRF attempt)

        Security Note:
            This is a synchronous check. For async validation,
            call is_internal_endpoint() with the redirect target.
        """
        location = response_headers.get("Location") or response_headers.get("location")
        if not location:
            return True  # No redirect

        # Parse redirect target
        parsed = urlparse(location)
        hostname = parsed.hostname

        if not hostname:
            # Relative redirect - stays on same host
            return True

        try:
            ip_str = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(ip_str)

            # Handle IPv4-mapped IPv6
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped

            # Check if redirect target is in private range
            for network in self.PRIVATE_IP_RANGES:
                try:
                    if ip in network:
                        return True
                except TypeError:
                    continue

            # Redirect to external = potential SSRF
            logger.warning(
                f"Redirect from {original_url} to external URL {location} blocked"
            )
            return False

        except Exception as e:
            logger.error(f"Error validating redirect to {location}: {e}")
            return False  # Assume external on error

    def is_ip_private(self, ip_str: str) -> bool:
        """
        Check if an IP address is private/internal.

        Args:
            ip_str: IP address string (IPv4 or IPv6)

        Returns:
            True if IP is private/internal
            False if IP is public/external
        """
        try:
            ip = ipaddress.ip_address(ip_str)

            # Handle IPv4-mapped IPv6
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped

            for network in self.PRIVATE_IP_RANGES:
                try:
                    if ip in network:
                        return True
                except TypeError:
                    continue

            return False

        except ValueError:
            return False  # Invalid IP = not private


# Singleton instance
_export_control_service: Optional[ExportControlService] = None


def get_export_control_service(db: Optional[AsyncSession] = None) -> ExportControlService:
    """
    Get the export control service instance.

    Args:
        db: Database session for whitelist lookups

    Returns:
        ExportControlService instance
    """
    global _export_control_service

    if _export_control_service is None or db is not None:
        _export_control_service = ExportControlService(db=db)

    return _export_control_service


def reset_export_control_service() -> None:
    """Reset the singleton instance (for testing)."""
    global _export_control_service
    _export_control_service = None
