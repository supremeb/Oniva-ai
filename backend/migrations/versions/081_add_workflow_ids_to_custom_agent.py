"""Add workflow_ids column to custom_agents.

Stores workflow UUIDs attached to an agent, enabling
workflows-as-agent-resources pattern.

Revision ID: 081_workflow_ids_custom_agent
Revises: 080_scheduled_version_fk
Create Date: 2026-04-04

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "081_workflow_ids_custom_agent"
down_revision = "080_scheduled_version_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_agents",
        sa.Column(
            "workflow_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("custom_agents", "workflow_ids")
