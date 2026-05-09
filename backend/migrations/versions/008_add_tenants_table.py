"""Add tenants table for multi-tenancy

Revision ID: 008_add_tenants
Revises: 005_add_custom_agents
Create Date: 2026-01-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '008_add_tenants'
down_revision = '005_add_custom_agents'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tenants',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False, unique=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('settings', JSONB, server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Index for slug lookups (authentication flow)
    op.create_index('ix_tenants_slug', 'tenants', ['slug'])

    # Index for active tenant filtering
    op.create_index('ix_tenants_is_active', 'tenants', ['is_active'])

    # Create demo tenants for testing
    op.execute("""
        INSERT INTO tenants (id, name, slug, is_active, settings) VALUES
        ('11111111-1111-1111-1111-111111111111', 'Demo Organization', 'demo', true, '{"plan": "enterprise"}'),
        ('22222222-2222-2222-2222-222222222222', 'Acme Corporation', 'acme', true, '{"plan": "enterprise"}'),
        ('33333333-3333-3333-3333-333333333333', 'Test Company', 'test', true, '{"plan": "trial"}')
    """)


def downgrade() -> None:
    op.drop_index('ix_tenants_is_active')
    op.drop_index('ix_tenants_slug')
    op.drop_table('tenants')
