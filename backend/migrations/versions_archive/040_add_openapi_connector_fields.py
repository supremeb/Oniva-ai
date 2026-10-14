"""Add OpenAPI connector fields to connectors table.

OpenAPI Connector Factory feature - extends the connectors table
to support dynamically generated connectors from OpenAPI specs.

New fields:
- source_type: "native", "curated", or "custom"
- openapi_spec_url: URL to fetch spec from
- openapi_spec_version: OpenAPI version (e.g., "3.0.3")
- openapi_spec_hash: SHA256 hash for change detection
- openapi_operations: Cached tool definitions (JSONB)

Revision ID: 040_add_openapi_connector_fields
Revises: 039_add_kb_id_index
Create Date: 2026-01-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic
revision = '040_add_openapi_connector_fields'
down_revision = '039_add_kb_id_index'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to connectors table
    op.add_column(
        'connectors',
        sa.Column(
            'source_type',
            sa.String(20),
            nullable=False,
            server_default='native',
            comment='Source: native, curated, or custom'
        )
    )
    op.add_column(
        'connectors',
        sa.Column(
            'openapi_spec_url',
            sa.String(500),
            nullable=True,
            comment='URL to fetch OpenAPI spec'
        )
    )
    op.add_column(
        'connectors',
        sa.Column(
            'openapi_spec_version',
            sa.String(20),
            nullable=True,
            comment='OpenAPI spec version (e.g., 3.0.3)'
        )
    )
    op.add_column(
        'connectors',
        sa.Column(
            'openapi_spec_hash',
            sa.String(64),
            nullable=True,
            comment='SHA256 hash of spec for change detection'
        )
    )
    op.add_column(
        'connectors',
        sa.Column(
            'openapi_operations',
            JSONB,
            nullable=True,
            server_default='[]',
            comment='Cached tool definitions from spec'
        )
    )
    op.add_column(
        'connectors',
        sa.Column(
            'registry_id',
            sa.String(100),
            nullable=True,
            comment='ID in curated registry (e.g., stripe)'
        )
    )

    # Add index for source_type filtering
    op.create_index(
        'idx_connectors_source_type',
        'connectors',
        ['source_type']
    )

    # Add index for registry lookups
    op.create_index(
        'idx_connectors_registry_id',
        'connectors',
        ['registry_id'],
        postgresql_where=sa.text("registry_id IS NOT NULL")
    )


def downgrade() -> None:
    # Remove indexes
    op.drop_index('idx_connectors_registry_id', table_name='connectors')
    op.drop_index('idx_connectors_source_type', table_name='connectors')

    # Remove columns
    op.drop_column('connectors', 'registry_id')
    op.drop_column('connectors', 'openapi_operations')
    op.drop_column('connectors', 'openapi_spec_hash')
    op.drop_column('connectors', 'openapi_spec_version')
    op.drop_column('connectors', 'openapi_spec_url')
    op.drop_column('connectors', 'source_type')
