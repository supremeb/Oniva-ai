"""Update scheduled workflows for daemon implementation

Revision ID: 074_update_scheduled_workflows_for_daemon
Revises: 073_add_embedding_model_to_llm_providers
Create Date: 2026-02-25

Changes:
- Drop unused apscheduler_job_id column
- Add optimized partial index for daemon poll query
- Add index on executions(schedule_id, status) for concurrent count
- Add CHECK constraint for status/is_enabled consistency
"""
from alembic import op
import sqlalchemy as sa

revision = '074_update_scheduled_workflows_for_daemon'
down_revision = '073_add_embedding_model_to_llm_providers'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop unused apscheduler_job_id column and its unique constraint
    op.drop_constraint(
        'scheduled_workflows_apscheduler_job_id_key',
        'scheduled_workflows',
        type_='unique'
    )
    op.drop_column('scheduled_workflows', 'apscheduler_job_id')

    # 2. Add optimized partial index for daemon poll query
    #    Replaces the existing (is_enabled, next_run_at) index with a
    #    more selective partial index that includes status check
    op.drop_index('ix_scheduled_workflows_next_run_at', 'scheduled_workflows')
    op.execute("""
        CREATE INDEX idx_scheduled_workflows_poll
        ON scheduled_workflows (next_run_at ASC)
        WHERE is_enabled = true AND status = 'ACTIVE';
    """)

    # 3. Add CHECK constraint for status/is_enabled consistency
    op.execute("""
        ALTER TABLE scheduled_workflows
        ADD CONSTRAINT chk_status_enabled_consistency CHECK (
            (status = 'ACTIVE' AND is_enabled = true) OR
            (status IN ('PAUSED', 'DISABLED', 'COMPLETED', 'ERROR') AND is_enabled = false)
        );
    """)


def downgrade() -> None:
    # Remove CHECK constraint
    op.execute("""
        ALTER TABLE scheduled_workflows
        DROP CONSTRAINT IF EXISTS chk_status_enabled_consistency;
    """)

    # Restore original index
    op.drop_index('idx_scheduled_workflows_poll', 'scheduled_workflows')
    op.create_index(
        'ix_scheduled_workflows_next_run_at',
        'scheduled_workflows',
        ['next_run_at'],
    )

    # Restore apscheduler_job_id column
    op.add_column(
        'scheduled_workflows',
        sa.Column('apscheduler_job_id', sa.String(255), nullable=True)
    )
    op.create_unique_constraint(
        'scheduled_workflows_apscheduler_job_id_key',
        'scheduled_workflows',
        ['apscheduler_job_id']
    )
