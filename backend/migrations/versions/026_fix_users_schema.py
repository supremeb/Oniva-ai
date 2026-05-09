"""Fix users table schema - add missing columns and seed data

This migration fixes the users table to match the expected schema with
tenant_id, role, auth_provider, name, and external_id columns.
It also seeds the demo tenants and stub users for development.

Revision ID: 026_fix_users_schema
Revises: 025_merge_skills_branch
Create Date: 2026-01-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '026_fix_users_schema'
down_revision = '025_merge_skills_branch'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if tenant_id column exists on users table
    result = conn.execute(sa.text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'tenant_id'
    """))
    has_tenant_id = result.fetchone() is not None

    # First ensure tenants exist (needed for foreign keys)
    op.execute("""
        INSERT INTO tenants (id, name, slug, is_active, settings)
        VALUES
            ('11111111-1111-1111-1111-111111111111', 'Demo Organization', 'demo', true, '{"plan": "enterprise"}'),
            ('22222222-2222-2222-2222-222222222222', 'Acme Corporation', 'acme', true, '{"plan": "enterprise"}'),
            ('33333333-3333-3333-3333-333333333333', 'Test Company', 'test', true, '{"plan": "trial"}')
        ON CONFLICT (slug) DO UPDATE SET
            name = EXCLUDED.name,
            settings = EXCLUDED.settings
    """)

    if not has_tenant_id:
        # Add missing columns to users table
        op.add_column('users', sa.Column('tenant_id', UUID(as_uuid=True), nullable=True))
        op.add_column('users', sa.Column('role', sa.String(50), server_default='analyst', nullable=False))
        op.add_column('users', sa.Column('auth_provider', sa.String(50), server_default='stub', nullable=False))
        op.add_column('users', sa.Column('external_id', sa.String(255), nullable=True))
        op.add_column('users', sa.Column('name', sa.String(255), nullable=True))

        # Update all existing users with tenant and role info
        op.execute("""
            UPDATE users
            SET tenant_id = '11111111-1111-1111-1111-111111111111',
                role = 'admin',
                name = COALESCE(name, 'Demo Admin')
            WHERE tenant_id IS NULL
        """)

        # Make tenant_id NOT NULL
        op.alter_column('users', 'tenant_id', nullable=False)

        # Add foreign key
        op.create_foreign_key(
            'fk_users_tenant_id',
            'users', 'tenants',
            ['tenant_id'], ['id'],
            ondelete='CASCADE'
        )

        # Drop old unique constraint if exists using raw SQL to avoid errors
        op.execute("""
            ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key
        """)

        op.execute("""
            DROP INDEX IF EXISTS ix_users_email
        """)

        # Create new unique constraint
        op.execute("""
            ALTER TABLE users ADD CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email)
        """)

        op.create_index('ix_users_tenant_id', 'users', ['tenant_id'])
        op.create_index('ix_users_auth_provider_external_id', 'users', ['auth_provider', 'external_id'])

    # Seed stub users (delete existing ones first to avoid conflicts, then insert)
    op.execute("""
        DELETE FROM users WHERE email IN ('admin@demo.local', 'analyst@demo.local', 'admin@acme.local', 'analyst@acme.local', 'viewer@test.local')
    """)

    op.execute("""
        INSERT INTO users (id, tenant_id, email, name, role, auth_provider, is_active, is_superuser, created_at, updated_at)
        VALUES
        ('00000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'admin@demo.local', 'Demo Admin', 'admin', 'stub', true, false, now(), now()),
        ('00000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'analyst@demo.local', 'Demo Analyst', 'analyst', 'stub', true, false, now(), now()),
        ('00000000-0000-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222', 'admin@acme.local', 'Acme Admin', 'admin', 'stub', true, false, now(), now()),
        ('00000000-0000-0000-0000-000000000004', '22222222-2222-2222-2222-222222222222', 'analyst@acme.local', 'Acme Analyst', 'analyst', 'stub', true, false, now(), now()),
        ('00000000-0000-0000-0000-000000000005', '33333333-3333-3333-3333-333333333333', 'viewer@test.local', 'Test Viewer', 'viewer', 'stub', true, false, now(), now())
    """)


def downgrade() -> None:
    # Remove stub users
    op.execute("""
        DELETE FROM users WHERE auth_provider = 'stub'
        AND email IN ('admin@demo.local', 'analyst@demo.local', 'admin@acme.local', 'analyst@acme.local', 'viewer@test.local')
    """)

    # Remove additional tenants
    op.execute("""
        DELETE FROM tenants WHERE slug IN ('acme', 'test')
    """)
