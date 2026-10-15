"""Deep Work runs remember their originating chat.

Revision ID: 108_deep_work_run_chat_id
Revises: 107_deep_work_runs

Adds nullable ``deep_work_runs.chat_id`` (uuid, no FK: it is a legacy chat session id or an engine chat
conversation id) with an index, so a refreshed chat finds its run. RLS is unchanged (row-level, by tenant).
Idempotent: the baseline builds from live models, so a fresh database may already have the column.
"""
from alembic import op

revision = "108_deep_work_run_chat_id"
down_revision = "107_deep_work_runs"
branch_labels = None
depends_on = None

TABLE = "deep_work_runs"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS chat_id uuid")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_chat_id ON {TABLE} (chat_id)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS ix_{TABLE}_chat_id")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS chat_id")
