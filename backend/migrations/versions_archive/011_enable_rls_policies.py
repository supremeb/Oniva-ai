"""Enable Row-Level Security policies for tenant isolation

Revision ID: 011_rls_policies
Revises: 010_tenant_id_all
Create Date: 2026-01-07
"""
from alembic import op

revision = '011_rls_policies'
down_revision = '010_tenant_id_all'
branch_labels = None
depends_on = None

# Tables that need RLS (all with tenant_id)
RLS_TABLES = [
    'users',
    'api_keys',
    'connectors',
    'custom_agents',
    'workflows',
    'workflow_executions',
    'documents',
    'audit_logs',
    'skill_executions',
]

# Skills table has special handling (system skills have NULL tenant_id)
SKILLS_TABLE = 'skills'


def upgrade() -> None:
    # Create application role if not exists
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oniva_app') THEN
                CREATE ROLE oniva_app WITH LOGIN PASSWORD 'app_password_change_me';
            END IF;
        END
        $$;
    """)

    # Grant necessary permissions to app role
    op.execute("""
        GRANT CONNECT ON DATABASE oniva TO oniva_app;
        GRANT USAGE ON SCHEMA public TO oniva_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO oniva_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO oniva_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO oniva_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO oniva_app;
    """)

    # Enable RLS on all tables
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

        # Create policy: users see only their tenant's data
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            FOR ALL
            TO oniva_app
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

    # Enable RLS on skills table with special handling
    op.execute(f"ALTER TABLE {SKILLS_TABLE} ENABLE ROW LEVEL SECURITY")

    # Skills policy: see system skills (NULL tenant) OR own tenant's custom skills
    op.execute(f"""
        CREATE POLICY tenant_isolation_skills ON {SKILLS_TABLE}
        FOR ALL
        TO oniva_app
        USING (
            tenant_id IS NULL  -- System skills visible to all
            OR tenant_id = current_setting('app.current_tenant', true)::uuid
        )
        WITH CHECK (
            -- Can only create/modify own tenant's skills
            tenant_id = current_setting('app.current_tenant', true)::uuid
        )
    """)

    # Tenants table: visible to authenticated users (read-only for app)
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_read_own ON tenants
        FOR SELECT
        TO oniva_app
        USING (id = current_setting('app.current_tenant', true)::uuid)
    """)

    # Force RLS for table owners too (important for testing)
    for table in RLS_TABLES + [SKILLS_TABLE, 'tenants']:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Disable RLS and drop policies
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_skills ON {SKILLS_TABLE}")
    op.execute(f"ALTER TABLE {SKILLS_TABLE} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tenant_read_own ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")

    # Optionally revoke and drop role (be careful in shared environments)
    # op.execute("DROP ROLE IF EXISTS oniva_app")
