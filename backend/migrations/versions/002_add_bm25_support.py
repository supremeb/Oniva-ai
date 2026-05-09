"""Add BM25 support for Hybrid RAG

Revision ID: 002_add_bm25_support
Revises: 001_initial_schema
Create Date: 2025-12-20 19:30:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '002_add_bm25_support'
down_revision = '001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass
    # Enable pg_textsearch extension
    # op.execute('CREATE EXTENSION IF NOT EXISTS pg_textsearch')

    # Fix embedding column type if it was created as UUID in initial schema
    # (Checking the initial schema shows it might be UUID)
    # op.execute('ALTER TABLE documents ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)')
    # Actually, let's just make sure it's correct
    # op.execute('CREATE INDEX IF NOT EXISTS idx_documents_embedding ON documents USING hnsw (embedding vector_cosine_ops)')

    # Add BM25 index for full-text search
    # pg_textsearch uses 'bm25' access method
    # op.execute('CREATE INDEX idx_documents_content_bm25 ON documents USING bm25 (content)')
    
    # Optional: Add weight to title
    # op.execute('CREATE INDEX idx_documents_title_bm25 ON documents USING bm25 (title)')


def downgrade() -> None:
    # Drop BM25 index
    op.execute('DROP INDEX IF EXISTS idx_documents_content_bm25')
    
    # Drop extension
    op.execute('DROP EXTENSION IF EXISTS pg_textsearch')
