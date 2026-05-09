"""Add scheduled workflows tables

Revision ID: 034_add_scheduled_workflows
Revises: cde44ff38bc1
Create Date: 2026-01-21

Part of Workflow Nodes & Features Expansion.
Implements:
- scheduled_workflows table for cron/interval/one-time scheduling
- scheduled_workflow_executions table for tracking execution history
- Row Level Security for multi-tenancy
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '034_add_scheduled_workflows'
down_revision = 'cde44ff38bc1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Create schedule_type enum
    # ===========================================
    op.execute("""
        CREATE TYPE schedule_type AS ENUM ('cron', 'interval', 'one_time');
    """)

    # ===========================================
    # 2. Create schedule_status enum
    # ===========================================
    op.execute("""
        CREATE TYPE schedule_status AS ENUM ('active', 'paused', 'disabled', 'completed', 'error');
    """)

    # ===========================================
    # 3. Create scheduled_workflows table
    # ===========================================
    op.create_table(
        'scheduled_workflows',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('workflow_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),

        # Schedule configuration
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('schedule_type', sa.Enum('cron', 'interval', 'one_time',
                                           name='schedule_type', create_type=False),
                  nullable=False, server_default='cron'),

        # Cron expression (for CRON type)
        sa.Column('cron_expression', sa.String(100), nullable=True),

        # Interval settings (for INTERVAL type)
        sa.Column('interval_seconds', sa.Integer, nullable=True),

        # One-time execution (for ONE_TIME type)
        sa.Column('scheduled_time', sa.DateTime(timezone=True), nullable=True),

        # Timezone
        sa.Column('timezone', sa.String(50), server_default='UTC'),

        # Status and control
        sa.Column('status', sa.Enum('active', 'paused', 'disabled', 'completed', 'error',
                                    name='schedule_status', create_type=False),
                  server_default='active', index=True),
        sa.Column('is_enabled', sa.Boolean, server_default='true', index=True),

        # Input variables
        sa.Column('input_variables', JSONB, server_default='{}'),

        # Execution limits
        sa.Column('max_executions', sa.Integer, nullable=True),
        sa.Column('execution_count', sa.Integer, server_default='0'),
        sa.Column('max_concurrent', sa.Integer, server_default='1'),
        sa.Column('timeout_seconds', sa.Integer, server_default='3600'),

        # Error handling
        sa.Column('retry_on_failure', sa.Boolean, server_default='false'),
        sa.Column('max_retries', sa.Integer, server_default='3'),
        sa.Column('retry_delay_seconds', sa.Integer, server_default='60'),

        # Last execution tracking
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_status', sa.String(50), nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True, index=True),

        # APScheduler job ID
        sa.Column('apscheduler_job_id', sa.String(255), nullable=True, unique=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Foreign keys
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    )

    # Additional indexes for common queries
    op.create_index(
        'idx_scheduled_workflows_workflow',
        'scheduled_workflows',
        ['workflow_id']
    )
    op.create_index(
        'idx_scheduled_workflows_next_run',
        'scheduled_workflows',
        ['is_enabled', 'next_run_at'],
        postgresql_where=sa.text("is_enabled = true")
    )
    op.create_index(
        'idx_scheduled_workflows_tenant_status',
        'scheduled_workflows',
        ['tenant_id', 'status']
    )

    # ===========================================
    # 4. Create scheduled_workflow_executions table
    # ===========================================
    op.create_table(
        'scheduled_workflow_executions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('schedule_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('workflow_execution_id', UUID(as_uuid=True), nullable=True),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),

        # Execution details
        sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(50), server_default='pending', index=True),

        # Results
        sa.Column('result_summary', JSONB, server_default='{}'),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('retry_count', sa.Integer, server_default='0'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Foreign keys
        sa.ForeignKeyConstraint(['schedule_id'], ['scheduled_workflows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workflow_execution_id'], ['workflow_executions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    )

    # Indexes for execution history queries
    op.create_index(
        'idx_scheduled_executions_schedule_status',
        'scheduled_workflow_executions',
        ['schedule_id', 'status']
    )
    op.create_index(
        'idx_scheduled_executions_scheduled_for',
        'scheduled_workflow_executions',
        ['scheduled_for']
    )

    # ===========================================
    # 5. Enable Row Level Security
    # ===========================================
    op.execute("""
        ALTER TABLE scheduled_workflows ENABLE ROW LEVEL SECURITY;
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_scheduled_workflows ON scheduled_workflows
            FOR ALL
            TO oniva_app
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            );
    """)
    op.execute("ALTER TABLE scheduled_workflows FORCE ROW LEVEL SECURITY;")

    op.execute("""
        ALTER TABLE scheduled_workflow_executions ENABLE ROW LEVEL SECURITY;
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_scheduled_executions ON scheduled_workflow_executions
            FOR ALL
            TO oniva_app
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            );
    """)
    op.execute("ALTER TABLE scheduled_workflow_executions FORCE ROW LEVEL SECURITY;")

    # ===========================================
    # 6. Add audit triggers
    # ===========================================
    op.execute("""
        CREATE TRIGGER audit_scheduled_workflows_trigger
            AFTER INSERT OR UPDATE OR DELETE ON scheduled_workflows
            FOR EACH ROW EXECUTE FUNCTION audit_memory_trigger_func();
    """)
    op.execute("""
        CREATE TRIGGER audit_scheduled_executions_trigger
            AFTER INSERT OR UPDATE OR DELETE ON scheduled_workflow_executions
            FOR EACH ROW EXECUTE FUNCTION audit_memory_trigger_func();
    """)

    # ===========================================
    # 7. Create helper function for updating next_run_at
    # ===========================================
    op.execute("""
        CREATE OR REPLACE FUNCTION update_scheduled_workflow_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER update_scheduled_workflows_timestamp
            BEFORE UPDATE ON scheduled_workflows
            FOR EACH ROW EXECUTE FUNCTION update_scheduled_workflow_timestamp();
    """)


def downgrade() -> None:
    # Drop triggers
    op.execute("DROP TRIGGER IF EXISTS update_scheduled_workflows_timestamp ON scheduled_workflows;")
    op.execute("DROP FUNCTION IF EXISTS update_scheduled_workflow_timestamp();")
    op.execute("DROP TRIGGER IF EXISTS audit_scheduled_executions_trigger ON scheduled_workflow_executions;")
    op.execute("DROP TRIGGER IF EXISTS audit_scheduled_workflows_trigger ON scheduled_workflows;")

    # Drop RLS policies
    op.execute("DROP POLICY IF EXISTS tenant_isolation_scheduled_executions ON scheduled_workflow_executions;")
    op.execute("ALTER TABLE scheduled_workflow_executions DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_scheduled_workflows ON scheduled_workflows;")
    op.execute("ALTER TABLE scheduled_workflows DISABLE ROW LEVEL SECURITY;")

    # Drop tables
    op.drop_table('scheduled_workflow_executions')
    op.drop_table('scheduled_workflows')

    # Drop enums
    op.execute("DROP TYPE IF EXISTS schedule_status;")
    op.execute("DROP TYPE IF EXISTS schedule_type;")
