"""Enable RLS on documents table

Missing RLS policy on documents table identified in Enterprise Readiness Report.
This migration enables tenant isolation for RAG documents.

Revision ID: 059_enable_rls_documents
Revises: 058_api_key_scoping
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa

revision = '059_enable_rls_documents'
down_revision = '058_api_key_scoping'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Verify no NULL tenant_ids (would break RLS)
    connection = op.get_bind()
    result = connection.execute(sa.text("""
        SELECT COUNT(*) FROM documents WHERE tenant_id IS NULL
    """))
    null_count = result.scalar()

    if null_count > 0:
        raise ValueError(
            f"Found {null_count} documents with NULL tenant_id. "
            "Backfill tenant_id before enabling RLS:\n"
            "UPDATE documents SET tenant_id = (SELECT id FROM tenants LIMIT 1) WHERE tenant_id IS NULL;"
        )

    # Enable RLS
    op.execute("""
        ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
        ALTER TABLE documents FORCE ROW LEVEL SECURITY;
    """)

    # Create tenant isolation policy
    op.execute("""
        CREATE POLICY tenant_isolation_documents ON documents
        FOR ALL
        TO oniva_app
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)

    print("✅ RLS enabled on documents table")


def downgrade() -> None:
    op.execute("""
        DROP POLICY IF EXISTS tenant_isolation_documents ON documents;
        ALTER TABLE documents DISABLE ROW LEVEL SECURITY;
    """)
