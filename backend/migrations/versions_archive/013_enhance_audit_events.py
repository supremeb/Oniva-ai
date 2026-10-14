"""Enhance audit events for compliance

Revision ID: 013_audit_events
Revises: 012_stub_users
Create Date: 2026-01-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET

revision = '013_audit_events'
down_revision = '012_stub_users'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create new audit_events table
    op.create_table(
        'audit_events',
        # Primary key
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Tenant isolation
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),

        # Actor information
        sa.Column('user_id', UUID(as_uuid=True), nullable=True),  # NULL for system events
        sa.Column('user_email', sa.String(255), nullable=True),

        # Event details
        sa.Column('action', sa.String(50), nullable=False),  # CREATE, UPDATE, DELETE, EXECUTE
        sa.Column('resource_type', sa.String(50), nullable=False),  # workflow, agent, etc.
        sa.Column('resource_id', UUID(as_uuid=True), nullable=False),
        sa.Column('resource_name', sa.String(255), nullable=True),

        # Change data
        sa.Column('changes', JSONB, nullable=True),  # {before: {}, after: {}}
        sa.Column('metadata', JSONB, server_default='{}', nullable=False),

        # Request context
        sa.Column('ip_address', INET, nullable=True),
        sa.Column('user_agent', sa.Text, nullable=True),

        # Timestamp (immutable after insert)
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Foreign keys (no CASCADE - keep audit even if user/tenant deleted)
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_audit_events_tenant'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_audit_events_user'),
    )

    # Indexes for efficient querying
    op.create_index('ix_audit_events_tenant_created',
                    'audit_events', ['tenant_id', 'created_at'])
    op.create_index('ix_audit_events_resource',
                    'audit_events', ['resource_type', 'resource_id'])
    op.create_index('ix_audit_events_user',
                    'audit_events', ['user_id'])
    op.create_index('ix_audit_events_action',
                    'audit_events', ['action'])

    # IMMUTABILITY: Create triggers to prevent updates/deletes
    op.execute("""
        -- Create trigger function to prevent updates/deletes
        CREATE OR REPLACE FUNCTION prevent_audit_modification()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit events cannot be modified or deleted';
        END;
        $$ LANGUAGE plpgsql;

        -- Trigger on UPDATE
        CREATE TRIGGER audit_events_no_update
            BEFORE UPDATE ON audit_events
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_modification();

        -- Trigger on DELETE
        CREATE TRIGGER audit_events_no_delete
            BEFORE DELETE ON audit_events
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_modification();
    """)

    # Enable RLS on audit_events
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")

    # SELECT policy for tenant isolation
    op.execute("""
        CREATE POLICY tenant_isolation_audit_events ON audit_events
        FOR SELECT
        TO oniva_app
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # INSERT policy allows app to create audit records
    op.execute("""
        CREATE POLICY audit_insert ON audit_events
        FOR INSERT
        TO oniva_app
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_insert ON audit_events")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_audit_events ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_modification()")
    op.drop_index('ix_audit_events_action')
    op.drop_index('ix_audit_events_user')
    op.drop_index('ix_audit_events_resource')
    op.drop_index('ix_audit_events_tenant_created')
    op.drop_table('audit_events')
