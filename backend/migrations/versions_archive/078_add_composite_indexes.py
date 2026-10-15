"""Add composite indexes for agent list, session list, and episodic memory queries.

Revision ID: 078_composite_indexes
Revises: 0959413d1b2f
Create Date: 2026-03-15

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "078_composite_indexes"
down_revision = "0959413d1b2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_custom_agents_tenant_user_active
            ON custom_agents (tenant_id, user_id) WHERE is_active = true;
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_custom_agents_tenant_visibility_category
            ON custom_agents (tenant_id, visibility, category);
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chat_sessions_tenant_user_updated
            ON chat_sessions (tenant_id, user_id, updated_at DESC);
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_episodic_memories_active_embedding
            ON episodic_memories USING hnsw (embedding vector_cosine_ops)
            WHERE archived = false;
        """)

    # Drop redundant index (superset by new 3-column index)
    op.execute("DROP INDEX IF EXISTS ix_custom_agents_tenant_visibility;")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_custom_agents_tenant_user_active;")
    op.execute("DROP INDEX IF EXISTS ix_custom_agents_tenant_visibility_category;")
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_tenant_user_updated;")
    op.execute("DROP INDEX IF EXISTS ix_episodic_memories_active_embedding;")
    # Recreate old index if needed
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_custom_agents_tenant_visibility
        ON custom_agents (tenant_id, visibility);
    """)
