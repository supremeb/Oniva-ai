"""Add workflow_entities table for entity tracking.

Stores entities (companies, people, regulations, etc.) extracted
from workflow executions. Enables queries like "What workflows analyzed Acme Corp?"

Revision ID: 046_add_workflow_entities
Revises: 045_add_memory_hybrid_search
Create Date: 2026-01-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = '046_add_workflow_entities'
down_revision = '045_add_memory_hybrid_search'
branch_labels = None
depends_on = None


def upgrade():
    """Create workflow_entities table with indexes and RLS."""
    op.create_table(
        'workflow_entities',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('workflow_id', UUID(as_uuid=True), sa.ForeignKey('workflows.id', ondelete='CASCADE'), nullable=False),
        sa.Column('execution_id', UUID(as_uuid=True), sa.ForeignKey('workflow_executions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('properties', JSONB, server_default='{}', nullable=False),
        sa.Column('source_node_id', sa.String(100), nullable=True),
        sa.Column('confidence', sa.Float, server_default='1.0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), nullable=True),
    )

    # Indexes for efficient querying
    op.create_index(
        'idx_workflow_entities_tenant_name',
        'workflow_entities',
        ['tenant_id', 'name']
    )
    op.create_index(
        'idx_workflow_entities_type',
        'workflow_entities',
        ['entity_type']
    )
    op.create_index(
        'idx_workflow_entities_workflow',
        'workflow_entities',
        ['workflow_id']
    )
    op.create_index(
        'idx_workflow_entities_execution',
        'workflow_entities',
        ['execution_id']
    )

    # GIN index for JSONB properties search
    op.create_index(
        'idx_workflow_entities_properties',
        'workflow_entities',
        ['properties'],
        postgresql_using='gin'
    )

    # Enable RLS for multi-tenant security
    op.execute("""
        ALTER TABLE workflow_entities ENABLE ROW LEVEL SECURITY;

        -- Policy for tenant isolation
        CREATE POLICY workflow_entities_tenant_isolation ON workflow_entities
        FOR ALL
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

        -- Policy for bypass (service accounts, testing)
        CREATE POLICY workflow_entities_bypass ON workflow_entities
        FOR ALL
        USING (current_setting('app.bypass_rls', true)::boolean = true);
    """)

    # Also add extract_entities flag to workflows table
    op.add_column('workflows',
        sa.Column('extract_entities', sa.Boolean, nullable=False, server_default='true')
    )


def downgrade():
    """Remove workflow_entities table and related columns."""
    # Drop RLS policies
    op.execute("""
        DROP POLICY IF EXISTS workflow_entities_bypass ON workflow_entities;
        DROP POLICY IF EXISTS workflow_entities_tenant_isolation ON workflow_entities;
    """)

    # Drop extract_entities column from workflows
    op.drop_column('workflows', 'extract_entities')

    # Drop table (cascades indexes)
    op.drop_table('workflow_entities')
