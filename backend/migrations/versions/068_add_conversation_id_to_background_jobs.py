"""Add conversation_id to background_jobs for Deep Work lookup by chat.

UUID column (nullable) with composite index on (conversation_id, created_at).
Backfills existing rows from input_data->>'conversation_id' where available.

Revision ID: 068_add_conversation_id_to_background_jobs
Revises: 067_add_cache_tracking_fields
Create Date: 2026-02-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '068_add_conversation_id_to_background_jobs'
down_revision = '067_add_cache_tracking_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable conversation_id column
    op.add_column(
        'background_jobs',
        sa.Column('conversation_id', UUID(as_uuid=True), nullable=True),
    )

    # Composite index for efficient conversation lookup (ORDER BY created_at DESC)
    op.create_index(
        'ix_bgj_conversation_created',
        'background_jobs',
        ['conversation_id', 'created_at'],
    )

    # Backfill from input_data->>'conversation_id' where available
    op.execute("""
        UPDATE background_jobs
        SET conversation_id = (input_data->>'conversation_id')::uuid
        WHERE input_data->>'conversation_id' IS NOT NULL
          AND input_data->>'conversation_id' != ''
          AND conversation_id IS NULL
    """)


def downgrade() -> None:
    op.drop_index('ix_bgj_conversation_created', table_name='background_jobs')
    op.drop_column('background_jobs', 'conversation_id')
