"""Add hybrid search support for episodic memories.

Adds:
- importance: Float (0.0 - 1.0) for memory ranking
- archived: Boolean for soft archival
- last_accessed: DateTime for LRU tracking
- access_count: Integer for popularity tracking
- search_vector: TSVector for BM25 full-text search

Revision ID: 045_add_memory_hybrid_search
Revises: 044_add_signup_bypass_policies
Create Date: 2026-01-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR

# revision identifiers, used by Alembic.
revision = '045_add_memory_hybrid_search'
down_revision = '044_signup_bypass'
branch_labels = None
depends_on = None


def upgrade():
    """Add hybrid search columns to episodic_memories table."""
    # Add importance scoring columns
    op.add_column('episodic_memories',
        sa.Column('importance', sa.Float, nullable=False, server_default='0.5')
    )
    op.add_column('episodic_memories',
        sa.Column('archived', sa.Boolean, nullable=False, server_default='false')
    )
    op.add_column('episodic_memories',
        sa.Column('last_accessed', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('episodic_memories',
        sa.Column('access_count', sa.Integer, nullable=False, server_default='0')
    )

    # Add tsvector column for BM25 search
    op.add_column('episodic_memories',
        sa.Column('search_vector', TSVECTOR, nullable=True)
    )

    # Create GIN index for full-text search
    op.create_index(
        'idx_episodic_memories_search_vector',
        'episodic_memories',
        ['search_vector'],
        postgresql_using='gin'
    )

    # Create index for importance-based queries
    op.create_index(
        'idx_episodic_memories_importance',
        'episodic_memories',
        ['importance']
    )

    # Create index for archived filter
    op.create_index(
        'idx_episodic_memories_archived',
        'episodic_memories',
        ['archived']
    )

    # Create trigger to auto-update search_vector on INSERT/UPDATE
    # Uses context_decision column (which is the combined context + decision text)
    op.execute("""
        CREATE OR REPLACE FUNCTION update_episodic_memory_search_vector()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english', coalesce(NEW.context_decision, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER tsvector_update_trigger
        BEFORE INSERT OR UPDATE ON episodic_memories
        FOR EACH ROW EXECUTE FUNCTION update_episodic_memory_search_vector();
    """)

    # Populate search_vector for existing rows
    op.execute("""
        UPDATE episodic_memories
        SET search_vector = to_tsvector('english', coalesce(context_decision, ''))
        WHERE search_vector IS NULL;
    """)


def downgrade():
    """Remove hybrid search columns from episodic_memories table."""
    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS tsvector_update_trigger ON episodic_memories")
    op.execute("DROP FUNCTION IF EXISTS update_episodic_memory_search_vector()")

    # Drop indexes
    op.drop_index('idx_episodic_memories_archived', table_name='episodic_memories')
    op.drop_index('idx_episodic_memories_importance', table_name='episodic_memories')
    op.drop_index('idx_episodic_memories_search_vector', table_name='episodic_memories')

    # Drop columns
    op.drop_column('episodic_memories', 'search_vector')
    op.drop_column('episodic_memories', 'access_count')
    op.drop_column('episodic_memories', 'last_accessed')
    op.drop_column('episodic_memories', 'archived')
    op.drop_column('episodic_memories', 'importance')
