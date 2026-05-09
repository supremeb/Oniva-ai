"""Password Policy Enforcement.

SECURITY: Enforces password complexity requirements for user registration
and password changes. Based on NIST SP 800-63B guidelines.

Configuration:
    Defaults can be overridden via environment variables:
    - PASSWORD_MIN_LENGTH: Minimum length (default: 12)
    - PASSWORD_REQUIRE_UPPERCASE: Require uppercase (default: true)
    - PASSWORD_REQUIRE_LOWERCASE: Require lowercase (default: true)
    - PASSWORD_REQUIRE_DIGIT: Require digit (default: true)
    - PASSWORD_REQUIRE_SPECIAL: Require special char (default: true)

Usage:
    from app.auth.password_policy import PasswordPolicy

    is_valid, errors = PasswordPolicy.validate("MyPassword123!")
    if not is_valid:
        raise HTTPException(400, detail={"password_errors": errors})
"""

import os
import re
from typing import List, Tuple


class PasswordPolicy:
    """
    Password complexity requirements based on security best practices.

    NIST SP 800-63B recommends:
    - Minimum 8 characters
    - Check against common password lists (TODO: implement breach check)
    - Allow all printable ASCII characters
    - No arbitrary composition rules (but we keep them for legacy compliance)
    """

    # Configuration with environment variable overrides
    MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", "8"))
    MAX_LENGTH = 128  # Prevent DoS via very long passwords
    REQUIRE_UPPERCASE = os.environ.get("PASSWORD_REQUIRE_UPPERCASE", "true").lower() == "true"
    REQUIRE_LOWERCASE = os.environ.get("PASSWORD_REQUIRE_LOWERCASE", "true").lower() == "true"
    REQUIRE_DIGIT = os.environ.get("PASSWORD_REQUIRE_DIGIT", "true").lower() == "true"
    REQUIRE_SPECIAL = os.environ.get("PASSWORD_REQUIRE_SPECIAL", "true").lower() == "true"

    # Special characters allowed
    SPECIAL_CHARS = r"[!@#$%^&*(),.?\":{}|<>\-_=+\[\]\\/`~;']"

    # Common passwords to reject (top 100 most common)
    COMMON_PASSWORDS = frozenset([
        "password", "123456", "12345678", "qwerty", "abc123",
        "monkey", "1234567", "letmein", "trustno1", "dragon",
        "baseball", "iloveyou", "master", "sunshine", "ashley",
        "bailey", "passw0rd", "shadow", "123123", "654321",
        "superman", "qazwsx", "michael", "football", "password1",
        "password123", "welcome", "welcome1", "admin", "login",
    ])

    @classmethod
    def validate(cls, password: str) -> Tuple[bool, List[str]]:
        """
        Validate password against policy requirements.

        Args:
            password: Password string to validate

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors = []

        # Length checks
        if len(password) < cls.MIN_LENGTH:
            errors.append(f"Password must be at least {cls.MIN_LENGTH} characters")

        if len(password) > cls.MAX_LENGTH:
            errors.append(f"Password cannot exceed {cls.MAX_LENGTH} characters")

        # Complexity checks
        if cls.REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
            errors.append("Password must contain at least one uppercase letter")

        if cls.REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
            errors.append("Password must contain at least one lowercase letter")

        if cls.REQUIRE_DIGIT and not re.search(r'\d', password):
            errors.append("Password must contain at least one digit")

        if cls.REQUIRE_SPECIAL and not re.search(cls.SPECIAL_CHARS, password):
            errors.append("Password must contain at least one special character")

        # Common password check
        if password.lower() in cls.COMMON_PASSWORDS:
            errors.append("Password is too common. Please choose a more unique password")

        # Check for repeated characters (e.g., "aaaa")
        if re.search(r'(.)\1{3,}', password):
            errors.append("Password cannot contain more than 3 consecutive identical characters")

        # Check for sequential characters (e.g., "1234", "abcd")
        if cls._has_sequential_chars(password, 4):
            errors.append("Password cannot contain sequential characters (e.g., '1234', 'abcd')")

        return len(errors) == 0, errors

    @classmethod
    def _has_sequential_chars(cls, password: str, min_seq: int = 4) -> bool:
        """Check for sequential characters in password."""
        password_lower = password.lower()

        for i in range(len(password_lower) - min_seq + 1):
            # Check ascending sequence
            is_ascending = True
            # Check descending sequence
            is_descending = True

            for j in range(min_seq - 1):
                curr_ord = ord(password_lower[i + j])
                next_ord = ord(password_lower[i + j + 1])

                if next_ord != curr_ord + 1:
                    is_ascending = False
                if next_ord != curr_ord - 1:
                    is_descending = False

            if is_ascending or is_descending:
                return True

        return False

    @classmethod
    def get_requirements_text(cls) -> str:
        """Get human-readable password requirements."""
        requirements = [f"At least {cls.MIN_LENGTH} characters"]

        if cls.REQUIRE_UPPERCASE:
            requirements.append("At least one uppercase letter (A-Z)")
        if cls.REQUIRE_LOWERCASE:
            requirements.append("At least one lowercase letter (a-z)")
        if cls.REQUIRE_DIGIT:
            requirements.append("At least one digit (0-9)")
        if cls.REQUIRE_SPECIAL:
            requirements.append("At least one special character (!@#$%^&*...)")

        return "\n".join(f"• {req}" for req in requirements)
