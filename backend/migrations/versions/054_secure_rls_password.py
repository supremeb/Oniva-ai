"""Secure RLS role password - read from environment variable

This migration fixes a CRITICAL security vulnerability where the PostgreSQL
RLS role password was hardcoded in migration files (011, 043).

The password is now read from APP_DATABASE_PASSWORD environment variable.
In production, the migration will FAIL if:
- APP_DATABASE_PASSWORD is not set
- APP_DATABASE_PASSWORD is a known insecure default

Revision ID: 054_secure_rls_password
Revises: 053_fix_workflow_version_trigger
Create Date: 2026-01-31
"""
import os
from alembic import op

revision = '054_secure_rls_password'
down_revision = '053_fix_workflow_version_trigger'
branch_labels = None
depends_on = None

# Known insecure default passwords that must be rejected in production
INSECURE_DEFAULTS = {
    'app_password_change_me',
    'oniva_secure_2026',
    'password',
    'changeme',
    'dev_password_only',
    '',
}


def upgrade() -> None:
    """Update the oniva_app role with a secure password from environment."""
    password = os.environ.get('APP_DATABASE_PASSWORD', '')
    environment = os.environ.get('ENVIRONMENT', 'development').lower()
    is_production = environment in ('production', 'prod', 'staging')

    # In production, enforce secure password
    if is_production:
        if not password:
            raise ValueError(
                "CRITICAL: APP_DATABASE_PASSWORD environment variable must be set in production.\n"
                "Generate a secure password with:\n"
                "  python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        if password.lower() in INSECURE_DEFAULTS or len(password) < 16:
            raise ValueError(
                "CRITICAL: APP_DATABASE_PASSWORD is insecure or too short (min 16 chars).\n"
                "Current value matches a known default or is too short.\n"
                "Generate a secure password with:\n"
                "  python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )

    # Use provided password or development-only default
    safe_password = password if password else 'dev_password_local_only'

    # Escape single quotes in password for SQL
    escaped_password = safe_password.replace("'", "''")

    # Create or update the oniva_app role with secure password
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'oniva_app') THEN
                -- Role exists, update password
                ALTER ROLE oniva_app WITH PASSWORD '{escaped_password}';
                RAISE NOTICE 'Updated password for existing role oniva_app';
            ELSE
                -- Create role with secure password
                CREATE ROLE oniva_app WITH LOGIN PASSWORD '{escaped_password}';
                RAISE NOTICE 'Created role oniva_app with secure password';
            END IF;
        END
        $$;
    """)

    # Ensure role has required permissions (idempotent)
    op.execute("""
        DO $$
        BEGIN
            -- Grant database connection (ignore if already granted)
            EXECUTE 'GRANT CONNECT ON DATABASE ' || current_database() || ' TO oniva_app';
        EXCEPTION WHEN OTHERS THEN
            -- Ignore errors (e.g., already granted)
            NULL;
        END
        $$;
    """)

    op.execute("""
        GRANT USAGE ON SCHEMA public TO oniva_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO oniva_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO oniva_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO oniva_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO oniva_app;
    """)


def downgrade() -> None:
    """
    Downgrade resets password to dev default.
    WARNING: Do NOT run in production - will break RLS security!
    """
    environment = os.environ.get('ENVIRONMENT', 'development').lower()
    if environment in ('production', 'prod', 'staging'):
        raise RuntimeError(
            "REFUSING to downgrade RLS password in production/staging. "
            "This would reset the password to an insecure default."
        )

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'oniva_app') THEN
                ALTER ROLE oniva_app WITH PASSWORD 'dev_password_local_only';
            END IF;
        END
        $$;
    """)
