"""Add trigger field to background_jobs table

Revision ID: 029
Revises: 028
Create Date: 2026-01-16

This migration adds:
1. trigger field to track how workflow executions were initiated (manual, api, scheduled)
2. Composite indexes for efficient query performance on workflow logs
3. CHECK constraint for valid trigger values
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '029_add_background_job_trigger'
down_revision = '028_custom_agents_tenant'
branch_labels = None
depends_on = None


def upgrade():
    # Add trigger column with server_default for atomic backfill
    # Using server_default instead of Python default to avoid race conditions during deployment
    op.add_column(
        'background_jobs',
        sa.Column('trigger', sa.String(20), nullable=False, server_default='manual')
    )

    # Add composite indexes for query performance (from performance review)
    # Index for filtering by workflow and ordering by created_at (most common query pattern)
    op.create_index(
        'ix_background_jobs_workflow_created',
        'background_jobs',
        ['workflow_id', 'created_at'],
        postgresql_using='btree'
    )

    # Index for tenant-scoped status filtering (monitoring dashboard queries)
    op.create_index(
        'ix_background_jobs_tenant_status',
        'background_jobs',
        ['tenant_id', 'status'],
        postgresql_using='btree'
    )

    # Index for tenant-scoped workflow filtering with created_at ordering
    op.create_index(
        'ix_background_jobs_tenant_workflow_created',
        'background_jobs',
        ['tenant_id', 'workflow_id', 'created_at'],
        postgresql_using='btree'
    )

    # Add CHECK constraint for valid trigger values
    op.execute("""
        ALTER TABLE background_jobs
        ADD CONSTRAINT ck_background_jobs_trigger
        CHECK (trigger IN ('manual', 'api', 'scheduled'))
    """)


def downgrade():
    # Remove CHECK constraint
    op.execute("ALTER TABLE background_jobs DROP CONSTRAINT IF EXISTS ck_background_jobs_trigger")

    # Drop indexes
    op.drop_index('ix_background_jobs_tenant_workflow_created', table_name='background_jobs')
    op.drop_index('ix_background_jobs_tenant_status', table_name='background_jobs')
    op.drop_index('ix_background_jobs_workflow_created', table_name='background_jobs')

    # Remove trigger column
    op.drop_column('background_jobs', 'trigger')
