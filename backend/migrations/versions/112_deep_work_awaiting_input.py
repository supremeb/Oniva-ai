"""Deep Work intake clarification waiting on the user (Approvals & Policy plan, U7).

Revision ID: 112_deep_work_awaiting_input
Revises: 111_tool_approvals

Adds ``deep_work_runs.awaiting_input`` (JSONB, nullable). Idempotent: the baseline builds its schema
from the live models, so a fresh database may already hold the column when this revision runs.
"""
from alembic import op

revision = "112_deep_work_awaiting_input"
down_revision = "111_tool_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE deep_work_runs ADD COLUMN IF NOT EXISTS awaiting_input jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE deep_work_runs DROP COLUMN IF EXISTS awaiting_input")
