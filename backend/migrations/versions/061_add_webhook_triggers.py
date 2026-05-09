"""Add webhook triggers for inbound event-driven automation

Enterprise feature: Allow external systems to trigger workflows via webhooks.

Supports:
- HMAC signature verification (Stripe, GitHub, etc.)
- API key authentication
- IP whitelisting
- Payload mapping (JMESPath)
- Filter expressions
- Rate limiting
- Idempotency
- Delivery tracking

Revision ID: 061_webhook_triggers
Revises: 060_cost_analytics_view
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET, ARRAY

revision = '061_webhook_triggers'
down_revision = '060_cost_analytics_view'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Webhook Triggers table
    op.create_table(
        'webhook_triggers',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('workflow_id', UUID(as_uuid=True), sa.ForeignKey('workflows.id', ondelete='CASCADE'), nullable=False),

        # Webhook URL (unique token)
        sa.Column('url_token', sa.String(64), unique=True, nullable=False),

        # Authentication
        sa.Column('auth_method', sa.String(20), nullable=False),  # 'hmac', 'api_key', 'ip_whitelist', 'none'
        sa.Column('auth_config', JSONB, nullable=False, server_default='{}'),  # Encrypted secrets
        sa.Column('allowed_ip_addresses', ARRAY(INET), nullable=True),

        # Payload processing
        sa.Column('payload_mapping', JSONB, nullable=False, server_default='{}'),  # JMESPath expressions
        sa.Column('filter_expression', sa.Text, nullable=True),  # SQL-like WHERE clause

        # Rate limiting
        sa.Column('rate_limit_per_hour', sa.Integer, nullable=False, server_default='100'),

        # Status
        sa.Column('is_enabled', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now())
    )

    op.create_index('idx_webhook_triggers_tenant', 'webhook_triggers', ['tenant_id'])
    op.create_index('idx_webhook_triggers_workflow', 'webhook_triggers', ['workflow_id'])
    op.create_index('idx_webhook_triggers_token', 'webhook_triggers', ['url_token'])

    # Webhook Deliveries table (audit log)
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('webhook_trigger_id', UUID(as_uuid=True), sa.ForeignKey('webhook_triggers.id', ondelete='CASCADE'), nullable=False),

        # Request details
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('source_ip', INET, nullable=True),
        sa.Column('request_headers', JSONB, nullable=True),
        sa.Column('request_body', JSONB, nullable=True),

        # Processing
        sa.Column('filter_matched', sa.Boolean, nullable=True),
        sa.Column('workflow_execution_id', UUID(as_uuid=True), sa.ForeignKey('workflow_executions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('response_status', sa.Integer, nullable=False),  # HTTP status code
        sa.Column('error_message', sa.Text, nullable=True),

        # Idempotency
        sa.Column('idempotency_key', sa.String(255), nullable=True),

        sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )

    op.create_index('idx_webhook_deliveries_tenant', 'webhook_deliveries', ['tenant_id'])
    op.create_index('idx_webhook_deliveries_trigger', 'webhook_deliveries', ['webhook_trigger_id'])
    op.create_index('idx_webhook_deliveries_received', 'webhook_deliveries', ['received_at'])
    op.create_index('idx_webhook_deliveries_idempotency', 'webhook_deliveries', ['webhook_trigger_id', 'idempotency_key'])

    # Enable RLS
    for table in ['webhook_triggers', 'webhook_deliveries']:
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

    # Check constraints
    op.execute("""
        ALTER TABLE webhook_triggers
        ADD CONSTRAINT chk_auth_method CHECK (auth_method IN ('hmac', 'api_key', 'ip_whitelist', 'none'));
    """)

    print("✅ Webhook triggers tables created")


def downgrade() -> None:
    for table in ['webhook_deliveries', 'webhook_triggers']:
        op.execute(f"""
            DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};
            ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
        """)

    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_triggers')
