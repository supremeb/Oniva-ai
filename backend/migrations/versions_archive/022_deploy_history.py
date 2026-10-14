"""Add deploy_history table for Agent Studio deployment tracking

Revision ID: 022_deploy_history
Revises: 021_agent_accuracy_history
Create Date: 2026-01-10

Part of Phase 2A: Agent Studio
Tracks deployment history with gate check results for audit and rollback.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '022_deploy_history'
down_revision = '021_agent_accuracy_history'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Deploy History Table
    # ===========================================
    op.create_table(
        'deploy_history',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),

        # Agent reference
        sa.Column('agent_id', UUID(as_uuid=True), nullable=False),
        sa.Column('agent_version_id', UUID(as_uuid=True), nullable=False),

        # Deploy action
        sa.Column('action', sa.String(50), nullable=False),  # 'deploy', 'rollback', 'block'
        sa.Column('status', sa.String(50), nullable=False),  # 'success', 'blocked', 'rolled_back'

        # Gate check results
        sa.Column('gate_check_passed', sa.Boolean, nullable=False),
        sa.Column('accuracy_at_deploy', sa.Float, nullable=True),
        sa.Column('accuracy_threshold', sa.Float, server_default='0.8'),  # 80% minimum
        sa.Column('accuracy_regression_allowed', sa.Float, server_default='0.05'),  # 5% max regression

        # Comparison
        sa.Column('previous_version_id', UUID(as_uuid=True), nullable=True),
        sa.Column('previous_accuracy', sa.Float, nullable=True),
        sa.Column('accuracy_delta', sa.Float, nullable=True),

        # Reason (for blocks/rollbacks)
        sa.Column('block_reason', sa.Text, nullable=True),

        # User action
        sa.Column('deployed_by', UUID(as_uuid=True), nullable=False),
        sa.Column('notes', sa.Text, nullable=True),

        # Force deploy flag (bypasses gates)
        sa.Column('force_deployed', sa.Boolean, server_default='false'),
        sa.Column('force_reason', sa.Text, nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['custom_agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_version_id'], ['agent_versions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['previous_version_id'], ['agent_versions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['deployed_by'], ['users.id'], ondelete='SET NULL'),
    )

    # Indexes for common queries
    op.create_index(
        'idx_deploy_history_agent_time',
        'deploy_history',
        ['agent_id', 'created_at'],
        postgresql_using='btree',
        postgresql_ops={'created_at': 'DESC'}
    )
    op.create_index(
        'idx_deploy_history_version',
        'deploy_history',
        ['agent_version_id']
    )

    # ===========================================
    # 2. Enable Row Level Security
    # ===========================================
    op.execute("""
        ALTER TABLE deploy_history ENABLE ROW LEVEL SECURITY;
    """)

    # Create RLS policy for tenant isolation
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON deploy_history
            FOR ALL
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
    """)


def downgrade() -> None:
    # Drop RLS policies first
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON deploy_history;")

    # Drop indexes
    op.drop_index('idx_deploy_history_version', table_name='deploy_history')
    op.drop_index('idx_deploy_history_agent_time', table_name='deploy_history')

    # Drop table
    op.drop_table('deploy_history')
