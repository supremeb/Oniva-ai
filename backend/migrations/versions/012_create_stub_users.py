"""Create stub users for authentication

Revision ID: 012_stub_users
Revises: 011_rls_policies
Create Date: 2026-01-07
"""
from alembic import op

revision = '012_stub_users'
down_revision = '011_rls_policies'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create stub users (password not stored - stub auth uses hardcoded values)
    op.execute("""
        INSERT INTO users (id, tenant_id, email, name, role, auth_provider, is_active)
        VALUES
        ('00000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'admin@demo.local', 'Demo Admin', 'admin', 'stub', true),
        ('00000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'analyst@demo.local', 'Demo Analyst', 'analyst', 'stub', true),
        ('00000000-0000-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222', 'admin@acme.local', 'Acme Admin', 'admin', 'stub', true),
        ('00000000-0000-0000-0000-000000000004', '22222222-2222-2222-2222-222222222222', 'analyst@acme.local', 'Acme Analyst', 'analyst', 'stub', true),
        ('00000000-0000-0000-0000-000000000005', '33333333-3333-3333-3333-333333333333', 'viewer@test.local', 'Test Viewer', 'viewer', 'stub', true)
        ON CONFLICT (tenant_id, email) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM users WHERE auth_provider = 'stub'
        AND email IN ('admin@demo.local', 'analyst@demo.local', 'admin@acme.local', 'analyst@acme.local', 'viewer@test.local')
    """)
