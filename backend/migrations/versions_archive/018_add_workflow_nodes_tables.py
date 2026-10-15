"""Add workflow nodes tables for approvals, webhooks, and subworkflows

Revision ID: 018_workflow_nodes
Revises: 017_compliance
Create Date: 2026-01-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET, ARRAY

revision = '018_workflow_nodes'
down_revision = '017_compliance'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Workflow Approvals Table (Human-in-Loop)
    # ===========================================
    op.create_table(
        'workflow_approvals',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('workflow_execution_id', UUID(as_uuid=True), nullable=False),
        sa.Column('node_id', sa.String(255), nullable=False),
        sa.Column('checkpoint_id', UUID(as_uuid=True), nullable=True),

        # Approver configuration
        sa.Column('approver_type', sa.String(50), nullable=False),  # 'user', 'role', 'group', 'dynamic'
        sa.Column('approver_config', JSONB, nullable=False, server_default='{}'),
        sa.Column('assigned_user_id', UUID(as_uuid=True), nullable=True),

        # Request details
        sa.Column('title', sa.String(500), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('context_data', JSONB, nullable=True),
        sa.Column('options', JSONB, nullable=True),  # Available decisions

        # SLA tracking
        sa.Column('sla_deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('escalation_config', JSONB, nullable=True),
        sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('escalation_level', sa.Integer, server_default='0'),

        # Decision
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('decision', sa.String(100), nullable=True),
        sa.Column('decision_comment', sa.Text, nullable=True),
        sa.Column('decided_by', UUID(as_uuid=True), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),

        # Cryptographic audit trail
        sa.Column('decision_hash', sa.String(64), nullable=True),
        sa.Column('previous_hash', sa.String(64), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'],
                                name='fk_workflow_approvals_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'],
                                name='fk_workflow_approvals_assigned_user', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id'],
                                name='fk_workflow_approvals_decided_by', ondelete='SET NULL'),

        # Check constraints
        sa.CheckConstraint(
            "approver_type IN ('user', 'role', 'group', 'dynamic')",
            name='ck_workflow_approvals_approver_type'
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'escalated', 'cancelled')",
            name='ck_workflow_approvals_status'
        ),
    )

    # Indexes for workflow_approvals
    op.create_index('ix_workflow_approvals_workflow',
                    'workflow_approvals', ['workflow_execution_id'])
    op.create_index('ix_workflow_approvals_user',
                    'workflow_approvals', ['assigned_user_id', 'status'])
    op.create_index('ix_workflow_approvals_deadline',
                    'workflow_approvals', ['sla_deadline'],
                    postgresql_where=sa.text("status = 'pending'"))
    op.create_index('ix_workflow_approvals_status',
                    'workflow_approvals', ['status'])

    # ===========================================
    # 2. Webhook Endpoints Table
    # ===========================================
    op.create_table(
        'webhook_endpoints',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('workflow_id', UUID(as_uuid=True), nullable=False),
        sa.Column('node_id', sa.String(255), nullable=False),

        # Endpoint configuration
        sa.Column('endpoint_path', sa.String(255), nullable=False, unique=True),
        sa.Column('secret_key_encrypted', sa.LargeBinary, nullable=True),

        # Security settings
        sa.Column('auth_type', sa.String(50), server_default='hmac'),  # 'none', 'hmac', 'api_key', 'oauth'
        sa.Column('allowed_ips', ARRAY(INET), nullable=True),
        sa.Column('rate_limit_per_minute', sa.Integer, server_default='60'),

        # Payload configuration
        sa.Column('expected_content_type', sa.String(50), server_default='json'),
        sa.Column('payload_schema', JSONB, nullable=True),

        # Response configuration
        sa.Column('response_mode', sa.String(20), server_default='async'),  # 'sync', 'async'
        sa.Column('sync_timeout_seconds', sa.Integer, server_default='30'),

        # Idempotency settings
        sa.Column('idempotency_enabled', sa.Boolean, server_default='true'),
        sa.Column('idempotency_key_path', sa.String(255), nullable=True),
        sa.Column('idempotency_ttl_hours', sa.Integer, server_default='24'),

        # State
        sa.Column('is_active', sa.Boolean, server_default='true'),
        sa.Column('last_triggered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('trigger_count', sa.Integer, server_default='0'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'],
                                name='fk_webhook_endpoints_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'],
                                name='fk_webhook_endpoints_workflow', ondelete='CASCADE'),

        # Check constraints
        sa.CheckConstraint(
            "auth_type IN ('none', 'hmac', 'api_key', 'oauth')",
            name='ck_webhook_endpoints_auth_type'
        ),
        sa.CheckConstraint(
            "response_mode IN ('sync', 'async')",
            name='ck_webhook_endpoints_response_mode'
        ),
    )

    # Indexes for webhook_endpoints
    op.create_index('ix_webhook_endpoints_path',
                    'webhook_endpoints', ['endpoint_path'])
    op.create_index('ix_webhook_endpoints_workflow',
                    'webhook_endpoints', ['workflow_id'])
    op.create_index('ix_webhook_endpoints_active',
                    'webhook_endpoints', ['is_active'])

    # ===========================================
    # 3. Subworkflow Executions Table
    # ===========================================
    op.create_table(
        'subworkflow_executions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('parent_execution_id', UUID(as_uuid=True), nullable=False),
        sa.Column('parent_node_id', sa.String(255), nullable=False),
        sa.Column('child_workflow_id', UUID(as_uuid=True), nullable=False),
        sa.Column('child_execution_id', UUID(as_uuid=True), nullable=True),

        # Execution tracking
        sa.Column('recursion_depth', sa.Integer, nullable=False, server_default='1'),
        sa.Column('input_parameters', JSONB, nullable=True),
        sa.Column('output_data', JSONB, nullable=True),

        # State
        sa.Column('status', sa.String(50), server_default='running'),
        sa.Column('error_message', sa.Text, nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'],
                                name='fk_subworkflow_executions_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['child_workflow_id'], ['workflows.id'],
                                name='fk_subworkflow_executions_child_workflow', ondelete='CASCADE'),

        # Check constraints
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')",
            name='ck_subworkflow_executions_status'
        ),
        sa.CheckConstraint(
            "recursion_depth <= 10",
            name='ck_subworkflow_executions_max_depth'
        ),
    )

    # Indexes for subworkflow_executions
    op.create_index('ix_subworkflow_executions_parent',
                    'subworkflow_executions', ['parent_execution_id'])
    op.create_index('ix_subworkflow_executions_child',
                    'subworkflow_executions', ['child_workflow_id'])
    op.create_index('ix_subworkflow_executions_status',
                    'subworkflow_executions', ['status'])

    # ===========================================
    # 4. Enable RLS for all tables
    # ===========================================
    for table in ['workflow_approvals', 'webhook_endpoints', 'subworkflow_executions']:
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


def downgrade() -> None:
    # Drop RLS policies
    for table in ['workflow_approvals', 'webhook_endpoints', 'subworkflow_executions']:
        op.execute(f"DROP POLICY IF EXISTS tenant_delete_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_update_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_insert_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")

    # Drop indexes
    op.drop_index('ix_subworkflow_executions_status')
    op.drop_index('ix_subworkflow_executions_child')
    op.drop_index('ix_subworkflow_executions_parent')
    op.drop_index('ix_webhook_endpoints_active')
    op.drop_index('ix_webhook_endpoints_workflow')
    op.drop_index('ix_webhook_endpoints_path')
    op.drop_index('ix_workflow_approvals_status')
    op.drop_index('ix_workflow_approvals_deadline')
    op.drop_index('ix_workflow_approvals_user')
    op.drop_index('ix_workflow_approvals_workflow')

    # Drop tables
    op.drop_table('subworkflow_executions')
    op.drop_table('webhook_endpoints')
    op.drop_table('workflow_approvals')
