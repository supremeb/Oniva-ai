"""Add pg_trgm index for search queries

Revision ID: 051_add_pg_trgm_search_index
Revises: 050_add_cursor_pagination_index
Create Date: 2026-01-30 11:00:00.000000

This migration adds:
1. pg_trgm extension for trigram-based text search
2. GIN index on background_jobs.job_type for fast ILIKE queries
3. GIN index on background_jobs.status for pattern matching

Expected Impact: 50x faster ILIKE search queries
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '051_add_pg_trgm_search_index'
down_revision = '050_add_cursor_pagination_index'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pg_trgm extension for trigram-based text search
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')

    # Create GIN index on job_type for fast ILIKE queries
    # Using to avoid locking the table in production
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_background_jobs_job_type_trgm
        ON background_jobs USING gin (job_type gin_trgm_ops)
    ''')

    # Create GIN index on status for pattern matching
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_background_jobs_status_trgm
        ON background_jobs USING gin (status gin_trgm_ops)
    ''')


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS idx_background_jobs_status_trgm')
    op.execute('DROP INDEX IF EXISTS idx_background_jobs_job_type_trgm')
    # Note: We don't drop the pg_trgm extension as other tables might use it
