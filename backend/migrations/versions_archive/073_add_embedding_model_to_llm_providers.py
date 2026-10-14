"""Add embedding_model column to llm_providers table.

Allows tenants to configure a custom embedding model per LLM provider,
used by the RAG service for knowledge base embeddings.

Revision ID: 073_add_embedding_model_to_llm_providers
Revises: a5908f9ec0db
Create Date: 2026-02-20
"""
from alembic import op
import sqlalchemy as sa

revision = '073_add_embedding_model_to_llm_providers'
down_revision = 'a5908f9ec0db'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'llm_providers',
        sa.Column('embedding_model', sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('llm_providers', 'embedding_model')
