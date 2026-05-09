"""Add composite index on workflows (user_id, tenant_id, updated_at DESC).

Optimizes the list_workflows query which filters by user_id + tenant_id
and sorts by updated_at DESC.

Revision ID: 079_workflow_composite_idx
Revises: 078_composite_indexes
Create Date: 2026-03-15

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "079_workflow_composite_idx"
down_revision = "078_composite_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workflows_user_tenant_updated
            ON workflows (user_id, tenant_id, updated_at DESC);
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_workflows_user_tenant_updated;")
