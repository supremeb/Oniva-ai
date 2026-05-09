"""
Security Hardening Tests

Tests for the January 2026 Security Hardening Sprint covering:
- S-CRIT-1: Code sandbox hardening
- S-CRIT-2: Auth bypass removal
- S-HIGH-1: CORS configuration
- S-HIGH-2: Rate limiting
- S-MED-1: JWT token blacklist
- S-MED-3: Password policy

These tests verify that security vulnerabilities have been properly addressed.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestSandboxSecurity:
    """Tests for code execution sandbox hardening (S-CRIT-1)."""

    def test_is_safe_code_blocks_dunder_attributes(self):
        """Test that __class__, __globals__, etc. are blocked."""
        from app.agents.skills.code import is_safe_code

        # Test various sandbox escape attempts
        escape_attempts = [
            '().__class__.__bases__[0].__subclasses__()',
            'getattr(str, "__class__")',
            'object.__subclasses__()',
            'x.__globals__',
            'func.__code__',
            'type.__mro__',
        ]

        for code in escape_attempts:
            is_safe, error = is_safe_code(code)
            assert not is_safe, f"Should block: {code}"
            assert error is not None

    def test_is_safe_code_blocks_private_attributes(self):
        """Test that private attribute access is blocked."""
        from app.agents.skills.code import is_safe_code

        is_safe, error = is_safe_code('obj._private_method()')
        assert not is_safe
        assert "private" in error.lower() or "not allowed" in error.lower()

    def test_is_safe_code_blocks_dangerous_builtins(self):
        """Test that dangerous builtins are blocked."""
        from app.agents.skills.code import is_safe_code

        dangerous_calls = [
            'eval("1+1")',
            'exec("print(1)")',
            'compile("x=1", "", "exec")',
            'open("/etc/passwd")',
            '__import__("os")',
            'getattr(x, "y")',
            'type(x)',
            'dir(x)',
        ]

        for code in dangerous_calls:
            is_safe, error = is_safe_code(code)
            assert not is_safe, f"Should block: {code}"

    def test_is_safe_code_allows_safe_operations(self):
        """Test that legitimate code is allowed."""
        from app.agents.skills.code import is_safe_code

        safe_code = '''
import math
import json

data = [1, 2, 3, 4, 5]
result = sum(data) / len(data)
squared = [x ** 2 for x in data]
formatted = json.dumps({"average": result})
print(formatted)
'''
        is_safe, error = is_safe_code(safe_code)
        assert is_safe, f"Should allow safe code: {error}"

    def test_safe_builtins_excludes_dangerous_functions(self):
        """Test that SAFE_BUILTINS doesn't include escape vectors."""
        from app.agents.skills.code import SAFE_BUILTINS

        dangerous_builtins = {
            'getattr', 'setattr', 'delattr',
            'type', 'object',
            'dir', 'vars',
            'eval', 'exec', 'compile',
            '__import__',
        }

        for dangerous in dangerous_builtins:
            assert dangerous not in SAFE_BUILTINS, f"{dangerous} should not be in SAFE_BUILTINS"


class TestAuthBypassRemoval:
    """Tests for authentication bypass removal (S-CRIT-2)."""

    @pytest.mark.asyncio
    async def test_get_current_user_requires_authentication(self):
        """Test that get_current_user raises 401 without valid user."""
        from app.auth.dependencies import get_current_user
        from fastapi import HTTPException

        # Create a mock request with no user
        mock_request = MagicMock()
        mock_request.state.user = None

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(mock_request)

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_no_demo_user_fallback(self):
        """Test that there's no automatic demo user fallback."""
        from app.auth.dependencies import get_current_user
        from fastapi import HTTPException

        # Even with DEBUG=true and development environment,
        # should not auto-authenticate
        mock_request = MagicMock()
        mock_request.state.user = None

        with patch.dict('os.environ', {'DEBUG': 'true', 'ENVIRONMENT': 'development'}):
            with pytest.raises(HTTPException):
                await get_current_user(mock_request)


class TestCORSConfiguration:
    """Tests for CORS configuration (S-HIGH-1)."""

    def test_cors_production_validation_rejects_wildcard(self):
        """Test that production validation rejects wildcard CORS."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,
            ENVIRONMENT="production",
            CORS_ORIGINS="*",
            SECRET_KEY="a" * 32,  # Valid length
            ENCRYPTION_SALT="b" * 32,
            DATABASE_URL="postgresql+asyncpg://user:safepassword123@host:5432/db",
            APP_DATABASE_PASSWORD="safepassword456!"
        )

        with pytest.raises(ValueError) as exc_info:
            settings.validate_production_security()

        assert "CORS_ORIGINS" in str(exc_info.value)

    def test_cors_production_validation_rejects_empty(self):
        """Test that production validation rejects empty CORS."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,
            ENVIRONMENT="production",
            CORS_ORIGINS="",
            SECRET_KEY="a" * 32,
            ENCRYPTION_SALT="b" * 32,
            DATABASE_URL="postgresql+asyncpg://user:safepassword123@host:5432/db",
            APP_DATABASE_PASSWORD="xK9#mP2$vL7@nQ4wR8"  # Complex password to pass validation
        )

        with pytest.raises(ValueError) as exc_info:
            settings.validate_production_security()

        assert "CORS_ORIGINS" in str(exc_info.value)

    def test_cors_production_accepts_explicit_origins(self):
        """Test that production validation accepts explicit CORS origins."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,
            ENVIRONMENT="production",
            CORS_ORIGINS="https://app.example.com,https://admin.example.com",
            SECRET_KEY="a" * 32,
            ENCRYPTION_SALT="b" * 32,
            DATABASE_URL="postgresql+asyncpg://user:safepassword123@host:5432/db",
            APP_DATABASE_PASSWORD="safepassword456!"
        )

        # Should not raise for CORS_ORIGINS specifically
        # (other validations may still fail)
        try:
            settings.validate_production_security()
            cors_ok = True
        except ValueError as e:
            # If it fails, it should not be due to CORS
            cors_ok = "CORS_ORIGINS" not in str(e)

        assert cors_ok, "Production should accept explicit CORS origins"


class TestPasswordPolicy:
    """Tests for password policy (S-MED-3)."""

    def test_validates_minimum_length(self):
        """Test password minimum length validation."""
        from app.auth.password_policy import PasswordPolicy

        is_valid, errors = PasswordPolicy.validate("Short1!")
        assert not is_valid
        assert any("at least" in e.lower() and "character" in e.lower() for e in errors)

    def test_validates_uppercase_requirement(self):
        """Test uppercase letter requirement."""
        from app.auth.password_policy import PasswordPolicy

        is_valid, errors = PasswordPolicy.validate("alllowercase123!")
        assert not is_valid
        assert any("uppercase" in e.lower() for e in errors)

    def test_validates_lowercase_requirement(self):
        """Test lowercase letter requirement."""
        from app.auth.password_policy import PasswordPolicy

        is_valid, errors = PasswordPolicy.validate("ALLUPPERCASE123!")
        assert not is_valid
        assert any("lowercase" in e.lower() for e in errors)

    def test_validates_digit_requirement(self):
        """Test digit requirement."""
        from app.auth.password_policy import PasswordPolicy

        is_valid, errors = PasswordPolicy.validate("NoDigitsHere!!!")
        assert not is_valid
        assert any("digit" in e.lower() for e in errors)

    def test_validates_special_char_requirement(self):
        """Test special character requirement."""
        from app.auth.password_policy import PasswordPolicy

        is_valid, errors = PasswordPolicy.validate("NoSpecialChars123")
        assert not is_valid
        assert any("special" in e.lower() for e in errors)

    def test_rejects_common_passwords(self):
        """Test rejection of common passwords."""
        from app.auth.password_policy import PasswordPolicy

        # Test that a common password is detected (even if it fails other checks)
        # "password" is in the COMMON_PASSWORDS list
        is_valid, errors = PasswordPolicy.validate("password")
        assert not is_valid
        assert any("common" in e.lower() for e in errors)

    def test_accepts_valid_password(self):
        """Test that valid passwords are accepted."""
        from app.auth.password_policy import PasswordPolicy

        is_valid, errors = PasswordPolicy.validate("MySecure#Pass2026!")
        assert is_valid
        assert len(errors) == 0


class TestRateLimiting:
    """Tests for rate limiting configuration (S-HIGH-2)."""

    def test_rate_limit_middleware_has_fail_open_parameter(self):
        """Test that RateLimitMiddleware supports fail_open parameter."""
        from app.middleware.rate_limit import RateLimitMiddleware

        # Should be able to instantiate with fail_open parameter
        mock_app = MagicMock()
        middleware = RateLimitMiddleware(
            mock_app,
            requests_per_minute=100,
            fail_open=True
        )
        assert middleware.limiter.fail_open is True

        middleware_strict = RateLimitMiddleware(
            mock_app,
            requests_per_minute=100,
            fail_open=False
        )
        assert middleware_strict.limiter.fail_open is False


class TestJWTTokenBlacklist:
    """Tests for JWT token blacklist (S-MED-1)."""

    def test_jwt_includes_jti_claim(self):
        """Test that JWT tokens include unique jti claim."""
        from app.auth.jwt import create_access_token, decode_access_token

        token = create_access_token(
            user_id="test-user",
            email="test@example.com",
            tenant_id="test-tenant",
            tenant_slug="test",
            role="admin"
        )

        payload = decode_access_token(token)
        assert payload is not None
        assert "jti" in payload
        assert payload["jti"] is not None
        # jti should be a UUID string
        assert len(payload["jti"]) == 36

    def test_jwt_jti_is_unique(self):
        """Test that each token gets a unique jti."""
        from app.auth.jwt import create_access_token, decode_access_token

        token1 = create_access_token(
            user_id="test-user",
            email="test@example.com",
            tenant_id="test-tenant",
            tenant_slug="test",
            role="admin"
        )

        token2 = create_access_token(
            user_id="test-user",
            email="test@example.com",
            tenant_id="test-tenant",
            tenant_slug="test",
            role="admin"
        )

        payload1 = decode_access_token(token1)
        payload2 = decode_access_token(token2)

        assert payload1["jti"] != payload2["jti"]
