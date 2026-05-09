"""Enhance workflow_executions table for logs and monitoring.

Revision ID: 030_enhance_workflow_executions
Revises: 029_add_background_job_trigger
Create Date: 2025-01-16

Adds columns for:
- trigger: Execution trigger type (manual, api, scheduled)
- input_data: Input variables passed to workflow
- nodes_executed: Number of nodes executed
- total_nodes: Total nodes in workflow
- total_tokens: Token usage for LLM calls
- duration_ms: Execution time in milliseconds
- execution_trace: Per-node execution details for tracing
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic
revision = '030_enhance_workflow_executions'
down_revision = '029_add_background_job_trigger'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to workflow_executions
    op.add_column('workflow_executions', sa.Column('trigger', sa.String(50), nullable=True, default='manual'))
    op.add_column('workflow_executions', sa.Column('input_data', JSONB, nullable=True))
    op.add_column('workflow_executions', sa.Column('nodes_executed', sa.Integer, nullable=True, default=0))
    op.add_column('workflow_executions', sa.Column('total_nodes', sa.Integer, nullable=True, default=0))
    op.add_column('workflow_executions', sa.Column('total_tokens', sa.Integer, nullable=True, default=0))
    op.add_column('workflow_executions', sa.Column('duration_ms', sa.Integer, nullable=True))
    op.add_column('workflow_executions', sa.Column('execution_trace', JSONB, nullable=True))

    # Add indexes for common queries
    op.create_index('ix_workflow_executions_workflow_id', 'workflow_executions', ['workflow_id'])
    op.create_index('ix_workflow_executions_status', 'workflow_executions', ['status'])
    op.create_index('ix_workflow_executions_started_at', 'workflow_executions', ['started_at'])

    # Set default values for existing rows
    op.execute("UPDATE workflow_executions SET trigger = 'manual' WHERE trigger IS NULL")
    op.execute("UPDATE workflow_executions SET input_data = '{}' WHERE input_data IS NULL")
    op.execute("UPDATE workflow_executions SET nodes_executed = 0 WHERE nodes_executed IS NULL")
    op.execute("UPDATE workflow_executions SET total_nodes = 0 WHERE total_nodes IS NULL")
    op.execute("UPDATE workflow_executions SET total_tokens = 0 WHERE total_tokens IS NULL")
    op.execute("UPDATE workflow_executions SET execution_trace = '[]' WHERE execution_trace IS NULL")


def downgrade() -> None:
    # Remove indexes
    op.drop_index('ix_workflow_executions_started_at', table_name='workflow_executions')
    op.drop_index('ix_workflow_executions_status', table_name='workflow_executions')
    op.drop_index('ix_workflow_executions_workflow_id', table_name='workflow_executions')

    # Remove columns
    op.drop_column('workflow_executions', 'execution_trace')
    op.drop_column('workflow_executions', 'duration_ms')
    op.drop_column('workflow_executions', 'total_tokens')
    op.drop_column('workflow_executions', 'total_nodes')
    op.drop_column('workflow_executions', 'nodes_executed')
    op.drop_column('workflow_executions', 'input_data')
    op.drop_column('workflow_executions', 'trigger')
