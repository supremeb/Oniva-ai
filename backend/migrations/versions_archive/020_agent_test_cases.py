"""Add agent_test_cases table for Agent Studio evaluation

Revision ID: 020_agent_test_cases
Revises: 019_agent_chat_sessions
Create Date: 2026-01-10

Part of Phase 2A: Agent Studio
Stores test cases collected from chat feedback and manual creation.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '020_agent_test_cases'
down_revision = '019_agent_chat_sessions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Agent Test Cases Table
    # ===========================================
    op.create_table(
        'agent_test_cases',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),

        # Agent reference
        sa.Column('agent_id', UUID(as_uuid=True), nullable=False),

        # Test case content
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('category', sa.String(100), nullable=True),

        # Input/Output
        sa.Column('input', sa.Text, nullable=False),
        sa.Column('expected_output', sa.Text, nullable=True),
        sa.Column('context', JSONB, nullable=True),

        # Source tracking
        sa.Column('source', sa.String(50), nullable=False),  # 'chat_feedback', 'manual', 'imported'
        sa.Column('source_session_id', UUID(as_uuid=True), nullable=True),
        sa.Column('source_message_id', UUID(as_uuid=True), nullable=True),

        # Feedback type
        sa.Column('is_positive', sa.Boolean, nullable=False, server_default='true'),

        # Evaluation config
        sa.Column('evaluation_type', sa.String(50), server_default='llm_judge'),  # 'exact', 'contains', 'llm_judge'
        sa.Column('evaluation_criteria', JSONB, nullable=True),

        # Execution history
        # Format: {passed: bool, actual_output: str, score: float, evaluated_at: datetime}
        sa.Column('last_result', JSONB, nullable=True),
        sa.Column('pass_count', sa.Integer, server_default='0'),
        sa.Column('fail_count', sa.Integer, server_default='0'),

        # Status
        sa.Column('is_active', sa.Boolean, server_default='true'),
        sa.Column('is_baseline', sa.Boolean, server_default='false'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['custom_agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_session_id'], ['agent_chat_sessions.id'], ondelete='SET NULL'),
    )

    # Indexes for common queries
    op.create_index(
        'idx_agent_test_cases_agent',
        'agent_test_cases',
        ['agent_id']
    )
    op.create_index(
        'idx_agent_test_cases_active',
        'agent_test_cases',
        ['agent_id', 'is_active'],
        postgresql_where=sa.text('is_active = true')
    )
    op.create_index(
        'idx_agent_test_cases_baseline',
        'agent_test_cases',
        ['agent_id', 'is_baseline'],
        postgresql_where=sa.text('is_baseline = true')
    )
    op.create_index(
        'idx_agent_test_cases_category',
        'agent_test_cases',
        ['agent_id', 'category']
    )

    # ===========================================
    # 2. Enable Row Level Security
    # ===========================================
    op.execute("""
        ALTER TABLE agent_test_cases ENABLE ROW LEVEL SECURITY;
    """)

    # Create RLS policy for tenant isolation
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON agent_test_cases
            FOR ALL
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
    """)


def downgrade() -> None:
    # Drop RLS policies first
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON agent_test_cases;")

    # Drop indexes
    op.drop_index('idx_agent_test_cases_category', table_name='agent_test_cases')
    op.drop_index('idx_agent_test_cases_baseline', table_name='agent_test_cases')
    op.drop_index('idx_agent_test_cases_active', table_name='agent_test_cases')
    op.drop_index('idx_agent_test_cases_agent', table_name='agent_test_cases')

    # Drop table
    op.drop_table('agent_test_cases')
