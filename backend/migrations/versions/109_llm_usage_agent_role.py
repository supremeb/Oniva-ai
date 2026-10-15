"""Agent role on the LLM ledger (Deep Work on engine, U10).

Revision ID: 109_llm_usage_agent_role
Revises: 108_deep_work_run_chat_id

Adds nullable ``llm_usage_records.agent_role`` (varchar 64): the subagent tool name that made a
child-conversation call (``research_worker``, ``reflector``, ``output_agent``); null for the root
conversation and for legacy rows. Idempotent: the baseline builds from live models, so a fresh
database may already have the column.
"""
from alembic import op

revision = "109_llm_usage_agent_role"
down_revision = "108_deep_work_run_chat_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_usage_records ADD COLUMN IF NOT EXISTS agent_role varchar(64)")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_usage_records DROP COLUMN IF EXISTS agent_role")
