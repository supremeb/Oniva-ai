"""Add workflow_builder_model to llm_providers

Revision ID: 075_add_workflow_builder_model
Revises: 074_update_scheduled_workflows_for_daemon
Create Date: 2026-02-25

Changes:
- Add workflow_builder_model column for configuring the AI workflow builder model
"""
from alembic import op
import sqlalchemy as sa

revision = '075_add_workflow_builder_model'
down_revision = '074_update_scheduled_workflows_for_daemon'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'llm_providers',
        sa.Column('workflow_builder_model', sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('llm_providers', 'workflow_builder_model')
