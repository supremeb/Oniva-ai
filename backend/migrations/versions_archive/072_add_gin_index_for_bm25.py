"""Add GIN index on knowledge_chunks.content for BM25 full-text search.

Speeds up plainto_tsquery lookups used by hybrid_search_v2 RRF fusion.
Also adds a GIN index on the legacy documents table for hybrid_search.

Revision ID: 072_add_gin_index_for_bm25
Revises: 071_create_admin_and_feedback
Create Date: 2026-02-13
"""
from alembic import op

revision = '072_add_gin_index_for_bm25'
down_revision = '071_create_admin_and_feedback'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GIN index for BM25 on knowledge_chunks (used by hybrid_search_v2)
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_knowledge_chunks_content_gin "
        "ON knowledge_chunks USING gin(to_tsvector('english', content))"
    )

    # GIN index for BM25 on documents (used by legacy hybrid_search)
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_documents_content_gin "
        "ON documents USING gin(to_tsvector('english', content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_documents_content_gin")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_content_gin")
