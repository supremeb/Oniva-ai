"""Add tenant_id to workflow_executions table.

Revision ID: 031_add_tenant_id_to_workflow_executions
Revises: 030_enhance_workflow_executions
Create Date: 2025-01-16

Adds the tenant_id column for multi-tenancy support.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic
revision = '031_add_tenant_id_to_workflow_executions'
down_revision = '030_enhance_workflow_executions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add tenant_id column with a default value (will be updated later)
    op.add_column(
        'workflow_executions',
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=True)
    )

    # Set tenant_id from related workflow or use default tenant
    op.execute("""
        UPDATE workflow_executions we
        SET tenant_id = COALESCE(
            (SELECT w.tenant_id FROM workflows w WHERE w.id = we.workflow_id),
            '11111111-1111-1111-1111-111111111111'::uuid
        )
        WHERE tenant_id IS NULL
    """)

    # Make tenant_id not nullable after setting defaults
    op.alter_column('workflow_executions', 'tenant_id', nullable=False)

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_workflow_executions_tenant_id',
        'workflow_executions',
        'tenants',
        ['tenant_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Add index for tenant_id
    op.create_index('ix_workflow_executions_tenant_id', 'workflow_executions', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_workflow_executions_tenant_id', table_name='workflow_executions')
    op.drop_constraint('fk_workflow_executions_tenant_id', 'workflow_executions', type_='foreignkey')
    op.drop_column('workflow_executions', 'tenant_id')
