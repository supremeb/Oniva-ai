"""Add RLS policies for signup flow

During signup (before authentication), we need to access:
1. tenants table - Create new organization
2. users table - Check if email exists, create new user
3. pending_users - Check for invitations, mark as used

These tables use CASE expressions to check NULL/empty BEFORE casting to UUID.
All other tenant-scoped tables use STRICT policies (require tenant context).

Revision ID: 048_signup_write
Revises: 047_add_memory_ui_fields
Create Date: 2026-01-26
"""
from alembic import op

revision = '048_signup_write'
down_revision = '047_add_memory_ui_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old policies that might conflict
    op.execute("""
        DROP POLICY IF EXISTS signup_create_tenant ON tenants;
        DROP POLICY IF EXISTS tenant_create_for_signup ON tenants;
        DROP POLICY IF EXISTS tenant_read_for_signup ON tenants;
        DROP POLICY IF EXISTS tenant_read_own ON tenants;
        DROP POLICY IF EXISTS tenants_full_access ON tenants;

        DROP POLICY IF EXISTS signup_create_user ON users;
        DROP POLICY IF EXISTS signup_email_check_users ON users;
        DROP POLICY IF EXISTS users_full_access ON users;
        DROP POLICY IF EXISTS tenant_isolation_users ON users;

        DROP POLICY IF EXISTS signup_email_lookup_pending ON pending_users;
        DROP POLICY IF EXISTS signup_update_pending ON pending_users;
        DROP POLICY IF EXISTS signup_delete_pending ON pending_users;
        DROP POLICY IF EXISTS pending_users_full_access ON pending_users;
        DROP POLICY IF EXISTS tenant_isolation_pending_users ON pending_users;
    """)

    # Tenants: Allow full access for signup (no tenant context during registration)
    # Uses CASE to check NULL/empty BEFORE uuid cast to avoid errors
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'tenants') THEN
                CREATE POLICY tenants_full_access ON tenants
                FOR ALL
                TO oniva_app
                USING (
                    CASE
                        WHEN current_setting('app.current_tenant', true) IS NULL
                             OR current_setting('app.current_tenant', true) = '' THEN true
                        ELSE id = current_setting('app.current_tenant', true)::uuid
                    END
                )
                WITH CHECK (true);
            END IF;
        END
        $$;
    """)

    # Users: Allow read for email checks, write for registration
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'users') THEN
                CREATE POLICY users_full_access ON users
                FOR ALL
                TO oniva_app
                USING (
                    CASE
                        WHEN current_setting('app.current_tenant', true) IS NULL
                             OR current_setting('app.current_tenant', true) = '' THEN true
                        ELSE tenant_id = current_setting('app.current_tenant', true)::uuid
                    END
                )
                WITH CHECK (
                    CASE
                        WHEN current_setting('app.current_tenant', true) IS NULL
                             OR current_setting('app.current_tenant', true) = '' THEN true
                        ELSE tenant_id = current_setting('app.current_tenant', true)::uuid
                    END
                );
            END IF;
        END
        $$;
    """)

    # Pending users: Allow access for invitation flow
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'pending_users') THEN
                CREATE POLICY pending_users_full_access ON pending_users
                FOR ALL
                TO oniva_app
                USING (
                    CASE
                        WHEN current_setting('app.current_tenant', true) IS NULL
                             OR current_setting('app.current_tenant', true) = '' THEN true
                        ELSE tenant_id = current_setting('app.current_tenant', true)::uuid
                    END
                )
                WITH CHECK (
                    CASE
                        WHEN current_setting('app.current_tenant', true) IS NULL
                             OR current_setting('app.current_tenant', true) = '' THEN true
                        ELSE tenant_id = current_setting('app.current_tenant', true)::uuid
                    END
                );
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Remove signup policies and restore strict tenant isolation
    op.execute("""
        DROP POLICY IF EXISTS tenants_full_access ON tenants;
        DROP POLICY IF EXISTS users_full_access ON users;
        DROP POLICY IF EXISTS pending_users_full_access ON pending_users;
    """)

    # Restore strict policies
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'tenants') THEN
                CREATE POLICY tenant_read_own ON tenants
                FOR SELECT
                TO oniva_app
                USING (id = current_setting('app.current_tenant', true)::uuid);
            END IF;

            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'users') THEN
                CREATE POLICY tenant_isolation_users ON users
                FOR ALL
                TO oniva_app
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            END IF;

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
