"""Add workflow permissions table for sharing

Enterprise feature: Enable workflow collaboration with granular permission
controls. Supports sharing with users, roles, and organization-wide access.

Revision ID: 056_workflow_permissions
Revises: 055_oauth_and_batches
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '056_workflow_permissions'
down_revision = '055_oauth_and_batches'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create workflow_permissions table
    op.create_table(
        'workflow_permissions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('workflow_id', UUID(as_uuid=True), sa.ForeignKey('workflows.id', ondelete='CASCADE'), nullable=False),
        # Grantee info: user UUID, role name, or 'organization' for org-wide
        sa.Column('grantee_type', sa.String(20), nullable=False),  # 'user', 'role', 'organization'
        sa.Column('grantee_id', sa.String(255), nullable=False),   # user UUID, role name, or 'organization'
        # Permission level
        sa.Column('permission', sa.String(20), nullable=False),    # 'view', 'execute', 'edit', 'admin'
        # Audit info
        sa.Column('granted_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        # Metadata
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        # Unique constraint: one permission per workflow/grantee combination
        sa.UniqueConstraint('workflow_id', 'grantee_type', 'grantee_id', name='uq_workflow_permission_grantee')
    )

    # Create indexes
    op.create_index('idx_workflow_permissions_tenant_id', 'workflow_permissions', ['tenant_id'])
    op.create_index('idx_workflow_permissions_workflow_id', 'workflow_permissions', ['workflow_id'])
    op.create_index('idx_workflow_permissions_grantee', 'workflow_permissions', ['grantee_type', 'grantee_id'])
    op.create_index('idx_workflow_permissions_expires', 'workflow_permissions', ['expires_at'],
                    postgresql_where=sa.text('expires_at IS NOT NULL'))

    # Enable RLS
    op.execute("""
        ALTER TABLE workflow_permissions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE workflow_permissions FORCE ROW LEVEL SECURITY;
    """)

    # RLS Policy: Tenant isolation
    op.execute("""
        CREATE POLICY tenant_isolation_workflow_permissions ON workflow_permissions
        FOR ALL
        TO oniva_app
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)

    # Add check constraints for valid values
    op.execute("""
        ALTER TABLE workflow_permissions
        ADD CONSTRAINT chk_grantee_type CHECK (grantee_type IN ('user', 'role', 'organization'));
    """)

    op.execute("""
        ALTER TABLE workflow_permissions
        ADD CONSTRAINT chk_permission CHECK (permission IN ('view', 'execute', 'edit', 'admin'));
    """)


def downgrade() -> None:
    # Drop RLS policy
    op.execute("""
        DROP POLICY IF EXISTS tenant_isolation_workflow_permissions ON workflow_permissions;
        ALTER TABLE workflow_permissions DISABLE ROW LEVEL SECURITY;
    """)

    # Drop table
    op.drop_table('workflow_permissions')
