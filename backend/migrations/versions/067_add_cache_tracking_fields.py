"""Add cache tracking columns to llm_usage_records.

Tracks provider cache token counts for cache hit rate analytics.
Nullable so existing rows retain NULL (no backfill needed).

Revision ID: 067_add_cache_tracking_fields
Revises: 066_add_api_key_hash_index
Create Date: 2026-02-07
"""
from alembic import op
import sqlalchemy as sa

revision = '067_add_cache_tracking_fields'
down_revision = '066_add_api_key_hash_index'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('llm_usage_records', sa.Column('cache_creation_tokens', sa.Integer(), nullable=True))
    op.add_column('llm_usage_records', sa.Column('cache_read_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('llm_usage_records', 'cache_read_tokens')
    op.drop_column('llm_usage_records', 'cache_creation_tokens')
