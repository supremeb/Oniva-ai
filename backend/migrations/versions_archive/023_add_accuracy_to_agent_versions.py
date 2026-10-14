"""Add accuracy columns to agent_versions table

Revision ID: 023_add_accuracy_to_versions
Revises: 022_deploy_history
Create Date: 2026-01-10

Part of Phase 2A: Agent Studio
Adds accuracy tracking columns to agent_versions for deploy gate checks.
"""
from alembic import op
import sqlalchemy as sa

revision = '023_add_accuracy_to_versions'
down_revision = '022_deploy_history'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add accuracy tracking columns to agent_versions
    op.add_column('agent_versions',
        sa.Column('accuracy', sa.Float, nullable=True)
    )
    op.add_column('agent_versions',
        sa.Column('accuracy_at_deploy', sa.Float, nullable=True)
    )
    op.add_column('agent_versions',
        sa.Column('test_count', sa.Integer, server_default='0')
    )
    op.add_column('agent_versions',
        sa.Column('deploy_blocked', sa.Boolean, server_default='false')
    )
    op.add_column('agent_versions',
        sa.Column('block_reason', sa.Text, nullable=True)
    )

    # Add index for deployed versions lookup
    op.create_index(
        'idx_agent_versions_deployed',
        'agent_versions',
        ['agent_id', 'is_deployed'],
        postgresql_where=sa.text('is_deployed = true')
    )


def downgrade() -> None:
    # Drop index
    op.drop_index('idx_agent_versions_deployed', table_name='agent_versions')

    # Remove columns
    op.drop_column('agent_versions', 'block_reason')
    op.drop_column('agent_versions', 'deploy_blocked')
    op.drop_column('agent_versions', 'test_count')
    op.drop_column('agent_versions', 'accuracy_at_deploy')
    op.drop_column('agent_versions', 'accuracy')
