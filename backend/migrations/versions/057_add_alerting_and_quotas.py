"""Add alerting rules, notification channels, and usage quotas

Enterprise feature: Proactive failure alerts and usage quota enforcement.

- AlertRule: Define triggers for notifications (execution_failed, high_failure_rate, sla_breach)
- NotificationChannel: Define delivery channels (email, slack, webhook)
- UsageQuota: Enforce LLM token/execution limits per user/team/org

Revision ID: 057_alerting_and_quotas
Revises: 056_workflow_permissions
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

revision = '057_alerting_and_quotas'
down_revision = '056_workflow_permissions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Notification Channels Table ---
    op.create_table(
        'notification_channels',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('channel_type', sa.String(50), nullable=False),  # 'email', 'slack', 'webhook', 'teams'
        sa.Column('config', JSONB, nullable=False),  # Channel-specific config (encrypted webhook URLs, etc.)
        sa.Column('is_active', sa.Boolean, default=True, nullable=False),
        sa.Column('is_verified', sa.Boolean, default=False, nullable=False),  # For email/slack verification
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_index('idx_notification_channels_tenant', 'notification_channels', ['tenant_id'])
    op.create_index('idx_notification_channels_type', 'notification_channels', ['channel_type'])

    # --- Alert Rules Table ---
    op.create_table(
        'alert_rules',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        # Trigger configuration
        sa.Column('trigger_type', sa.String(50), nullable=False),  # 'execution_failed', 'high_failure_rate', 'sla_breach', 'quota_exceeded'
        sa.Column('trigger_config', JSONB, nullable=False),  # {"failure_rate": 0.1, "window_minutes": 60} or {"threshold_percent": 80}
        # Scope (optional - null means all workflows)
        sa.Column('workflow_id', UUID(as_uuid=True), sa.ForeignKey('workflows.id', ondelete='CASCADE'), nullable=True),
        sa.Column('scope_type', sa.String(20), nullable=True),  # 'workflow', 'user', 'all'
        sa.Column('scope_id', sa.String(255), nullable=True),
        # Notification targets
        sa.Column('notification_channel_ids', ARRAY(UUID(as_uuid=True)), nullable=False),
        # Cooldown to prevent alert storms
        sa.Column('cooldown_minutes', sa.Integer, default=15, nullable=False),
        sa.Column('last_triggered_at', sa.DateTime(timezone=True), nullable=True),
        # Status
        sa.Column('is_active', sa.Boolean, default=True, nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_index('idx_alert_rules_tenant', 'alert_rules', ['tenant_id'])
    op.create_index('idx_alert_rules_trigger_type', 'alert_rules', ['trigger_type'])
    op.create_index('idx_alert_rules_workflow', 'alert_rules', ['workflow_id'])
    op.create_index('idx_alert_rules_active', 'alert_rules', ['is_active'])

    # --- Alert History Table ---
    op.create_table(
        'alert_history',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('alert_rule_id', UUID(as_uuid=True), sa.ForeignKey('alert_rules.id', ondelete='CASCADE'), nullable=False),
        sa.Column('execution_id', UUID(as_uuid=True), sa.ForeignKey('workflow_executions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('workflow_id', UUID(as_uuid=True), sa.ForeignKey('workflows.id', ondelete='SET NULL'), nullable=True),
        # Alert details
        sa.Column('trigger_type', sa.String(50), nullable=False),
        sa.Column('trigger_data', JSONB, nullable=True),  # Context about why alert triggered
        sa.Column('message', sa.Text, nullable=False),
        # Delivery tracking
        sa.Column('notification_status', sa.String(20), default='pending', nullable=False),  # 'pending', 'sent', 'failed'
        sa.Column('notification_error', sa.Text, nullable=True),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index('idx_alert_history_tenant', 'alert_history', ['tenant_id'])
    op.create_index('idx_alert_history_rule', 'alert_history', ['alert_rule_id'])
    op.create_index('idx_alert_history_created', 'alert_history', ['created_at'])

    # --- Usage Quotas Table ---
    op.create_table(
        'usage_quotas',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        # Quota scope
        sa.Column('scope_type', sa.String(20), nullable=False),  # 'tenant', 'user', 'team'
        sa.Column('scope_id', UUID(as_uuid=True), nullable=False),  # tenant_id, user_id, or team_id
        # Quota type and limits
        sa.Column('quota_type', sa.String(50), nullable=False),  # 'llm_tokens', 'executions', 'storage_bytes'
        sa.Column('limit_value', sa.BigInteger, nullable=False),  # Max allowed
        sa.Column('current_usage', sa.BigInteger, default=0, nullable=False),
        # Period
        sa.Column('period', sa.String(20), nullable=False),  # 'daily', 'weekly', 'monthly', 'annual', 'lifetime'
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),  # null for lifetime
        # Alert thresholds
        sa.Column('warning_threshold', sa.Float, default=0.8, nullable=False),  # 80% triggers warning
        sa.Column('warning_sent', sa.Boolean, default=False, nullable=False),
        # Status
        sa.Column('is_active', sa.Boolean, default=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        # Unique per scope/type/period
        sa.UniqueConstraint('scope_type', 'scope_id', 'quota_type', 'period', 'period_start',
                           name='uq_usage_quota_scope_type_period')
    )

    op.create_index('idx_usage_quotas_tenant', 'usage_quotas', ['tenant_id'])
    op.create_index('idx_usage_quotas_scope', 'usage_quotas', ['scope_type', 'scope_id'])
    op.create_index('idx_usage_quotas_type', 'usage_quotas', ['quota_type'])
    op.create_index('idx_usage_quotas_active', 'usage_quotas', ['is_active'])

    # --- Usage History Table (for tracking usage over time) ---
    op.create_table(
        'usage_history',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('quota_id', UUID(as_uuid=True), sa.ForeignKey('usage_quotas.id', ondelete='CASCADE'), nullable=False),
        # Usage event
        sa.Column('scope_type', sa.String(20), nullable=False),
        sa.Column('scope_id', UUID(as_uuid=True), nullable=False),
        sa.Column('quota_type', sa.String(50), nullable=False),
        sa.Column('amount', sa.BigInteger, nullable=False),
        # Context
        sa.Column('execution_id', UUID(as_uuid=True), nullable=True),
        sa.Column('workflow_id', UUID(as_uuid=True), nullable=True),
        sa.Column('metadata', JSONB, nullable=True),  # Additional context
        # Timestamp
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index('idx_usage_history_tenant', 'usage_history', ['tenant_id'])
    op.create_index('idx_usage_history_quota', 'usage_history', ['quota_id'])
    op.create_index('idx_usage_history_scope', 'usage_history', ['scope_type', 'scope_id'])
    op.create_index('idx_usage_history_created', 'usage_history', ['created_at'])

    # --- Enable RLS on all new tables ---
    for table in ['notification_channels', 'alert_rules', 'alert_history', 'usage_quotas', 'usage_history']:
        op.execute(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        """)

        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            FOR ALL
            TO oniva_app
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
        """)

    # --- Check constraints ---
    op.execute("""
        ALTER TABLE notification_channels
        ADD CONSTRAINT chk_channel_type CHECK (channel_type IN ('email', 'slack', 'webhook', 'teams', 'pagerduty'));
    """)

    op.execute("""
        ALTER TABLE alert_rules
        ADD CONSTRAINT chk_trigger_type CHECK (trigger_type IN ('execution_failed', 'high_failure_rate', 'sla_breach', 'quota_exceeded', 'custom'));
    """)

    op.execute("""
        ALTER TABLE usage_quotas
        ADD CONSTRAINT chk_scope_type CHECK (scope_type IN ('tenant', 'user', 'team'));
    """)

    op.execute("""
        ALTER TABLE usage_quotas
        ADD CONSTRAINT chk_quota_type CHECK (quota_type IN ('llm_tokens', 'executions', 'storage_bytes', 'api_calls'));
    """)

    op.execute("""
        ALTER TABLE usage_quotas
        ADD CONSTRAINT chk_period CHECK (period IN ('daily', 'weekly', 'monthly', 'annual', 'lifetime'));
    """)


def downgrade() -> None:
    # Drop RLS policies
    for table in ['usage_history', 'usage_quotas', 'alert_history', 'alert_rules', 'notification_channels']:
        op.execute(f"""
            DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};
            ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
        """)

    # Drop tables in reverse order
    op.drop_table('usage_history')
    op.drop_table('usage_quotas')
    op.drop_table('alert_history')
    op.drop_table('alert_rules')
    op.drop_table('notification_channels')
