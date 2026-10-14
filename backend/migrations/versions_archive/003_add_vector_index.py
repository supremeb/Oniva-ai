"""Fix embedding column type and add vector index

Revision ID: 003_add_vector_index
Revises: 002_add_bm25_support
Create Date: 2025-12-21 20:30:00.000000

This migration:
1. Fixes the embedding column from UUID to vector(1536)
2. Creates an HNSW index for fast vector similarity search
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '003_add_vector_index'
down_revision = '002_add_bm25_support'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pgvector extension exists
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    
    # Check if embedding column is UUID type and convert to vector
    # This handles the case where initial schema created embedding as UUID
    op.execute('''
        DO $$
        BEGIN
            -- Check if the column is UUID type and alter if so
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'documents' 
                AND column_name = 'embedding' 
                AND data_type = 'uuid'
            ) THEN
                -- Drop the UUID column and recreate as vector
                ALTER TABLE documents DROP COLUMN IF EXISTS embedding;
                ALTER TABLE documents ADD COLUMN embedding vector(1536);
            END IF;
            
            -- If column doesn't exist at all, add it
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'documents' 
                AND column_name = 'embedding'
            ) THEN
                ALTER TABLE documents ADD COLUMN embedding vector(1536);
            END IF;
        END $$;
    ''')
    
    # Create HNSW index for fast approximate nearest neighbor search
    # Parameters: m=16 (connections per layer), ef_construction=64 (construction quality)
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_documents_embedding_hnsw 
        ON documents 
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    ''')


def downgrade() -> None:
    # Drop the HNSW index
    op.execute('DROP INDEX IF EXISTS idx_documents_embedding_hnsw')
    
    # Note: We don't convert back to UUID as that would lose data
    # The column remains as vector type
