"""Add episodic_memories and user_preferences tables for Enhanced Memory System

Revision ID: 032_add_memory_tables
Revises: 031_add_tenant_id_to_workflow_executions
Create Date: 2026-01-17

Part of Enhanced Agent Memory System feature.
Enables:
- Cross-session episodic memory with vector similarity search
- Persistent user preferences for agent personalization
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector

revision = '032_add_memory_tables'
down_revision = '031_add_tenant_id_to_workflow_executions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. User Preferences Table
    # ===========================================
    op.create_table(
        'user_preferences',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False, index=True),

        # Preferences (JSONB for flexibility)
        sa.Column('preferences', JSONB, nullable=False, server_default='{}'),

        # Schema version for migrations
        sa.Column('schema_version', sa.Integer, server_default='1'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),

        # Unique constraint: one preference row per user
        sa.UniqueConstraint('tenant_id', 'user_id', name='uq_user_preferences_tenant_user'),
    )

    # Composite index for filtered queries
    op.create_index(
        'idx_user_preferences_tenant_user',
        'user_preferences',
        ['tenant_id', 'user_id']
    )

    # ===========================================
    # 2. Episodic Memories Table
    # ===========================================
    op.create_table(
        'episodic_memories',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False, index=True),

        # Session reference (links to filesystem session)
        sa.Column('session_id', sa.String(100), nullable=True),

        # Agent context
        sa.Column('agent_name', sa.String(100), nullable=False),
        sa.Column('action_type', sa.String(50), nullable=False),

        # Combined context + decision for single embedding
        sa.Column('context_decision', sa.Text, nullable=False),

        # Single embedding for similarity search (1536 dimensions for OpenAI)
        sa.Column('embedding', Vector(1536), nullable=True),

        # Additional context
        sa.Column('rationale', sa.Text, nullable=True),
        sa.Column('outcome', JSONB, nullable=True),
        sa.Column('entities', JSONB, server_default='[]'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Retention (default 2 years, max 7 years per security requirements)
        sa.Column('valid_until', sa.DateTime(timezone=True),
                  server_default=sa.text("NOW() + INTERVAL '2 years'")),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),

        # Check constraint for action_type
        sa.CheckConstraint(
            "action_type IN ('analysis', 'compliance_check', 'recommendation', 'research', 'other')",
            name='ck_episodic_memories_action_type'
        ),
    )

    # Composite indexes for filtered queries
    op.create_index(
        'idx_episodic_memories_tenant_user',
        'episodic_memories',
        ['tenant_id', 'user_id']
    )
    op.create_index(
        'idx_episodic_memories_agent_action',
        'episodic_memories',
        ['agent_name', 'action_type']
    )
    op.create_index(
        'idx_episodic_memories_created',
        'episodic_memories',
        [sa.text('created_at DESC')]
    )

    # GIN index for entity search
    op.create_index(
        'idx_episodic_memories_entities',
        'episodic_memories',
        ['entities'],
        postgresql_using='gin'
    )

    # HNSW index for vector similarity search
    # Parameters tuned for 1M+ vectors per Performance Oracle recommendations
    # m=24, ef_construction=100 for better recall with 1536-dim vectors
    # Note: NOT using CONCURRENTLY as it cannot run inside a transaction
    # This is safe for a new empty table
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodic_memories_embedding
        ON episodic_memories
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 24, ef_construction = 100);
    """)

    # ===========================================
    # 3. Enable Row Level Security
    # ===========================================

    # RLS for user_preferences with user-level isolation
    # Note: Uses 'app.current_tenant' (not 'app.current_tenant_id') to match existing convention
    op.execute("""
        ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
    """)
    op.execute("""
        CREATE POLICY user_isolation_prefs ON user_preferences
            FOR ALL
            TO oniva_app
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            );
    """)
    op.execute("ALTER TABLE user_preferences FORCE ROW LEVEL SECURITY;")

    # RLS for episodic_memories with user-level isolation
    op.execute("""
        ALTER TABLE episodic_memories ENABLE ROW LEVEL SECURITY;
    """)
    op.execute("""
        CREATE POLICY user_isolation_episodic ON episodic_memories
            FOR ALL
            TO oniva_app
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            );
    """)
    op.execute("ALTER TABLE episodic_memories FORCE ROW LEVEL SECURITY;")

    # ===========================================
    # 4. Updated_at Trigger Function
    # ===========================================
    op.execute("""
        CREATE OR REPLACE FUNCTION update_memory_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)

    op.execute("""
        CREATE TRIGGER update_user_preferences_updated_at
            BEFORE UPDATE ON user_preferences
            FOR EACH ROW
            EXECUTE FUNCTION update_memory_updated_at();
    """)

    # ===========================================
    # 5. Audit Triggers
    # ===========================================
    # Custom audit function for memory tables (no 'name' column dependency)
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_memory_trigger_func()
        RETURNS TRIGGER AS $$
        DECLARE
            v_action TEXT;
            v_changes JSONB;
            v_resource_name TEXT;
            v_user_id UUID;
            v_tenant_id UUID;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                v_action := 'CREATE';
                v_changes := jsonb_build_object('after', row_to_json(NEW));
                v_resource_name := TG_TABLE_NAME || ':' || NEW.id::text;
                v_tenant_id := NEW.tenant_id;
            ELSIF TG_OP = 'UPDATE' THEN
                v_action := 'UPDATE';
                v_changes := jsonb_build_object('before', row_to_json(OLD), 'after', row_to_json(NEW));
                v_resource_name := TG_TABLE_NAME || ':' || NEW.id::text;
                v_tenant_id := NEW.tenant_id;
            ELSIF TG_OP = 'DELETE' THEN
                v_action := 'DELETE';
                v_changes := jsonb_build_object('before', row_to_json(OLD));
                v_resource_name := TG_TABLE_NAME || ':' || OLD.id::text;
                v_tenant_id := OLD.tenant_id;
            END IF;

            v_user_id := NULLIF(current_setting('app.current_user_id', true), '')::UUID;

            IF v_tenant_id IS NOT NULL THEN
                INSERT INTO audit_events (
                    tenant_id, user_id, user_email, action, resource_type,
                    resource_id, resource_name, changes, ip_address, user_agent
                ) VALUES (
                    v_tenant_id, v_user_id,
                    current_setting('app.current_user_email', true),
                    v_action, TG_TABLE_NAME,
                    COALESCE(NEW.id, OLD.id), v_resource_name, v_changes,
                    NULLIF(current_setting('app.client_ip', true), '')::INET,
                    current_setting('app.user_agent', true)
                );
            END IF;

            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """)

    # Add audit triggers for compliance tracking
    op.execute("""
        CREATE TRIGGER audit_user_preferences_trigger
            AFTER INSERT OR UPDATE OR DELETE ON user_preferences
            FOR EACH ROW EXECUTE FUNCTION audit_memory_trigger_func();
    """)
    op.execute("""
        CREATE TRIGGER audit_episodic_memories_trigger
            AFTER INSERT OR UPDATE OR DELETE ON episodic_memories
            FOR EACH ROW EXECUTE FUNCTION audit_memory_trigger_func();
    """)


def downgrade() -> None:
    # Drop audit triggers
    op.execute("DROP TRIGGER IF EXISTS audit_episodic_memories_trigger ON episodic_memories;")
    op.execute("DROP TRIGGER IF EXISTS audit_user_preferences_trigger ON user_preferences;")

    # Drop updated_at trigger
    op.execute("DROP TRIGGER IF EXISTS update_user_preferences_updated_at ON user_preferences;")

    # Drop custom functions
    op.execute("DROP FUNCTION IF EXISTS audit_memory_trigger_func();")
    op.execute("DROP FUNCTION IF EXISTS update_memory_updated_at();")

    # Drop RLS policies and disable RLS
    op.execute("DROP POLICY IF EXISTS user_isolation_episodic ON episodic_memories;")
    op.execute("ALTER TABLE episodic_memories DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS user_isolation_prefs ON user_preferences;")
    op.execute("ALTER TABLE user_preferences DISABLE ROW LEVEL SECURITY;")

    # Drop HNSW index
    op.execute("DROP INDEX IF EXISTS idx_episodic_memories_embedding;")

    # Drop tables (cascades will handle remaining indexes)
    op.drop_table('episodic_memories')
    op.drop_table('user_preferences')
