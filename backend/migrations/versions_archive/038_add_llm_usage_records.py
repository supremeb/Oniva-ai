"""Add LLM usage tracking table.

Phase 2: LLM Reliability - Usage Tracking

This migration creates the llm_usage_records table for tracking
LLM API calls, costs, and latency metrics per tenant.

Revision ID: 038_add_llm_usage_records
Revises: 037_add_llm_providers
Create Date: 2026-01-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = '038_add_llm_usage_records'
down_revision = '037_add_llm_providers'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'llm_usage_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'tenant_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('tenants.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),

        # Provider info
        sa.Column(
            'provider_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('llm_providers.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('provider_name', sa.String(100), nullable=False),
        sa.Column('model', sa.String(100), nullable=False),

        # Usage metrics
        sa.Column('input_tokens', sa.Integer, nullable=False),
        sa.Column('output_tokens', sa.Integer, nullable=False),
        sa.Column('cost_usd', sa.Numeric(10, 6), nullable=False),
        sa.Column('latency_ms', sa.Integer, nullable=False),

        # Context
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('node_id', sa.String(100), nullable=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True),

        # Status
        sa.Column('status', sa.String(20), server_default='success'),

        # Timestamp
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Indexes for common query patterns
    # Tenant + date range for dashboard queries
    op.create_index(
        'ix_llm_usage_tenant_date',
        'llm_usage_records',
        ['tenant_id', 'created_at'],
    )

    # Provider aggregation
    op.create_index(
        'ix_llm_usage_provider',
        'llm_usage_records',
        ['provider_id', 'created_at'],
    )

    # Workflow attribution
    op.create_index(
        'ix_llm_usage_workflow',
        'llm_usage_records',
        ['workflow_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_llm_usage_workflow', 'llm_usage_records')
    op.drop_index('ix_llm_usage_provider', 'llm_usage_records')
    op.drop_index('ix_llm_usage_tenant_date', 'llm_usage_records')
    op.drop_table('llm_usage_records')
