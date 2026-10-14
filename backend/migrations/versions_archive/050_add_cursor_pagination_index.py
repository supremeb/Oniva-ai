"""Add cursor pagination index for workflow logs

Revision ID: 050_add_cursor_pagination_index
Revises: 049_add_refresh_tokens
Create Date: 2026-01-30 10:00:00.000000

This migration adds:
1. Composite index on background_jobs for O(1) cursor pagination
2. Index covers (workflow_id, created_at DESC, id DESC) for efficient log queries

Expected Impact: O(1) pagination instead of O(n) offset-based queries
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '050_add_cursor_pagination_index'
down_revision = '049_refresh_tokens'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create composite index for cursor-based pagination on workflow logs
    # Query pattern: WHERE workflow_id = X AND created_at <= cursor_time ORDER BY created_at DESC, id DESC
    # Note: CONCURRENTLY removed to allow running in transaction (safe for dev, manual for prod)
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_background_jobs_cursor_pagination
        ON background_jobs (workflow_id, created_at DESC, id DESC)
    ''')

    # Also add tenant-scoped index for RLS-filtered queries
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_background_jobs_tenant_workflow_created
        ON background_jobs (tenant_id, workflow_id, created_at DESC)
    ''')


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS idx_background_jobs_tenant_workflow_created')
    op.execute('DROP INDEX IF EXISTS idx_background_jobs_cursor_pagination')
