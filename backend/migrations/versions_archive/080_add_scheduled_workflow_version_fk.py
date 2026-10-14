"""Add workflow_version_id FK to scheduled_workflows.

Allows pinning scheduled executions to a published version instead of
always using the latest draft spec.

Revision ID: 080_scheduled_version_fk
Revises: 079_workflow_composite_idx
Create Date: 2026-03-15

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "080_scheduled_version_fk"
down_revision = "079_workflow_composite_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_workflows",
        sa.Column(
            "workflow_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_scheduled_workflows_version_id",
        "scheduled_workflows",
        "workflow_versions",
        ["workflow_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_scheduled_workflows_version_id",
        "scheduled_workflows",
        ["workflow_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_workflows_version_id", table_name="scheduled_workflows")
    op.drop_constraint(
        "fk_scheduled_workflows_version_id",
        "scheduled_workflows",
        type_="foreignkey",
    )
    op.drop_column("scheduled_workflows", "workflow_version_id")
