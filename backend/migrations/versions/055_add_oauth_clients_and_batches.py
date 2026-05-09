"""Add OAuth clients and workflow batches tables

Enterprise features:
1. OAuth 2.0 Client Credentials for machine-to-machine auth
2. Workflow batches for bulk execution (1000+ workflows)

Revision ID: 055_oauth_and_batches
Revises: 054_secure_rls_password
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB

revision = '055_oauth_and_batches'
down_revision = '054_secure_rls_password'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create batch_status enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'batch_status') THEN
                CREATE TYPE batch_status AS ENUM (
                    'queued', 'running', 'completed', 'partially_failed', 'failed', 'cancelled'
                );
            END IF;
        END $$;
    """)

    # Create batch_item_status enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'batch_item_status') THEN
                CREATE TYPE batch_item_status AS ENUM (
                    'pending', 'running', 'completed', 'failed', 'skipped'
                );
            END IF;
        END $$;
    """)

    # Create oauth_clients table
    op.create_table(
        'oauth_clients',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('client_id', sa.String(64), unique=True, nullable=False),
        sa.Column('client_secret_hash', sa.String(256), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('scopes', ARRAY(sa.String), server_default='{}', nullable=False),
        sa.Column('is_active', sa.Boolean, server_default='true', nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_ip', sa.String(45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Indexes for oauth_clients
    op.create_index('idx_oauth_clients_tenant_id', 'oauth_clients', ['tenant_id'])
    op.create_index('idx_oauth_clients_client_id', 'oauth_clients', ['client_id'])
    op.create_index('idx_oauth_clients_is_active', 'oauth_clients', ['is_active'])

    # Create workflow_batches table
    op.create_table(
        'workflow_batches',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('workflow_id', UUID(as_uuid=True), sa.ForeignKey('workflows.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('parallel_limit', sa.Integer, server_default='10', nullable=False),
        sa.Column('total_count', sa.Integer, server_default='0', nullable=False),
        sa.Column('completed_count', sa.Integer, server_default='0', nullable=False),
        sa.Column('failed_count', sa.Integer, server_default='0', nullable=False),
        sa.Column('pending_count', sa.Integer, server_default='0', nullable=False),
        sa.Column('status', sa.Enum('queued', 'running', 'completed', 'partially_failed', 'failed', 'cancelled',
                                     name='batch_status', create_type=False), server_default='queued', nullable=False),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('webhook_url', sa.String(2048), nullable=True),
        sa.Column('webhook_secret', sa.String(256), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Indexes for workflow_batches
    op.create_index('idx_workflow_batches_tenant_id', 'workflow_batches', ['tenant_id'])
    op.create_index('idx_workflow_batches_status', 'workflow_batches', ['status'])
    op.create_index('idx_workflow_batches_workflow_id', 'workflow_batches', ['workflow_id'])
    op.create_index('idx_workflow_batches_created_at', 'workflow_batches', ['created_at'])

    # Create workflow_batch_items table
    op.create_table(
        'workflow_batch_items',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('batch_id', UUID(as_uuid=True), sa.ForeignKey('workflow_batches.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sequence', sa.Integer, server_default='0', nullable=False),
        sa.Column('inputs', JSONB, server_default='{}', nullable=False),
        sa.Column('status', sa.Enum('pending', 'running', 'completed', 'failed', 'skipped',
                                     name='batch_item_status', create_type=False), server_default='pending', nullable=False),
        sa.Column('results', JSONB, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('execution_time_ms', sa.Integer, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Indexes for workflow_batch_items
    op.create_index('idx_workflow_batch_items_batch_id', 'workflow_batch_items', ['batch_id'])
    op.create_index('idx_workflow_batch_items_status', 'workflow_batch_items', ['status'])
    op.create_index('idx_workflow_batch_items_batch_seq', 'workflow_batch_items', ['batch_id', 'sequence'])

    # Enable RLS on new tables
    for table in ['oauth_clients', 'workflow_batches', 'workflow_batch_items']:
        op.execute(f"""
            DO $$
            BEGIN
                ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE {table} FORCE ROW LEVEL SECURITY;

                DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};
            END $$;
        """)

    # RLS for oauth_clients (tenant-scoped)
    op.execute("""
        CREATE POLICY tenant_isolation_oauth_clients ON oauth_clients
        FOR ALL
        TO oniva_app
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)

    # RLS for workflow_batches (tenant-scoped)
    op.execute("""
        CREATE POLICY tenant_isolation_workflow_batches ON workflow_batches
        FOR ALL
        TO oniva_app
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)

    # RLS for workflow_batch_items (via batch's tenant)
    op.execute("""
        CREATE POLICY tenant_isolation_workflow_batch_items ON workflow_batch_items
        FOR ALL
        TO oniva_app
        USING (
            batch_id IN (
                SELECT id FROM workflow_batches
                WHERE tenant_id = current_setting('app.current_tenant', true)::uuid
            )
        )
        WITH CHECK (
            batch_id IN (
                SELECT id FROM workflow_batches
                WHERE tenant_id = current_setting('app.current_tenant', true)::uuid
            )
        );
    """)


def downgrade() -> None:
    # Drop RLS policies
    for table in ['oauth_clients', 'workflow_batches', 'workflow_batch_items']:
        op.execute(f"""
            DO $$
            BEGIN
                DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};
                ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
            END $$;
        """)

    # Drop tables (cascade handles foreign keys)
    op.drop_table('workflow_batch_items')
    op.drop_table('workflow_batches')
    op.drop_table('oauth_clients')

    # Drop enums
    op.execute("DROP TYPE IF EXISTS batch_item_status")
    op.execute("DROP TYPE IF EXISTS batch_status")
