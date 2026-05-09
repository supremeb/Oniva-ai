"""Add data classification support

Revision ID: 036_add_data_classification
Revises: 035_add_workflow_versions
Create Date: 2026-01-23

Phase 1: Security Foundation - Data Classification.
Implements:
- max_classification column on workflow_executions
- internal_endpoint_whitelist table for approved internal endpoints
- Row Level Security for multi-tenancy

Security Features:
- Classification levels: public, internal, confidential
- Whitelist requires approval workflow (dual-control)
- Full audit trail for whitelist changes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '036_add_data_classification'
down_revision = '035_add_workflow_versions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ===========================================
    # 1. Add max_classification to workflow_executions (if not exists)
    # ===========================================
    # Check if column exists
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'workflow_executions' AND column_name = 'max_classification'
    """))
    if not result.fetchone():
        op.add_column(
            'workflow_executions',
            sa.Column(
                'max_classification',
                sa.String(20),
                nullable=False,
                server_default='internal'
            )
        )

        # Add check constraint for valid classification values
        op.create_check_constraint(
            'ck_workflow_executions_classification',
            'workflow_executions',
            "max_classification IN ('public', 'internal', 'confidential')"
        )

    # ===========================================
    # 2. Create internal_endpoint_whitelist table (if not exists)
    # ===========================================
    # Check if table exists
    result = conn.execute(sa.text("""
        SELECT to_regclass('public.internal_endpoint_whitelist')
    """))
    table_exists = result.fetchone()[0] is not None

    if not table_exists:
        op.create_table(
            'internal_endpoint_whitelist',
            sa.Column('id', UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text('gen_random_uuid()')),
            sa.Column('hostname', sa.String(500), nullable=False, unique=True),
            sa.Column('description', sa.Text, nullable=True),
            sa.Column('tenant_id', UUID(as_uuid=True),
                      sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('created_by', UUID(as_uuid=True),
                      sa.ForeignKey('users.id', ondelete='SET NULL'),
                      nullable=False),
            sa.Column('approved_by', UUID(as_uuid=True),
                      sa.ForeignKey('users.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('is_active', sa.Boolean, default=False, nullable=False),
            sa.Column('deactivated_by', UUID(as_uuid=True),
                      sa.ForeignKey('users.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('deactivation_reason', sa.Text, nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
        )

    # Create indexes for efficient queries (IF NOT EXISTS)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_internal_endpoint_whitelist_hostname
        ON internal_endpoint_whitelist (hostname)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_internal_endpoint_whitelist_active
        ON internal_endpoint_whitelist (is_active, approved_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_internal_endpoint_whitelist_tenant
        ON internal_endpoint_whitelist (tenant_id, is_active)
    """)

    # ===========================================
    # 3. Row Level Security for internal_endpoint_whitelist
    # ===========================================
    # Enable RLS (idempotent)
    op.execute("""
        ALTER TABLE internal_endpoint_whitelist ENABLE ROW LEVEL SECURITY;
    """)

    # Policy for tenant isolation (DROP IF EXISTS first)
    op.execute("""
        DROP POLICY IF EXISTS internal_endpoint_whitelist_tenant_policy
        ON internal_endpoint_whitelist;
    """)
    op.execute("""
        CREATE POLICY internal_endpoint_whitelist_tenant_policy
        ON internal_endpoint_whitelist
        FOR ALL
        USING (
            tenant_id IS NULL  -- Global entries visible to all
            OR tenant_id = current_setting('app.current_tenant_id', true)::uuid
        )
        WITH CHECK (
            tenant_id IS NULL
            OR tenant_id = current_setting('app.current_tenant_id', true)::uuid
        );
    """)

    # Grant access to app user (skip if role doesn't exist)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'oniva_app') THEN
                GRANT SELECT, INSERT, UPDATE ON internal_endpoint_whitelist TO oniva_app;
            END IF;
        END
        $$;
    """)

    # ===========================================
    # 4. Add index on workflow_executions.max_classification (if column exists)
    # ===========================================
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'workflow_executions' AND column_name = 'max_classification'
    """))
    if result.fetchone():
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_workflow_executions_max_classification
            ON workflow_executions (max_classification)
        """)


def downgrade() -> None:
    # ===========================================
    # Reverse all changes
    # ===========================================

    # Drop index on workflow_executions
    op.drop_index('ix_workflow_executions_max_classification', table_name='workflow_executions')

    # Revoke app user access
    op.execute("""
        REVOKE SELECT, INSERT, UPDATE ON internal_endpoint_whitelist FROM oniva_app;
    """)

    # Drop RLS policy
    op.execute("""
        DROP POLICY IF EXISTS internal_endpoint_whitelist_tenant_policy
        ON internal_endpoint_whitelist;
    """)

    # Disable RLS
    op.execute("""
        ALTER TABLE internal_endpoint_whitelist DISABLE ROW LEVEL SECURITY;
    """)

    # Drop indexes
    op.drop_index('ix_internal_endpoint_whitelist_tenant', table_name='internal_endpoint_whitelist')
    op.drop_index('ix_internal_endpoint_whitelist_active', table_name='internal_endpoint_whitelist')
    op.drop_index('ix_internal_endpoint_whitelist_hostname', table_name='internal_endpoint_whitelist')

    # Drop internal_endpoint_whitelist table
    op.drop_table('internal_endpoint_whitelist')

    # Drop check constraint
    op.drop_constraint('ck_workflow_executions_classification', 'workflow_executions')

    # Drop max_classification column
    op.drop_column('workflow_executions', 'max_classification')
