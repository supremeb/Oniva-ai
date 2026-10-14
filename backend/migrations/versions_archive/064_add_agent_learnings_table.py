"""Add agent_learnings table for error-driven knowledge capture.

Adds:
- agent_learnings table with vector embedding, dedup hash, RLS
- HNSW vector index for cosine similarity search
- Composite index on (tenant_id, agent_id, status)
- RLS policy for tenant isolation

Revision ID: 064_add_agent_learnings
Revises: 063_phase_a_agent_studio
Create Date: 2026-02-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector

revision = '064_add_agent_learnings'
down_revision = '063_phase_a_agent_studio'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create agent_learnings table
    op.create_table(
        'agent_learnings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('agent_id', UUID(as_uuid=True), sa.ForeignKey('custom_agents.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('pattern_type', sa.String(100), nullable=True),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('source_session_id', UUID(as_uuid=True), nullable=True),
        sa.Column('source_error', sa.Text, nullable=True),
        sa.Column('embedding', Vector(1536), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('content_hash', sa.String(64), unique=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Composite index for filtered queries
    op.create_index(
        'ix_agent_learnings_tenant_agent_status',
        'agent_learnings',
        ['tenant_id', 'agent_id', 'status'],
    )

    # HNSW vector index for cosine similarity search
    op.execute("""
        CREATE INDEX ix_agent_learnings_embedding_hnsw
        ON agent_learnings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 100)
    """)

    # Enable Row-Level Security
    op.execute("ALTER TABLE agent_learnings ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY agent_learnings_tenant_isolation
        ON agent_learnings
        USING (tenant_id::text = current_setting('app.current_tenant', true))
    """)
    op.execute("ALTER TABLE agent_learnings FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_learnings DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS agent_learnings_tenant_isolation ON agent_learnings")
    op.execute("DROP INDEX IF EXISTS ix_agent_learnings_embedding_hnsw")
    op.drop_index('ix_agent_learnings_tenant_agent_status', table_name='agent_learnings')
    op.drop_table('agent_learnings')
