"""Usage purpose on the LLM ledger (chat-on-engine plan, U11).

Revision ID: 105_llm_usage_purpose
Revises: 104_engine_continuation_summary

Adds ``llm_usage_records.purpose`` (varchar 20): ``generation`` or ``compaction`` for rows posted
by the engine. Idempotent: the baseline builds from live models, so a fresh database may already
have the column.
"""
from alembic import op

revision = "105_llm_usage_purpose"
down_revision = "104_engine_continuation_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_usage_records ADD COLUMN IF NOT EXISTS purpose varchar(20)")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_usage_records DROP COLUMN IF EXISTS purpose")
