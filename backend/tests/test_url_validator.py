"""
Unit tests for shared URL and path validation.

Tests cover:
- SSRF protection (blocked hosts, private IPs, scheme validation)
- Path traversal rejection
- Valid URL/path acceptance
"""

import socket
from unittest.mock import patch

from app.security.url_validator import is_url_safe, is_sandbox_path_safe


class TestIsUrlSafe:
    """Tests for SSRF URL validation."""

    def test_valid_https_url(self):
        with patch("app.security.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))
            ]
            assert is_url_safe("https://example.com") is None

    def test_valid_http_url(self):
        with patch("app.security.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))
            ]
            assert is_url_safe("http://example.com") is None

    def test_blocks_ftp_scheme(self):
        error = is_url_safe("ftp://files.example.com/data")
        assert error is not None
        assert "not allowed" in error

    def test_blocks_file_scheme(self):
        error = is_url_safe("file:///etc/passwd")
        assert error is not None
        assert "not allowed" in error

    def test_blocks_metadata_google(self):
        error = is_url_safe("http://metadata.google.internal/computeMetadata/v1/")
        assert error is not None
        assert "Blocked host" in error

    def test_blocks_aws_metadata(self):
        error = is_url_safe("http://169.254.169.254/latest/meta-data/")
        assert error is not None
        assert "Blocked host" in error

    def test_blocks_private_ip(self):
        with patch("app.security.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, 0, 0, "", ("192.168.1.1", 0))
            ]
            error = is_url_safe("http://internal-service.local")
            assert error is not None
            assert "non-public IP" in error

    def test_blocks_loopback(self):
        with patch("app.security.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, 0, 0, "", ("127.0.0.1", 0))
            ]
            error = is_url_safe("http://localhost:8080")
            assert error is not None
            assert "non-public IP" in error

    def test_blocks_no_hostname(self):
        error = is_url_safe("http://")
        assert error is not None

    def test_blocks_unresolvable_hostname(self):
        with patch("app.security.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.side_effect = socket.gaierror("Name resolution failed")
            error = is_url_safe("http://nonexistent.invalid")
            assert error is not None
            assert "Cannot resolve" in error

    def test_invalid_url(self):
        error = is_url_safe("")
        assert error is not None


class TestIsSandboxPathSafe:
    """Tests for sandbox path traversal protection."""

    def test_valid_relative_path(self):
        assert is_sandbox_path_safe("output.png") is None

    def test_valid_workspace_path(self):
        assert is_sandbox_path_safe("/workspace/data/report.csv") is None

    def test_blocks_path_traversal(self):
        error = is_sandbox_path_safe("../../etc/passwd")
        assert error is not None
        assert "traversal" in error

    def test_blocks_absolute_outside_workspace(self):
        error = is_sandbox_path_safe("/etc/passwd")
        assert error is not None
        assert "/workspace" in error

    def test_blocks_dot_dot_in_path(self):
        error = is_sandbox_path_safe("/workspace/../etc/shadow")
        assert error is not None
        # normpath resolves /workspace/../etc/shadow → /etc/shadow,
        # which is blocked by the "must be under /workspace" check.
        assert "/workspace" in error or "traversal" in error

    def test_blocks_null_bytes(self):
        error = is_sandbox_path_safe("output\x00.png")
        assert error is not None
        assert "Null bytes" in error

    def test_blocks_long_path(self):
        error = is_sandbox_path_safe("a" * 256)
        assert error is not None
        assert "too long" in error

    def test_max_length_path_allowed(self):
        assert is_sandbox_path_safe("a" * 255) is None
