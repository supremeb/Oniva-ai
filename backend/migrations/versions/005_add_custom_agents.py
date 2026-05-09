"""Add custom agents table

Revision ID: 003
Revises: 002
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005_add_custom_agents'
down_revision = '93e27b222f4b'
branch_labels = None
depends_on = None


def upgrade():
    # Create custom_agents table
    op.create_table(
        'custom_agents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('icon', sa.String(50), default='Robot'),
        sa.Column('agent_type', sa.String(100), default='custom'),
        sa.Column('model', sa.String(100), default='gpt-4o'),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('temperature', sa.Float(), default=0.7),
        sa.Column('max_tokens', sa.Integer(), default=2000),
        sa.Column('skills', postgresql.ARRAY(sa.String()), default=list),
        sa.Column('connector_ids', postgresql.ARRAY(postgresql.UUID(as_uuid=True)), default=list),
        sa.Column('config', postgresql.JSONB(), default=dict),
        sa.Column('workflow_usage', postgresql.JSONB(), default=dict),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('is_template', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('NOW()'), onupdate=sa.text('NOW()')),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('usage_count', sa.Integer(), default=0),
    )

    # Create indexes
    op.create_index('ix_custom_agents_user_id', 'custom_agents', ['user_id'])
    op.create_index('ix_custom_agents_agent_type', 'custom_agents', ['agent_type'])


def downgrade():
    # Drop indexes
    op.drop_index('ix_custom_agents_agent_type')
    op.drop_index('ix_custom_agents_user_id')

    # Drop table
    op.drop_table('custom_agents')
