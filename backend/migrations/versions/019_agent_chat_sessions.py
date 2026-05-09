"""Add agent_chat_sessions table for Agent Studio chat testing

Revision ID: 019_agent_chat_sessions
Revises: 018_workflow_checkpoints
Create Date: 2026-01-10

Part of Phase 2A: Agent Studio
Enables chat-based testing of agents with message history and feedback collection.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '019_agent_chat_sessions'
down_revision = '018_workflow_checkpoints'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Agent Chat Sessions Table
    # ===========================================
    op.create_table(
        'agent_chat_sessions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),

        # Agent reference
        sa.Column('agent_id', UUID(as_uuid=True), nullable=False),
        sa.Column('agent_version_id', UUID(as_uuid=True), nullable=True),

        # Session state
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('status', sa.String(50), server_default='active'),

        # Messages stored as JSONB array
        # Format: [{
        #   id: uuid,
        #   role: 'user' | 'assistant',
        #   content: string,
        #   timestamp: iso_datetime,
        #   feedback: 'positive' | 'negative' | null,
        #   test_case_id: uuid | null,
        #   tokens_used: int,
        #   latency_ms: int
        # }]
        sa.Column('messages', JSONB, nullable=False, server_default='[]'),

        # Session metrics
        sa.Column('total_messages', sa.Integer, server_default='0'),
        sa.Column('total_tokens_used', sa.Integer, server_default='0'),
        sa.Column('total_latency_ms', sa.Integer, server_default='0'),
        sa.Column('positive_feedback_count', sa.Integer, server_default='0'),
        sa.Column('negative_feedback_count', sa.Integer, server_default='0'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['custom_agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_version_id'], ['agent_versions.id'], ondelete='SET NULL'),
    )

    # Indexes for common queries
    op.create_index(
        'idx_agent_chat_sessions_agent',
        'agent_chat_sessions',
        ['agent_id']
    )
    op.create_index(
        'idx_agent_chat_sessions_user_tenant',
        'agent_chat_sessions',
        ['user_id', 'tenant_id']
    )
    op.create_index(
        'idx_agent_chat_sessions_updated',
        'agent_chat_sessions',
        ['updated_at'],
        postgresql_using='btree',
        postgresql_ops={'updated_at': 'DESC'}
    )

    # ===========================================
    # 2. Enable Row Level Security
    # ===========================================
    op.execute("""
        ALTER TABLE agent_chat_sessions ENABLE ROW LEVEL SECURITY;
    """)

    # Create RLS policy for tenant isolation
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON agent_chat_sessions
            FOR ALL
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
    """)


def downgrade() -> None:
    # Drop RLS policies first
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON agent_chat_sessions;")

    # Drop indexes
    op.drop_index('idx_agent_chat_sessions_updated', table_name='agent_chat_sessions')
    op.drop_index('idx_agent_chat_sessions_user_tenant', table_name='agent_chat_sessions')
    op.drop_index('idx_agent_chat_sessions_agent', table_name='agent_chat_sessions')

    # Drop table
    op.drop_table('agent_chat_sessions')
