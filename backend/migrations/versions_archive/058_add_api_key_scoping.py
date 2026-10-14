"""Add API key scoping, IP whitelist, and expiration

Enterprise feature: Granular API key permissions with scopes, IP whitelisting,
and expiration dates for enhanced security and compliance.

Revision ID: 058_api_key_scoping
Revises: 057_alerting_and_quotas
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, INET

revision = '058_api_key_scoping'
down_revision = '057_alerting_and_quotas'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add scoping and security columns to api_keys table
    op.add_column('api_keys',
        sa.Column('scopes', ARRAY(sa.String), nullable=False, server_default="{'*:*'}")
    )
    op.add_column('api_keys',
        sa.Column('allowed_ip_addresses', ARRAY(INET), nullable=True)
    )
    op.add_column('api_keys',
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('api_keys',
        sa.Column('rate_limit_per_hour', sa.Integer, nullable=False, server_default='1000')
    )
    op.add_column('api_keys',
        sa.Column('last_used_ip', INET, nullable=True)
    )

    # Create index on expiration for efficient cleanup queries
    op.create_index(
        'idx_api_keys_expires',
        'api_keys',
        ['expires_at'],
        postgresql_where=sa.text('expires_at IS NOT NULL')
    )

    # Create index on scopes for scope-based queries
    op.create_index(
        'idx_api_keys_scopes',
        'api_keys',
        ['scopes'],
        postgresql_using='gin'
    )

    print("✅ API key scoping columns added")


def downgrade() -> None:
    op.drop_index('idx_api_keys_scopes', 'api_keys')
    op.drop_index('idx_api_keys_expires', 'api_keys')

    op.drop_column('api_keys', 'last_used_ip')
    op.drop_column('api_keys', 'rate_limit_per_hour')
    op.drop_column('api_keys', 'expires_at')
    op.drop_column('api_keys', 'allowed_ip_addresses')
    op.drop_column('api_keys', 'scopes')
