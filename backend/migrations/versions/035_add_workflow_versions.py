"""Add workflow versions table

Revision ID: 035_add_workflow_versions
Revises: 034_add_scheduled_workflows
Create Date: 2026-01-21

Part of Workflow Nodes & Features Expansion.
Implements:
- workflow_versions table for version control
- Version numbering, tagging, and deployment tracking
- Row Level Security for multi-tenancy
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '035_add_workflow_versions'
down_revision = '034_add_scheduled_workflows'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Create workflow_versions table
    # ===========================================
    op.create_table(
        'workflow_versions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('workflow_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),

        # Versioning
        sa.Column('version_number', sa.Integer, nullable=False),
        sa.Column('version_tag', sa.String(100), nullable=True),

        # Content
        sa.Column('spec', JSONB, nullable=False),
        sa.Column('spec_hash', sa.String(64), nullable=True),

        # Metadata
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=True),

        # Deployment status
        sa.Column('is_active', sa.Boolean, server_default='false', index=True),
        sa.Column('is_draft', sa.Boolean, server_default='true'),

        # Statistics
        sa.Column('node_count', sa.Integer, server_default='0'),
        sa.Column('edge_count', sa.Integer, server_default='0'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )

    # Unique constraint: one version number per workflow
    op.create_unique_constraint(
        'uq_workflow_versions_workflow_version',
        'workflow_versions',
        ['workflow_id', 'version_number']
    )

    # Index for finding active version quickly
    op.create_index(
        'idx_workflow_versions_active',
        'workflow_versions',
        ['workflow_id', 'is_active'],
        postgresql_where=sa.text("is_active = true")
    )

    # Index for version tags
    op.create_index(
        'idx_workflow_versions_tag',
        'workflow_versions',
        ['workflow_id', 'version_tag'],
        postgresql_where=sa.text("version_tag IS NOT NULL")
    )

    # Index for spec hash (for detecting duplicate specs)
    op.create_index(
        'idx_workflow_versions_spec_hash',
        'workflow_versions',
        ['workflow_id', 'spec_hash']
    )

    # ===========================================
    # 2. Enable Row Level Security
    # ===========================================
    op.execute("""
        ALTER TABLE workflow_versions ENABLE ROW LEVEL SECURITY;
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_workflow_versions ON workflow_versions
            FOR ALL
            TO oniva_app
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            );
    """)
    op.execute("ALTER TABLE workflow_versions FORCE ROW LEVEL SECURITY;")

    # ===========================================
    # 3. Add audit trigger
    # ===========================================
    op.execute("""
        CREATE TRIGGER audit_workflow_versions_trigger
            AFTER INSERT OR UPDATE OR DELETE ON workflow_versions
            FOR EACH ROW EXECUTE FUNCTION audit_memory_trigger_func();
    """)

    # ===========================================
    # 4. Create function to auto-increment version number
    # ===========================================
    op.execute("""
        CREATE OR REPLACE FUNCTION get_next_workflow_version_number(p_workflow_id UUID)
        RETURNS INTEGER AS $$
        DECLARE
            next_version INTEGER;
        BEGIN
            SELECT COALESCE(MAX(version_number), 0) + 1
            INTO next_version
            FROM workflow_versions
            WHERE workflow_id = p_workflow_id;

            RETURN next_version;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ===========================================
    # 5. Create function to deactivate other versions when one is activated
    # ===========================================
    op.execute("""
        CREATE OR REPLACE FUNCTION ensure_single_active_version()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_active = true AND (OLD IS NULL OR OLD.is_active = false) THEN
                -- Deactivate all other versions for this workflow
                UPDATE workflow_versions
                SET is_active = false
                WHERE workflow_id = NEW.workflow_id
                  AND id != NEW.id
                  AND is_active = true;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER ensure_single_active_version_trigger
            BEFORE INSERT OR UPDATE OF is_active ON workflow_versions
            FOR EACH ROW
            WHEN (NEW.is_active = true)
            EXECUTE FUNCTION ensure_single_active_version();
    """)


def downgrade() -> None:
    # Drop triggers and functions
    op.execute("DROP TRIGGER IF EXISTS ensure_single_active_version_trigger ON workflow_versions;")
    op.execute("DROP FUNCTION IF EXISTS ensure_single_active_version();")
    op.execute("DROP FUNCTION IF EXISTS get_next_workflow_version_number(UUID);")
    op.execute("DROP TRIGGER IF EXISTS audit_workflow_versions_trigger ON workflow_versions;")

    # Drop RLS policies
    op.execute("DROP POLICY IF EXISTS tenant_isolation_workflow_versions ON workflow_versions;")
    op.execute("ALTER TABLE workflow_versions DISABLE ROW LEVEL SECURITY;")

    # Drop table
    op.drop_table('workflow_versions')
