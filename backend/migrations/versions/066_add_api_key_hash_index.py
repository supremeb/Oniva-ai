"""Add SHA-256 hash column to api_keys for O(1) lookup.

Replaces O(n) decrypt-all-keys-per-request pattern with indexed
hash-based lookup. The key_hash column is nullable to allow
incremental backfill of existing keys.

Revision ID: 066_add_api_key_hash_index
Revises: 065_add_registry_fields
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa

revision = '066_add_api_key_hash_index'
down_revision = '065_add_registry_fields'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('api_keys', sa.Column('key_hash', sa.String(64), nullable=True))
    op.create_index('ix_api_keys_key_hash', 'api_keys', ['key_hash'], unique=True)


def downgrade():
    op.drop_index('ix_api_keys_key_hash', table_name='api_keys')
    op.drop_column('api_keys', 'key_hash')
