"""Add memory UI fields for user feedback and visibility.

Part of Phase 4: Memory UI & Agent Triggers.
Adds fields for:
- Memory type classification
- User feedback (helpful/not helpful)
- Visibility control (public/private)
- Provenance tracking (triggered from chat)

Revision ID: 047_add_memory_ui_fields
Revises: 046_add_workflow_entities
Create Date: 2026-01-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = '047_add_memory_ui_fields'
down_revision = '046_add_workflow_entities'
branch_labels = None
depends_on = None


def upgrade():
    """Add memory UI fields to episodic_memories table."""
    # Memory type for UI filtering
    op.add_column('episodic_memories',
        sa.Column('memory_type', sa.String(50), nullable=False, server_default='decision',
                  comment="Memory type: decision, fact, preference, conversation")
    )

    # User feedback fields
    op.add_column('episodic_memories',
        sa.Column('feedback', sa.String(20), nullable=True,
                  comment="User feedback: helpful, not_helpful, null")
    )
    op.add_column('episodic_memories',
        sa.Column('feedback_at', sa.DateTime(timezone=True), nullable=True,
                  comment="When feedback was provided")
    )

    # Visibility control
    op.add_column('episodic_memories',
        sa.Column('visibility', sa.String(20), nullable=False, server_default='private',
                  comment="Visibility: private (user only), shared (tenant), public")
    )

    # Provenance tracking - what triggered this memory
    op.add_column('episodic_memories',
        sa.Column('source_type', sa.String(50), nullable=True,
                  comment="Source: chat, workflow, api, import")
    )
    op.add_column('episodic_memories',
        sa.Column('source_id', UUID(as_uuid=True), nullable=True,
                  comment="Source reference (chat session ID, workflow execution ID)")
    )

    # Tags for organization
    op.add_column('episodic_memories',
        sa.Column('tags', sa.ARRAY(sa.String(50)), nullable=False, server_default='{}',
                  comment="User-defined tags for organization")
    )

    # Index for memory type filtering
    op.create_index(
        'idx_episodic_memories_type',
        'episodic_memories',
        ['memory_type']
    )

    # Index for feedback filtering (find memories needing review)
    op.create_index(
        'idx_episodic_memories_feedback',
        'episodic_memories',
        ['feedback']
    )

    # Index for visibility filtering
    op.create_index(
        'idx_episodic_memories_visibility',
        'episodic_memories',
        ['visibility']
    )


def downgrade():
    """Remove memory UI fields."""
    # Drop indexes
    op.drop_index('idx_episodic_memories_visibility')
    op.drop_index('idx_episodic_memories_feedback')
    op.drop_index('idx_episodic_memories_type')

    # Drop columns
    op.drop_column('episodic_memories', 'tags')
    op.drop_column('episodic_memories', 'source_id')
    op.drop_column('episodic_memories', 'source_type')
    op.drop_column('episodic_memories', 'visibility')
    op.drop_column('episodic_memories', 'feedback_at')
    op.drop_column('episodic_memories', 'feedback')
    op.drop_column('episodic_memories', 'memory_type')
