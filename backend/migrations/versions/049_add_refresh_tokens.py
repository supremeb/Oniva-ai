"""Add refresh tokens table for JWT refresh token management

Implements secure refresh token storage with:
- Token hashing (SHA256)
- Token family tracking for rotation detection
- Revocation support
- Client metadata (user agent, IP)
- RLS policy for tenant isolation

Revision ID: 049_refresh_tokens
Revises: 048_signup_write
Create Date: 2026-01-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '049_refresh_tokens'
down_revision = '048_signup_write'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create refresh_tokens table
    op.create_table(
        'refresh_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('family_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.String(255), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('user_agent', sa.String(512), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Create indexes
    op.create_index('ix_refresh_tokens_user_id', 'refresh_tokens', ['user_id'])
    op.create_index('ix_refresh_tokens_tenant_id', 'refresh_tokens', ['tenant_id'])
    op.create_index('ix_refresh_tokens_token_hash', 'refresh_tokens', ['token_hash'], unique=True)
    op.create_index('ix_refresh_tokens_family_id', 'refresh_tokens', ['family_id'])
    op.create_index('ix_refresh_tokens_user_active', 'refresh_tokens', ['user_id', 'revoked'])
    op.create_index('ix_refresh_tokens_family_active', 'refresh_tokens', ['family_id', 'revoked'])
    op.create_index('ix_refresh_tokens_expires', 'refresh_tokens', ['expires_at'])

    # Enable RLS
    op.execute("ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY")

    # Create RLS policy - tokens are tenant-isolated
    # During auth (no tenant context), allow access for token validation
    op.execute("""
        DO $$
        BEGIN
            CREATE POLICY refresh_tokens_tenant_isolation ON refresh_tokens
            FOR ALL
            TO oniva_app
            USING (
                CASE
                    WHEN current_setting('app.current_tenant', true) IS NULL
                         OR current_setting('app.current_tenant', true) = '' THEN true
                    ELSE tenant_id = current_setting('app.current_tenant', true)::uuid
                END
            )
            WITH CHECK (
                CASE
                    WHEN current_setting('app.current_tenant', true) IS NULL
                         OR current_setting('app.current_tenant', true) = '' THEN true
                    ELSE tenant_id = current_setting('app.current_tenant', true)::uuid
                END
            );
        END
        $$;
    """)


def downgrade() -> None:
    # Drop RLS policy
    op.execute("DROP POLICY IF EXISTS refresh_tokens_tenant_isolation ON refresh_tokens")
    op.execute("ALTER TABLE refresh_tokens DISABLE ROW LEVEL SECURITY")

    # Drop indexes
    op.drop_index('ix_refresh_tokens_expires', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_family_active', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_user_active', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_family_id', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_token_hash', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_tenant_id', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_user_id', table_name='refresh_tokens')

    # Drop table
    op.drop_table('refresh_tokens')
