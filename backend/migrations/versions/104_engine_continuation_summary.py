"""Continuation summary on engine conversations (chat-on-engine plan, U10 follow-up).

Revision ID: 104_engine_continuation_summary
Revises: 103_chat_read_model

Adds ``engine_conversations.continuation_summary`` (text): the summary of a continued legacy
session, composed into the agent's system prompt (the engine ignores display-only entries) and
re-included whenever selections rebuild the agent config. Idempotent: the baseline builds from
live models, so a fresh database may already have the column.
"""
from alembic import op

revision = "104_engine_continuation_summary"
down_revision = "103_chat_read_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE engine_conversations ADD COLUMN IF NOT EXISTS continuation_summary text")


def downgrade() -> None:
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS continuation_summary")
