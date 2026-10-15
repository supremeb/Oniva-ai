"""Add tenant_id column to custom_agents table

Revision ID: 028_add_tenant_id_to_custom_agents
Revises: 027_drop_agent_chat_sessions_fk
Create Date: 2026-01-15

This migration adds multi-tenancy support to the custom_agents table
by adding a tenant_id foreign key column. Existing agents are assigned
to the tenant of their creator (user).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = '028_custom_agents_tenant'
down_revision = '027_drop_agent_chat_sessions_fk'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Add tenant_id column as nullable first
    op.add_column(
        'custom_agents',
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=True)
    )

    # Step 2: Populate tenant_id from the user's tenant
    # Get tenant_id from users table based on user_id
    op.execute("""
        UPDATE custom_agents
        SET tenant_id = users.tenant_id
        FROM users
        WHERE custom_agents.user_id = users.id
    """)

    # Step 3: For any remaining NULL tenant_ids (orphaned records),
    # set to the default demo tenant
    op.execute("""
        UPDATE custom_agents
        SET tenant_id = '11111111-1111-1111-1111-111111111111'
        WHERE tenant_id IS NULL
    """)

    # Step 4: Make the column NOT NULL
    op.alter_column(
        'custom_agents',
        'tenant_id',
        nullable=False
    )

    # Step 5: Add foreign key constraint
    op.create_foreign_key(
        'custom_agents_tenant_id_fkey',
        'custom_agents',
        'tenants',
        ['tenant_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Step 6: Add index for efficient tenant-based queries
    op.create_index(
        'ix_custom_agents_tenant_id',
        'custom_agents',
        ['tenant_id']
    )


def downgrade() -> None:
    # Remove index
    op.drop_index('ix_custom_agents_tenant_id', 'custom_agents')

    # Remove foreign key
    op.drop_constraint('custom_agents_tenant_id_fkey', 'custom_agents', type_='foreignkey')

    # Remove column
    op.drop_column('custom_agents', 'tenant_id')
