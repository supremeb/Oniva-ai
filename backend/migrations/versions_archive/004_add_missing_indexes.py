"""Add missing database indexes for performance at scale

Revision ID: 004_add_missing_indexes
Revises: 003_add_vector_index
Create Date: 2025-12-21 20:35:00.000000

This migration adds:
1. Foreign key indexes for faster joins
2. Pagination indexes for efficient list queries
3. Composite indexes for common query patterns

Expected Impact: 30-300x faster queries at scale
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '004_add_missing_indexes'
down_revision = '003_add_vector_index'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Foreign key indexes (PostgreSQL doesn't auto-create these)
    
    # Documents: Common lookup by user_id
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_documents_user_id 
        ON documents (user_id)
    ''')
    
    # Workflows: Common lookup by user_id
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_workflows_user_id 
        ON workflows (user_id)
    ''')
    
    # API Keys: Composite index for user + provider lookups
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_api_keys_user_provider 
        ON api_keys (user_id, provider)
    ''')
    
    # Connectors: Composite index for user + type lookups
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_connectors_user_type 
        ON connectors (user_id, connector_type)
    ''')
    
    # Pagination indexes (for efficient offset/limit queries)
    
    # Workflows: Paginated list by user, sorted by updated_at
    # INCLUDE adds columns to index for index-only scans
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_workflows_user_updated 
        ON workflows (user_id, updated_at DESC)
        INCLUDE (id, name)
    ''')
    
    # Workflow executions: History by workflow, sorted by created
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_created 
        ON workflow_executions (workflow_id, started_at DESC)
    ''')
    
    # Documents: Paginated list by user, sorted by created_at
    op.execute('''
        CREATE INDEX IF NOT EXISTS idx_documents_user_created 
        ON documents (user_id, created_at DESC)
        INCLUDE (id, title)
    ''')


def downgrade() -> None:
    # Drop pagination indexes
    op.execute('DROP INDEX IF EXISTS idx_documents_user_created')
    op.execute('DROP INDEX IF EXISTS idx_workflow_executions_workflow_created')
    op.execute('DROP INDEX IF EXISTS idx_workflows_user_updated')
    
    # Drop composite indexes
    op.execute('DROP INDEX IF EXISTS idx_connectors_user_type')
    op.execute('DROP INDEX IF EXISTS idx_api_keys_user_provider')
    
    # Drop foreign key indexes
    op.execute('DROP INDEX IF EXISTS idx_workflows_user_id')
    op.execute('DROP INDEX IF EXISTS idx_documents_user_id')
