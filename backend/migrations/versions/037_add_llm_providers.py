"""Add LLM provider configuration table.

Phase 2: LLM Reliability - Provider Settings

This migration creates the llm_providers table for tenant-specific
LLM provider configuration with custom endpoint support and fallback ordering.

Revision ID: 037_add_llm_providers
Revises: 036_add_data_classification
Create Date: 2026-01-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = '037_add_llm_providers'
down_revision = '036_add_data_classification'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'llm_providers',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'tenant_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('tenants.id', ondelete='CASCADE'),
            nullable=False,
        ),

        # Provider identification
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('display_name', sa.String(200), nullable=False),
        sa.Column('provider_type', sa.String(50), nullable=False),
        # provider_type: "openai", "anthropic", "openrouter", "azure", "custom"

        # Connection settings
        sa.Column('api_base', sa.String(500), nullable=True),  # Custom endpoint URL
        sa.Column('api_key_encrypted', sa.Text, nullable=True),

        # Model configuration
        sa.Column('models', postgresql.ARRAY(sa.String), server_default='{}'),
        sa.Column('default_model', sa.String(100), nullable=True),

        # Fallback settings
        sa.Column('priority', sa.Integer, server_default='0'),  # Lower = higher priority
        sa.Column('is_enabled', sa.Boolean, server_default='true'),
        sa.Column('is_default', sa.Boolean, server_default='false'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Indexes
    op.create_index('ix_llm_providers_tenant', 'llm_providers', ['tenant_id'])
    op.create_index('ix_llm_providers_tenant_priority', 'llm_providers', ['tenant_id', 'priority'])

    # Unique constraint: name must be unique per tenant
    op.create_unique_constraint(
        'uq_llm_provider_tenant_name',
        'llm_providers',
        ['tenant_id', 'name'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_llm_provider_tenant_name', 'llm_providers')
    op.drop_index('ix_llm_providers_tenant_priority', 'llm_providers')
    op.drop_index('ix_llm_providers_tenant', 'llm_providers')
    op.drop_table('llm_providers')
