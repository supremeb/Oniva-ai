"""Enable Row-Level Security on ALL tenant-scoped tables

This is a CRITICAL SECURITY migration that enables RLS on all tables
with tenant_id columns. Without this, data leaks between tenants.

Revision ID: 043_tenant_rls_all
Revises: 042_extend_skill_execution_audit
Create Date: 2026-01-26
"""
from alembic import op

revision = '043_tenant_rls_all'
down_revision = '042_extend_skill_execution_audit'
branch_labels = None
depends_on = None

# All tables with tenant_id that need RLS policies (verified from database)
TENANT_TABLES = [
    'users',
    'api_keys',
    'connectors',
    'custom_agents',
    'workflows',
    'workflow_executions',
    'workflow_checkpoints',
    'workflow_sessions',
    'workflow_versions',
    'workflow_state_logs',
    'audit_events',
    'chat_sessions',
    'agent_chat_sessions',
    'agent_versions',
    'agent_test_cases',
    'agent_accuracy_history',
    'background_jobs',
    'compliance_audit_logs',
    'compliance_executions',
    'human_review_tickets',
    'llm_providers',
    'llm_usage_records',
    'deploy_history',
    'episodic_memories',
    'scheduled_workflows',
    'scheduled_workflow_executions',
    'user_preferences',
    'pending_users',
    'internal_endpoint_whitelist',
]

# Skills table has special handling (system skills have NULL tenant_id)
SKILLS_TABLE = 'skills'
SKILL_EXECUTIONS_TABLE = 'skill_executions'


def upgrade() -> None:
    # Ensure application role exists (idempotent)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oniva_app') THEN
                CREATE ROLE oniva_app WITH LOGIN PASSWORD 'oniva_secure_2026';
            END IF;
        END
        $$;
    """)

    # Grant permissions to app role
    op.execute("""
        GRANT CONNECT ON DATABASE oniva TO oniva_app;
        GRANT USAGE ON SCHEMA public TO oniva_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO oniva_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO oniva_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO oniva_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO oniva_app;
    """)

    # Enable RLS on all tenant tables
    for table in TENANT_TABLES:
        # Check if table exists before enabling RLS
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = '{table}') THEN
                    -- Enable RLS
                    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
                    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;

                    -- Drop existing policy if any (for idempotency)
                    DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};

                    -- Create policy: users see only their tenant's data
                    CREATE POLICY tenant_isolation_{table} ON {table}
                    FOR ALL
                    TO oniva_app
                    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
                END IF;
            END
            $$;
        """)

    # Enable RLS on skills table with special handling for system skills
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = '{SKILLS_TABLE}') THEN
                ALTER TABLE {SKILLS_TABLE} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE {SKILLS_TABLE} FORCE ROW LEVEL SECURITY;

                DROP POLICY IF EXISTS tenant_isolation_skills ON {SKILLS_TABLE};

                -- System skills (NULL tenant_id) are visible to all tenants
                CREATE POLICY tenant_isolation_skills ON {SKILLS_TABLE}
                FOR ALL
                TO oniva_app
                USING (
                    tenant_id IS NULL  -- System skills visible to all
                    OR tenant_id = current_setting('app.current_tenant', true)::uuid
                )
                WITH CHECK (
                    tenant_id = current_setting('app.current_tenant', true)::uuid
                );
            END IF;
        END
        $$;
    """)

    # Enable RLS on skill_executions
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = '{SKILL_EXECUTIONS_TABLE}') THEN
                ALTER TABLE {SKILL_EXECUTIONS_TABLE} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE {SKILL_EXECUTIONS_TABLE} FORCE ROW LEVEL SECURITY;

                DROP POLICY IF EXISTS tenant_isolation_skill_executions ON {SKILL_EXECUTIONS_TABLE};

                CREATE POLICY tenant_isolation_skill_executions ON {SKILL_EXECUTIONS_TABLE}
                FOR ALL
                TO oniva_app
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            END IF;
        END
        $$;
    """)

    # Tenants table: visible to authenticated users (read-only for app)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'tenants') THEN
                ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
                ALTER TABLE tenants FORCE ROW LEVEL SECURITY;

                DROP POLICY IF EXISTS tenant_read_own ON tenants;

                CREATE POLICY tenant_read_own ON tenants
                FOR SELECT
                TO oniva_app
                USING (id = current_setting('app.current_tenant', true)::uuid);
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Disable RLS and drop policies
    for table in TENANT_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = '{table}') THEN
                    DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};
                    ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
                END IF;
            END
            $$;
        """)

    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = '{SKILLS_TABLE}') THEN
                DROP POLICY IF EXISTS tenant_isolation_skills ON {SKILLS_TABLE};
                ALTER TABLE {SKILLS_TABLE} DISABLE ROW LEVEL SECURITY;
            END IF;
        END
        $$;
    """)

    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = '{SKILL_EXECUTIONS_TABLE}') THEN
                DROP POLICY IF EXISTS tenant_isolation_skill_executions ON {SKILL_EXECUTIONS_TABLE};
                ALTER TABLE {SKILL_EXECUTIONS_TABLE} DISABLE ROW LEVEL SECURITY;
            END IF;
        END
        $$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'tenants') THEN
                DROP POLICY IF EXISTS tenant_read_own ON tenants;
                ALTER TABLE tenants DISABLE ROW LEVEL SECURITY;
            END IF;
        END
        $$;
    """)
