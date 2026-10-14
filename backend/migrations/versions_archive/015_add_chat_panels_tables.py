"""Add chat panels tables (chat_sessions, agent_versions, background_jobs)

Revision ID: 015_chat_panels
Revises: 014_audit_triggers
Create Date: 2026-01-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '015_chat_panels'
down_revision = '014_audit_triggers'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Chat Sessions Table
    # ===========================================
    op.create_table(
        'chat_sessions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', UUID(as_uuid=True), nullable=True),
        sa.Column('workflow_id', UUID(as_uuid=True), nullable=True),
        sa.Column('context_type', sa.String(20), nullable=False, server_default='agent'),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('messages', JSONB, nullable=False, server_default='[]'),
        sa.Column('session_metadata', JSONB, nullable=False, server_default='{}'),
        sa.Column('is_draft', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('is_pinned', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('message_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'],
                                name='fk_chat_sessions_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name='fk_chat_sessions_user', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['custom_agents.id'],
                                name='fk_chat_sessions_agent', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'],
                                name='fk_chat_sessions_workflow', ondelete='SET NULL'),
    )

    # Indexes for chat_sessions
    op.create_index('ix_chat_sessions_user_agent',
                    'chat_sessions', ['user_id', 'agent_id'])
    op.create_index('ix_chat_sessions_user_workflow',
                    'chat_sessions', ['user_id', 'workflow_id'])
    op.create_index('ix_chat_sessions_updated',
                    'chat_sessions', ['updated_at'])

    # ===========================================
    # 2. Agent Versions Table
    # ===========================================
    op.create_table(
        'agent_versions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('agent_id', UUID(as_uuid=True), nullable=False),
        sa.Column('version', sa.String(20), nullable=False),  # '1.0.0', '1.0.1', etc.
        sa.Column('config', JSONB, nullable=False),  # Snapshot of agent config
        sa.Column('is_deployed', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('deployed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deployed_by', UUID(as_uuid=True), nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'],
                                name='fk_agent_versions_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['custom_agents.id'],
                                name='fk_agent_versions_agent', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['deployed_by'], ['users.id'],
                                name='fk_agent_versions_deployed_by', ondelete='SET NULL'),

        # Unique constraint: one version per agent
        sa.UniqueConstraint('agent_id', 'version', name='uq_agent_version'),
    )

    # Indexes for agent_versions
    op.create_index('ix_agent_versions_agent_deployed',
                    'agent_versions', ['agent_id', 'is_deployed'])
    op.create_index('ix_agent_versions_created',
                    'agent_versions', ['created_at'])

    # ===========================================
    # 3. Background Jobs Table
    # ===========================================
    op.create_table(
        'background_jobs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('workflow_id', UUID(as_uuid=True), nullable=True),
        sa.Column('agent_id', UUID(as_uuid=True), nullable=True),
        sa.Column('job_type', sa.String(50), nullable=False),  # 'workflow_execution', 'agent_chat', etc.
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('progress', sa.Integer, nullable=False, server_default='0'),  # 0-100
        sa.Column('input_data', JSONB, nullable=True),
        sa.Column('result', JSONB, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('execution_trace', JSONB, nullable=False, server_default='[]'),  # Step-by-step trace
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'],
                                name='fk_background_jobs_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name='fk_background_jobs_user', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'],
                                name='fk_background_jobs_workflow', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['agent_id'], ['custom_agents.id'],
                                name='fk_background_jobs_agent', ondelete='SET NULL'),

        # Check constraint for status
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name='ck_background_jobs_status'
        ),
    )

    # Indexes for background_jobs
    op.create_index('ix_background_jobs_user_status',
                    'background_jobs', ['user_id', 'status'])
    op.create_index('ix_background_jobs_created',
                    'background_jobs', ['created_at'])

    # ===========================================
    # 4. Enable RLS on all new tables
    # ===========================================
    for table in ['chat_sessions', 'agent_versions', 'background_jobs']:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

        # SELECT policy
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            FOR SELECT
            TO oniva_app
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

        # INSERT policy
        op.execute(f"""
            CREATE POLICY tenant_insert_{table} ON {table}
            FOR INSERT
            TO oniva_app
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

        # UPDATE policy
        op.execute(f"""
            CREATE POLICY tenant_update_{table} ON {table}
            FOR UPDATE
            TO oniva_app
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

        # DELETE policy
        op.execute(f"""
            CREATE POLICY tenant_delete_{table} ON {table}
            FOR DELETE
            TO oniva_app
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

    # ===========================================
    # 5. Create updated_at trigger for chat_sessions
    # ===========================================
    op.execute("""
        CREATE OR REPLACE FUNCTION update_chat_sessions_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER chat_sessions_updated_at
            BEFORE UPDATE ON chat_sessions
            FOR EACH ROW
            EXECUTE FUNCTION update_chat_sessions_updated_at();
    """)


def downgrade() -> None:
    # Drop trigger
    op.execute("DROP TRIGGER IF EXISTS chat_sessions_updated_at ON chat_sessions")
    op.execute("DROP FUNCTION IF EXISTS update_chat_sessions_updated_at()")

    # Drop policies and tables
    for table in ['background_jobs', 'agent_versions', 'chat_sessions']:
        op.execute(f"DROP POLICY IF EXISTS tenant_delete_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_update_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_insert_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")

    # Drop indexes
    op.drop_index('ix_background_jobs_created')
    op.drop_index('ix_background_jobs_user_status')
    op.drop_index('ix_agent_versions_created')
    op.drop_index('ix_agent_versions_agent_deployed')
    op.drop_index('ix_chat_sessions_updated')
    op.drop_index('ix_chat_sessions_user_workflow')
    op.drop_index('ix_chat_sessions_user_agent')

    # Drop tables
    op.drop_table('background_jobs')
    op.drop_table('agent_versions')
    op.drop_table('chat_sessions')
