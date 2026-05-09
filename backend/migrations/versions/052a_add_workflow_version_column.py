"""Add workflow_version_id column and FK to background_jobs table

Revision ID: 052a_add_workflow_version_column
Revises: 051_add_pg_trgm_search_index
Create Date: 2026-01-30

NULL workflow_version_id means: legacy execution before versioning, or version was deleted.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '052a_add_workflow_version_column'
down_revision = '051_add_pg_trgm_search_index'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add workflow_version_id column (nullable for backward compat)
    op.add_column('background_jobs', sa.Column(
        'workflow_version_id',
        UUID(as_uuid=True),
        nullable=True
    ))

    # Add foreign key constraint with SET NULL (preserve logs if version deleted)
    op.create_foreign_key(
        'fk_background_jobs_workflow_version',
        'background_jobs',
        'workflow_versions',
        ['workflow_version_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # INTEGRITY: Trigger to enforce BOTH tenant AND workflow matching
    # Prevents cross-tenant/cross-workflow data corruption if RLS is bypassed
    op.execute("""
        CREATE OR REPLACE FUNCTION check_background_job_version_integrity()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.workflow_version_id IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM workflow_versions
                    WHERE id = NEW.workflow_version_id
                    AND tenant_id = NEW.tenant_id
                    AND workflow_id = NEW.workflow_id
                ) THEN
                    RAISE EXCEPTION 'workflow_version_id must belong to same tenant AND workflow as background_job';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_background_job_version_integrity
            BEFORE INSERT OR UPDATE OF workflow_version_id ON background_jobs
            FOR EACH ROW
            WHEN (NEW.workflow_version_id IS NOT NULL)
            EXECUTE FUNCTION check_background_job_version_integrity();
    """)


def downgrade() -> None:
    # 1. Drop trigger first
    op.execute("DROP TRIGGER IF EXISTS trg_background_job_version_integrity ON background_jobs;")

    # 2. Drop function
    op.execute("DROP FUNCTION IF EXISTS check_background_job_version_integrity();")

    # 3. Drop FK constraint
    op.drop_constraint('fk_background_jobs_workflow_version', 'background_jobs', type_='foreignkey')

    # 4. Drop column last
    op.drop_column('background_jobs', 'workflow_version_id')
