"""Pin and archive on engine conversations (sidebar rename / delete / pin).

Revision ID: 106_engine_conversation_pin_archive
Revises: 105_llm_usage_purpose

Adds ``engine_conversations.pinned`` (bool, default false) and ``archived_at`` (nullable
timestamptz). "Delete" in the sidebar archives the row (hidden from listings); engine data is
never deleted. Idempotent: the baseline builds from live models, so a fresh database may already
have the columns.
"""
from alembic import op

revision = "106_engine_conversation_pin_archive"
down_revision = "105_llm_usage_purpose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE engine_conversations ADD COLUMN IF NOT EXISTS pinned boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE engine_conversations ADD COLUMN IF NOT EXISTS archived_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS archived_at")
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS pinned")
