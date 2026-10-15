"""Add indexes for workflow_version_id (non-blocking)

Revision ID: 052b_add_workflow_version_indexes
Revises: 052a_add_workflow_version_column
Create Date: 2026-01-30

Uses to avoid blocking reads/writes during index creation.
IMPORTANT: This migration MUST be run separately with autocommit enabled.
"""
from alembic import op

revision = '052b_add_workflow_version_indexes'
down_revision = '052a_add_workflow_version_column'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # End alembic transaction to allow CONCURRENTLY
    op.execute("COMMIT")

    # PRIMARY INDEX: Composite index for RLS-filtered queries
    # Uses ix_ prefix for codebase consistency
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_background_jobs_tenant_version_created
        ON background_jobs (tenant_id, workflow_version_id, created_at DESC)
    """)

    # SECONDARY INDEX: Version-only for FK lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_background_jobs_workflow_version
        ON background_jobs (workflow_version_id)
    """)


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute("DROP INDEX IF EXISTS ix_background_jobs_workflow_version")
    op.execute("DROP INDEX IF EXISTS ix_background_jobs_tenant_version_created")
