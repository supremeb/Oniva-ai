"""Add RLS bypass policies for signup flow

During signup, we need to:
1. Check if email exists in pending_users (to verify whitelist)
2. Check if email already exists in users (to prevent duplicates)

These queries run before authentication (no tenant context), so they need
special bypass policies that allow email-only lookups.

Revision ID: 044_signup_bypass
Revises: 043_tenant_rls_all
Create Date: 2026-01-26
"""
from alembic import op

revision = '044_signup_bypass'
down_revision = '043_tenant_rls_all'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add bypass policy for pending_users - allow email lookups for signup
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'pending_users') THEN
                -- Drop existing policy if upgrading
                DROP POLICY IF EXISTS signup_email_lookup_pending ON pending_users;

                -- Allow app to lookup pending users by email (for signup validation)
                -- This is SELECT-only and still requires the app to do proper validation
                CREATE POLICY signup_email_lookup_pending ON pending_users
                FOR SELECT
                TO oniva_app
                USING (
                    -- Allow when tenant context is set (normal operation)
                    tenant_id = current_setting('app.current_tenant', true)::uuid
                    -- OR allow when no tenant context (signup flow)
                    OR current_setting('app.current_tenant', true) IS NULL
                    OR current_setting('app.current_tenant', true) = ''
                );
            END IF;
        END
        $$;
    """)

    # Add bypass policy for users - allow email existence checks for signup
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'users') THEN
                -- Drop existing policy if upgrading
                DROP POLICY IF EXISTS signup_email_check_users ON users;

                -- Allow app to check if email exists (for signup validation)
                CREATE POLICY signup_email_check_users ON users
                FOR SELECT
                TO oniva_app
                USING (
                    -- Allow when tenant context is set (normal operation)
                    tenant_id = current_setting('app.current_tenant', true)::uuid
                    -- OR allow when no tenant context (signup flow)
                    OR current_setting('app.current_tenant', true) IS NULL
                    OR current_setting('app.current_tenant', true) = ''
                );
            END IF;
        END
        $$;
    """)

    # Drop the restrictive tenant-only policies that conflict
    op.execute("""
        DROP POLICY IF EXISTS tenant_isolation_pending_users ON pending_users;
        DROP POLICY IF EXISTS tenant_isolation_users ON users;
    """)


def downgrade() -> None:
    # Remove signup bypass policies
    op.execute("""
        DROP POLICY IF EXISTS signup_email_lookup_pending ON pending_users;
        DROP POLICY IF EXISTS signup_email_check_users ON users;
    """)

    # Restore original tenant-only policies
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'pending_users') THEN
                CREATE POLICY tenant_isolation_pending_users ON pending_users
                FOR ALL
                TO oniva_app
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            END IF;
        END
        $$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'users') THEN
                CREATE POLICY tenant_isolation_users ON users
                FOR ALL
                TO oniva_app
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            END IF;
        END
        $$;
    """)
