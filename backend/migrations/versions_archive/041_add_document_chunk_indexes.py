"""Add missing indexes for document chunking queries.

Phase 0: Critical Security Fixes - Performance

These indexes are required for efficient chunk retrieval:
- idx_documents_parent_document_id: Fast lookup of chunks by parent
- idx_documents_chunk_index: Ordered chunk retrieval

Without these indexes, N+1 queries in knowledge_bases.py cause
5-10s response times at 1000+ documents.

Revision ID: 041_add_document_chunk_indexes
Revises: 040_add_openapi_connector_fields
Create Date: 2026-01-24
"""

from alembic import op

# revision identifiers, used by Alembic
revision = '041_add_document_chunk_indexes'
down_revision = '040_add_openapi_connector_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index for looking up chunks by parent document
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_parent_document_id
        ON documents (parent_document_id)
        WHERE parent_document_id IS NOT NULL
    """)

    # Composite index for ordered chunk retrieval
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_chunk_index
        ON documents (parent_document_id, chunk_index)
        WHERE parent_document_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_chunk_index")
    op.execute("DROP INDEX IF EXISTS idx_documents_parent_document_id")
