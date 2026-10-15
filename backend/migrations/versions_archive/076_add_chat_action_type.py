"""Add 'chat' to episodic_memories action_type constraint

Revision ID: 076_add_chat_action_type
Revises: 075_add_workflow_builder_model
Create Date: 2026-02-28

Changes:
- Drop and recreate ck_episodic_memories_action_type to include 'chat'
"""
from alembic import op

revision = '076_add_chat_action_type'
down_revision = '075_add_workflow_builder_model'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('ck_episodic_memories_action_type', 'episodic_memories', type_='check')
    op.create_check_constraint(
        'ck_episodic_memories_action_type',
        'episodic_memories',
        "action_type IN ('analysis', 'compliance_check', 'recommendation', 'research', 'chat', 'other')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_episodic_memories_action_type', 'episodic_memories', type_='check')
    op.create_check_constraint(
        'ck_episodic_memories_action_type',
        'episodic_memories',
        "action_type IN ('analysis', 'compliance_check', 'recommendation', 'research', 'other')",
    )
