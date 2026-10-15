"""Extend skill_executions table with audit fields

Revision ID: 042_extend_skill_execution_audit
Revises: 041_add_document_chunk_indexes
Create Date: 2025-01-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '042_extend_skill_execution_audit'
down_revision: Union[str, None] = '041_add_document_chunk_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to skill_executions table

    # Make skill_id nullable (for skills that aren't in the database)
    op.alter_column('skill_executions', 'skill_id',
        existing_type=sa.UUID(),
        nullable=True
    )

    # Add skill_name for faster queries without joins
    op.add_column('skill_executions',
        sa.Column('skill_name', sa.String(255), nullable=True)
    )

    # Backfill skill_name from existing skills
    op.execute("""
        UPDATE skill_executions se
        SET skill_name = s.name
        FROM skills s
        WHERE se.skill_id = s.id AND se.skill_name IS NULL
    """)

    # Now make skill_name not nullable
    op.alter_column('skill_executions', 'skill_name',
        existing_type=sa.String(255),
        nullable=False
    )

    # Add user and session tracking
    op.add_column('skill_executions',
        sa.Column('user_id', sa.UUID(), nullable=True)
    )
    op.add_column('skill_executions',
        sa.Column('session_id', sa.String(255), nullable=True)
    )

    # Add workflow context
    op.add_column('skill_executions',
        sa.Column('workflow_id', sa.UUID(), nullable=True)
    )
    op.add_column('skill_executions',
        sa.Column('workflow_run_id', sa.UUID(), nullable=True)
    )
    op.add_column('skill_executions',
        sa.Column('node_id', sa.String(255), nullable=True)
    )

    # Add error type
    op.add_column('skill_executions',
        sa.Column('error_type', sa.String(100), nullable=True)
    )

    # Add audit fields for integrity verification
    op.add_column('skill_executions',
        sa.Column('input_hash', sa.String(64), nullable=True)
    )
    op.add_column('skill_executions',
        sa.Column('output_hash', sa.String(64), nullable=True)
    )
    op.add_column('skill_executions',
        sa.Column('audit_metadata', postgresql.JSON(), nullable=True)
    )

    # Add completed_at timestamp
    op.add_column('skill_executions',
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True)
    )

    # Create indexes for the new columns
    op.create_index('ix_skill_executions_skill_name', 'skill_executions', ['skill_name'])
    op.create_index('ix_skill_executions_user_id', 'skill_executions', ['user_id'])
    op.create_index('ix_skill_executions_session_id', 'skill_executions', ['session_id'])
    op.create_index('ix_skill_executions_workflow_id', 'skill_executions', ['workflow_id'])
    op.create_index('ix_skill_executions_workflow_run_id', 'skill_executions', ['workflow_run_id'])
    op.create_index('ix_skill_executions_agent_id', 'skill_executions', ['agent_id'])

    # Add foreign key constraint for user_id
    op.create_foreign_key(
        'fk_skill_executions_user_id',
        'skill_executions', 'users',
        ['user_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Remove foreign key constraint
    op.drop_constraint('fk_skill_executions_user_id', 'skill_executions', type_='foreignkey')

    # Drop indexes
    op.drop_index('ix_skill_executions_agent_id', 'skill_executions')
    op.drop_index('ix_skill_executions_workflow_run_id', 'skill_executions')
    op.drop_index('ix_skill_executions_workflow_id', 'skill_executions')
    op.drop_index('ix_skill_executions_session_id', 'skill_executions')
    op.drop_index('ix_skill_executions_user_id', 'skill_executions')
    op.drop_index('ix_skill_executions_skill_name', 'skill_executions')

    # Drop columns
    op.drop_column('skill_executions', 'completed_at')
    op.drop_column('skill_executions', 'audit_metadata')
    op.drop_column('skill_executions', 'output_hash')
    op.drop_column('skill_executions', 'input_hash')
    op.drop_column('skill_executions', 'error_type')
    op.drop_column('skill_executions', 'node_id')
    op.drop_column('skill_executions', 'workflow_run_id')
    op.drop_column('skill_executions', 'workflow_id')
    op.drop_column('skill_executions', 'session_id')
    op.drop_column('skill_executions', 'user_id')
    op.drop_column('skill_executions', 'skill_name')

    # Revert skill_id to non-nullable
    op.alter_column('skill_executions', 'skill_id',
        existing_type=sa.UUID(),
        nullable=False
    )
