"""Automation run binding (fleet/workflows on engine, U1).

Revision ID: 120_engine_runs
Revises: 113_retire_review_tickets

Adds nullable ``engine_conversations.run_kind`` / ``run_ref_id`` (the domain row an archived
automation conversation belongs to), an index on them, and nullable
``workflow_executions.engine_conversation_id``. Idempotent: the baseline builds from live models,
so a fresh database may already have the columns. Plan 003 owns migrations 120-129; the
coordinator rebases only ``down_revision`` when Phase 5 lands.
"""
from alembic import op

revision = "120_engine_runs"
down_revision = "113_retire_review_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE engine_conversations ADD COLUMN IF NOT EXISTS run_kind varchar(20)")
    op.execute("ALTER TABLE engine_conversations ADD COLUMN IF NOT EXISTS run_ref_id uuid")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_engine_conversations_run "
        "ON engine_conversations (run_kind, run_ref_id)"
    )
    op.execute("ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS engine_conversation_id uuid")


def downgrade() -> None:
    op.execute("ALTER TABLE workflow_executions DROP COLUMN IF EXISTS engine_conversation_id")
    op.execute("DROP INDEX IF EXISTS ix_engine_conversations_run")
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS run_ref_id")
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS run_kind")
