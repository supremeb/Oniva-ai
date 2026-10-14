"""Create knowledge management tables (knowledge_bases, knowledge_documents, knowledge_chunks)
and add knowledge_base_ids to custom_agents.

Part of Frontier-Inspired Platform Simplification (Phase C).

Revision ID: 070_create_knowledge_tables
Revises: 069_update_episodic_memory_rls
Create Date: 2026-02-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector

revision = '070_create_knowledge_tables'
down_revision = '069_update_episodic_memory_rls'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- knowledge_bases ---
    op.create_table(
        'knowledge_bases',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('team_id', UUID(as_uuid=True), sa.ForeignKey('teams.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='empty'),
        sa.Column('document_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('total_chunks', sa.Integer, nullable=False, server_default='0'),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_knowledge_base_name'),
    )

    # RLS for knowledge_bases
    op.execute("ALTER TABLE knowledge_bases ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_knowledge_bases ON knowledge_bases
            FOR ALL
            TO oniva_app
            USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)

    # --- knowledge_documents ---
    op.create_table(
        'knowledge_documents',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('knowledge_base_id', UUID(as_uuid=True), sa.ForeignKey('knowledge_bases.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('filename', sa.String(500), nullable=False),
        sa.Column('file_type', sa.String(20), nullable=False),
        sa.Column('file_size_bytes', sa.BigInteger, nullable=False),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('status', sa.String(20), nullable=False, server_default='uploading'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('chunk_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('storage_path', sa.String(1000), nullable=False),
        sa.Column('uploaded_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_knowledge_doc_kb_filename', 'knowledge_documents', ['knowledge_base_id', 'filename'])

    # RLS for knowledge_documents
    op.execute("ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_knowledge_documents ON knowledge_documents
            FOR ALL
            TO oniva_app
            USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)

    # --- knowledge_chunks ---
    op.create_table(
        'knowledge_chunks',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('document_id', UUID(as_uuid=True), sa.ForeignKey('knowledge_documents.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('knowledge_base_id', UUID(as_uuid=True), sa.ForeignKey('knowledge_bases.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('chunk_index', sa.Integer, nullable=False),
        sa.Column('embedding', Vector(1536), nullable=True),
        sa.Column('chunk_metadata', JSONB, server_default='{}'),
    )

    # HNSW vector index for knowledge_chunks
    op.execute("""
        CREATE INDEX ix_knowledge_chunks_embedding ON knowledge_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    """)

    # RLS for knowledge_chunks
    op.execute("ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_knowledge_chunks ON knowledge_chunks
            FOR ALL
            TO oniva_app
            USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)

    # --- Add knowledge_base_ids to custom_agents ---
    op.add_column('custom_agents', sa.Column('knowledge_base_ids', sa.ARRAY(UUID(as_uuid=True)), server_default='{}'))


def downgrade() -> None:
    # Remove knowledge_base_ids from custom_agents
    op.drop_column('custom_agents', 'knowledge_base_ids')

    # Drop tables in reverse order (respects foreign keys)
    op.execute("DROP POLICY IF EXISTS tenant_isolation_knowledge_chunks ON knowledge_chunks;")
    op.drop_table('knowledge_chunks')

    op.execute("DROP POLICY IF EXISTS tenant_isolation_knowledge_documents ON knowledge_documents;")
    op.drop_table('knowledge_documents')

    op.execute("DROP POLICY IF EXISTS tenant_isolation_knowledge_bases ON knowledge_bases;")
    op.drop_table('knowledge_bases')
