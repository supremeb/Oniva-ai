"""Chat selections on engine conversations (chat-on-engine plan, U3).

Revision ID: 103a_engine_conversation_selections
Revises: 102_engine_interfaces

Adds ``engine_conversations.selections`` (jsonb). The gateway reads the selected knowledge
base ids from it so the model cannot widen the scope of ``search_knowledge``. Idempotent: the
baseline builds from live models, so a fresh database may already have the column.
"""
from alembic import op

revision = "103a_engine_conversation_selections"
down_revision = "102_engine_interfaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE engine_conversations ADD COLUMN IF NOT EXISTS selections jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS selections")
