"""Add agent_accuracy_history table for Agent Studio metrics

Revision ID: 021_agent_accuracy_history
Revises: 020_agent_test_cases
Create Date: 2026-01-10

Part of Phase 2A: Agent Studio
Tracks accuracy metrics over time for trend analysis and deploy gates.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '021_agent_accuracy_history'
down_revision = '020_agent_test_cases'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Agent Accuracy History Table
    # ===========================================
    op.create_table(
        'agent_accuracy_history',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),

        # Agent reference
        sa.Column('agent_id', UUID(as_uuid=True), nullable=False),
        sa.Column('agent_version_id', UUID(as_uuid=True), nullable=True),

        # Accuracy metrics
        sa.Column('accuracy', sa.Float, nullable=False),  # 0.0 to 1.0
        sa.Column('total_tests', sa.Integer, nullable=False),
        sa.Column('passed_tests', sa.Integer, nullable=False),
        sa.Column('failed_tests', sa.Integer, nullable=False),

        # Breakdown by category
        # Format: {category: {accuracy: float, passed: int, failed: int}}
        sa.Column('category_breakdown', JSONB, nullable=True),

        # Execution details
        sa.Column('evaluation_run_id', UUID(as_uuid=True), nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),

        # Comparison to previous
        sa.Column('previous_accuracy', sa.Float, nullable=True),
        sa.Column('accuracy_delta', sa.Float, nullable=True),  # Current - Previous

        # Timestamps
        sa.Column('evaluated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['custom_agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_version_id'], ['agent_versions.id'], ondelete='SET NULL'),
    )

    # Indexes for common queries
    op.create_index(
        'idx_agent_accuracy_agent_time',
        'agent_accuracy_history',
        ['agent_id', 'evaluated_at'],
        postgresql_using='btree',
        postgresql_ops={'evaluated_at': 'DESC'}
    )
    op.create_index(
        'idx_agent_accuracy_version',
        'agent_accuracy_history',
        ['agent_version_id']
    )

    # ===========================================
    # 2. Enable Row Level Security
    # ===========================================
    op.execute("""
        ALTER TABLE agent_accuracy_history ENABLE ROW LEVEL SECURITY;
    """)

    # Create RLS policy for tenant isolation
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON agent_accuracy_history
            FOR ALL
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
    """)


def downgrade() -> None:
    # Drop RLS policies first
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON agent_accuracy_history;")

    # Drop indexes
    op.drop_index('idx_agent_accuracy_version', table_name='agent_accuracy_history')
    op.drop_index('idx_agent_accuracy_agent_time', table_name='agent_accuracy_history')

    # Drop table
    op.drop_table('agent_accuracy_history')
