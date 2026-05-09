"""Drop agent_id foreign key constraint from agent_chat_sessions

Revision ID: 027_drop_agent_chat_sessions_fk
Revises: 026_fix_users_schema
Create Date: 2026-01-15

This migration removes the foreign key constraint on agent_chat_sessions.agent_id
to allow both custom agents (UUIDs) and built-in agents (deterministic UUIDs from strings).

Built-in agents like 'analysis', 'data-retrieval', etc. don't exist in the custom_agents
table, so the FK constraint prevents creating chat sessions for them.
"""
from alembic import op


revision = '027_drop_agent_chat_sessions_fk'
down_revision = '026_fix_users_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the foreign key constraint on agent_id
    # PostgreSQL auto-generates constraint name as: {table}_{column}_fkey
    op.drop_constraint(
        'agent_chat_sessions_agent_id_fkey',
        'agent_chat_sessions',
        type_='foreignkey'
    )


def downgrade() -> None:
    # Re-add the foreign key constraint
    op.create_foreign_key(
        'agent_chat_sessions_agent_id_fkey',
        'agent_chat_sessions',
        'custom_agents',
        ['agent_id'],
        ['id'],
        ondelete='CASCADE'
    )
