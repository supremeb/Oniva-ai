"""Add tenant_id to users table

Revision ID: 009_users_tenant_id
Revises: 008_add_tenants
Create Date: 2026-01-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '009_users_tenant_id'
down_revision = '008_add_tenants'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add tenant_id column (nullable first for migration)
    op.add_column('users', sa.Column('tenant_id', UUID(as_uuid=True), nullable=True))

    # Add auth-related columns
    op.add_column('users', sa.Column('role', sa.String(50), server_default='analyst', nullable=False))
    op.add_column('users', sa.Column('auth_provider', sa.String(50), server_default='stub', nullable=False))
    op.add_column('users', sa.Column('external_id', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('name', sa.String(255), nullable=True))

    # Migrate existing demo user to demo tenant
    op.execute("""
        UPDATE users
        SET tenant_id = '11111111-1111-1111-1111-111111111111',
            role = 'admin',
            name = 'Demo Admin'
        WHERE id = '00000000-0000-0000-0000-000000000000'
    """)

    # Set any remaining users without tenant to demo tenant
    op.execute("""
        UPDATE users
        SET tenant_id = '11111111-1111-1111-1111-111111111111'
        WHERE tenant_id IS NULL
    """)

    # Make tenant_id NOT NULL after migration
    op.alter_column('users', 'tenant_id', nullable=False)

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_users_tenant_id',
        'users', 'tenants',
        ['tenant_id'], ['id'],
        ondelete='CASCADE'
    )

    # Add unique constraint for email within tenant
    # First drop the existing unique constraint on email
    op.drop_constraint('users_email_key', 'users', type_='unique')
    op.create_unique_constraint('uq_users_tenant_email', 'users', ['tenant_id', 'email'])

    # Add index for tenant lookups
    op.create_index('ix_users_tenant_id', 'users', ['tenant_id'])

    # Add index for auth provider lookups
    op.create_index('ix_users_auth_provider_external_id', 'users', ['auth_provider', 'external_id'])


def downgrade() -> None:
    op.drop_index('ix_users_auth_provider_external_id')
    op.drop_index('ix_users_tenant_id')
    op.drop_constraint('uq_users_tenant_email', 'users', type_='unique')
    op.create_unique_constraint('users_email_key', 'users', ['email'])
    op.drop_constraint('fk_users_tenant_id', 'users', type_='foreignkey')
    op.drop_column('users', 'name')
    op.drop_column('users', 'external_id')
    op.drop_column('users', 'auth_provider')
    op.drop_column('users', 'role')
    op.drop_column('users', 'tenant_id')
