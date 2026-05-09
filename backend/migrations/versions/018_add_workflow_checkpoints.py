"""Add workflow checkpoints table for DBOS durable workflows

Revision ID: 018_workflow_checkpoints
Revises: 017_compliance
Create Date: 2026-01-09

Part of Phase 1: Production Reliability
Enables zero data loss on crash via automatic checkpointing.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '018_workflow_checkpoints'
down_revision = '017_compliance'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Workflow Checkpoints Table
    # ===========================================
    op.create_table(
        'workflow_checkpoints',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),

        # Workflow tracking
        sa.Column('workflow_uuid', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('workflow_execution_id', UUID(as_uuid=True), nullable=True),

        # Checkpoint data
        sa.Column('step_number', sa.Integer, nullable=False),
        sa.Column('step_name', sa.String(255), nullable=False),
        sa.Column('node_id', sa.String(255), nullable=False),

        # Input/Output state
        sa.Column('input_data', JSONB, nullable=False, server_default='{}'),
        sa.Column('output_data', JSONB, nullable=True),

        # Status tracking
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text, nullable=True),

        # Timing
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('elapsed_ms', sa.Integer, nullable=True),

        # Metadata
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Constraints
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workflow_execution_id'], ['workflow_executions.id'],
                               ondelete='SET NULL'),

        # Unique constraint: one checkpoint per workflow step
        sa.UniqueConstraint('workflow_uuid', 'step_number', name='uq_workflow_step'),
    )

    # Indexes for common queries
    op.create_index(
        'idx_checkpoints_workflow_uuid',
        'workflow_checkpoints',
        ['workflow_uuid']
    )
    op.create_index(
        'idx_checkpoints_status',
        'workflow_checkpoints',
        ['status']
    )
    op.create_index(
        'idx_checkpoints_tenant_workflow',
        'workflow_checkpoints',
        ['tenant_id', 'workflow_uuid']
    )

    # ===========================================
    # 2. Enable Row Level Security
    # ===========================================
    op.execute("""
        ALTER TABLE workflow_checkpoints ENABLE ROW LEVEL SECURITY;
    """)

    # Create RLS policy for tenant isolation
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON workflow_checkpoints
            FOR ALL
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
    """)

    # ===========================================
    # 3. Workflow Sessions Table
    # ===========================================
    op.create_table(
        'workflow_sessions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),

        # Workflow reference
        sa.Column('workflow_id', UUID(as_uuid=True), nullable=True),

        # Session metadata
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('status', sa.String(50), server_default='active'),

        # Filesystem path (relative to SESSION_BASE_PATH)
        sa.Column('filesystem_path', sa.String(500), nullable=False),

        # State tracking
        sa.Column('current_state', sa.String(50), nullable=True),
        sa.Column('last_checkpoint_id', UUID(as_uuid=True), nullable=True),

        # Resource tracking
        sa.Column('total_size_bytes', sa.Integer, server_default='0'),
        sa.Column('file_count', sa.Integer, server_default='0'),

        # Context tracking
        sa.Column('context_window_used', sa.Integer, server_default='0'),
        sa.Column('context_window_max', sa.Integer, server_default='128000'),

        # Cost tracking
        sa.Column('total_tokens_used', sa.Integer, server_default='0'),
        sa.Column('total_cost_usd', sa.Integer, server_default='0'),  # Cents

        # Session metadata
        sa.Column('session_metadata', JSONB, server_default='{}'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='SET NULL'),
    )

    # Enable RLS for sessions
    op.execute("""
        ALTER TABLE workflow_sessions ENABLE ROW LEVEL SECURITY;
    """)

    op.execute("""
        CREATE POLICY tenant_isolation_policy ON workflow_sessions
            FOR ALL
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
    """)


def downgrade() -> None:
    # Drop RLS policies first
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON workflow_sessions;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON workflow_checkpoints;")

    # Drop tables
    op.drop_table('workflow_sessions')
    op.drop_table('workflow_checkpoints')
