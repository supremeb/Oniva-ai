#!/usr/bin/env python3
"""Generate secure secrets for production deployment.

This script generates cryptographically secure secrets for Oniva production.
Output should be stored in a secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.)
and NEVER committed to version control.

Usage:
    python scripts/generate_secrets.py

Output:
    Prints environment variables ready for secrets manager or .env file
"""
import secrets
import sys


def generate_secret_key(length: int = 32) -> str:
    """Generate cryptographically secure SECRET_KEY for JWT signing.

    Args:
        length: Length of the key (default 32 chars minimum for security)

    Returns:
        URL-safe base64-encoded random string
    """
    return secrets.token_urlsafe(length)


def generate_encryption_salt(length: int = 32) -> str:
    """Generate random salt for Fernet key derivation.

    Args:
        length: Length in bytes (default 32 bytes = 256 bits)

    Returns:
        Hex-encoded random bytes
    """
    return secrets.token_bytes(length).hex()


def generate_db_password(length: int = 32) -> str:
    """Generate secure database password.

    Args:
        length: Length of password (default 32 chars)

    Returns:
        Random alphanumeric password
    """
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def main():
    """Generate all required secrets and print to stdout."""
    print("# Generated Secrets for Oniva Production Deployment")
    print("# " + "=" * 70)
    print("# IMPORTANT: Store these in a secrets manager (AWS Secrets Manager,")
    print("#            HashiCorp Vault, GCP Secret Manager, etc.)")
    print("# NEVER commit these to version control!")
    print("# " + "=" * 70)
    print()

    # Generate JWT signing key
    secret_key = generate_secret_key(48)  # 48 chars for extra security
    print(f"SECRET_KEY={secret_key}")
    print()

    # Generate encryption salt
    encryption_salt = generate_encryption_salt(32)
    print(f"ENCRYPTION_SALT={encryption_salt}")
    print()

    # Generate PostgreSQL superuser password
    postgres_password = generate_db_password(32)
    print(f"POSTGRES_PASSWORD={postgres_password}")
    print()

    # Generate application database user password
    app_db_password = generate_db_password(32)
    print(f"APP_DATABASE_PASSWORD={app_db_password}")
    print()

    print("# " + "=" * 70)
    print("# Deployment Checklist:")
    print("# 1. Store these secrets in your secrets manager")
    print("# 2. Update Kubernetes secrets or Docker Compose environment")
    print("# 3. Verify ENV=production is set")
    print("# 4. Test startup validation catches any missing secrets")
    print("# 5. Rotate these secrets every 90 days")
    print("# " + "=" * 70)
    print()

    # Generate DATABASE_URL with the new password
    print("# Complete DATABASE_URL (use APP_DATABASE_PASSWORD):")
    print(f"# DATABASE_URL=postgresql+asyncpg://fiagents_app:{app_db_password}@postgres:5432/oniva")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
