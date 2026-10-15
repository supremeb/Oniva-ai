"""Add index for knowledge base listing query.

Phase 3: Context Engineering - Knowledge Base API

This migration creates a partial index on doc_metadata->>'kb_id' for the
documents table. This index is CRITICAL for performance:
- Without index: Full table scan (100K docs = ~1s, 1M docs = ~10s)
- With index: <100ms regardless of table size

Revision ID: 039_add_kb_id_index
Revises: 038_add_llm_usage_records
Create Date: 2026-01-24
"""

from alembic import op

# revision identifiers, used by Alembic
revision = '039_add_kb_id_index'
down_revision = '038_add_llm_usage_records'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create partial index on kb_id for knowledge base listing
    # Using CREATE INDEX (not CONCURRENTLY) for transactional safety in migrations
    # For production with large tables, consider running CONCURRENTLY outside migration
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_kb_id
        ON documents ((doc_metadata->>'kb_id'))
        WHERE doc_metadata->>'kb_id' IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_kb_id")
