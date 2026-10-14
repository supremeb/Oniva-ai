"""Add user management tables and update users table

Revision ID: 033_add_user_management_tables
Revises: 032_add_memory_tables
Create Date: 2026-01-19

Part of User Management & RBAC feature.
Implements:
- pending_users table for admin-added users who haven't signed up
- password_reset_tokens table for future email-based password reset
- New columns on users table: added_by, added_at, password_changed_at
- Update role values from analyst to member
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '033_add_user_management_tables'
down_revision = '032_add_memory_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Add new columns to users table
    # ===========================================
    op.add_column('users', sa.Column(
        'added_by',
        UUID(as_uuid=True),
        sa.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        comment='Admin who added this user'
    ))
    op.add_column('users', sa.Column(
        'added_at',
        sa.DateTime(timezone=True),
        nullable=True,
        comment='When this user was added by admin'
    ))
    op.add_column('users', sa.Column(
        'password_changed_at',
        sa.DateTime(timezone=True),
        nullable=True,
        comment='When password was last changed'
    ))

    # Add foreign key constraint for added_by
    op.create_foreign_key(
        'fk_users_added_by',
        'users', 'users',
        ['added_by'], ['id'],
        ondelete='SET NULL'
    )

    # ===========================================
    # 2. Create pending_users table
    # ===========================================
    op.create_table(
        'pending_users',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # Multi-tenancy
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('email', sa.String(255), nullable=False, index=True),

        # Pre-assigned role
        sa.Column('role', sa.String(50), nullable=False, server_default='member'),

        # Who added this user
        sa.Column('added_by', UUID(as_uuid=True), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['added_by'], ['users.id'], ondelete='SET NULL'),

        # Unique constraint: email unique within tenant
        sa.UniqueConstraint('tenant_id', 'email', name='uq_pending_users_tenant_email'),

        # Check constraint for valid roles
        sa.CheckConstraint(
            "role IN ('admin', 'member', 'viewer')",
            name='ck_pending_users_valid_role'
        ),
    )

    # Composite index for tenant + email lookups
    op.create_index(
        'idx_pending_users_tenant_email',
        'pending_users',
        ['tenant_id', 'email']
    )

    # ===========================================
    # 3. Create password_reset_tokens table
    # ===========================================
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),

        # User this token belongs to
        sa.Column('user_id', UUID(as_uuid=True), nullable=False, index=True),

        # Token (hashed)
        sa.Column('token_hash', sa.String(255), nullable=False),

        # Expiration
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),

        # Usage tracking
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),

        # Foreign keys
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )

    # Index for finding tokens by user
    op.create_index(
        'idx_password_reset_tokens_user',
        'password_reset_tokens',
        ['user_id']
    )

    # ===========================================
    # 4. Enable Row Level Security for pending_users
    # ===========================================
    op.execute("""
        ALTER TABLE pending_users ENABLE ROW LEVEL SECURITY;
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_pending_users ON pending_users
            FOR ALL
            TO oniva_app
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            );
    """)
    op.execute("ALTER TABLE pending_users FORCE ROW LEVEL SECURITY;")

    # ===========================================
    # 5. Add audit triggers for pending_users
    # ===========================================
    op.execute("""
        CREATE TRIGGER audit_pending_users_trigger
            AFTER INSERT OR UPDATE OR DELETE ON pending_users
            FOR EACH ROW EXECUTE FUNCTION audit_memory_trigger_func();
    """)


def downgrade() -> None:
    # Drop audit trigger
    op.execute("DROP TRIGGER IF EXISTS audit_pending_users_trigger ON pending_users;")

    # Drop RLS policies
    op.execute("DROP POLICY IF EXISTS tenant_isolation_pending_users ON pending_users;")
    op.execute("ALTER TABLE pending_users DISABLE ROW LEVEL SECURITY;")

    # Drop tables
    op.drop_table('password_reset_tokens')
    op.drop_table('pending_users')

    # Drop foreign key constraint
    op.drop_constraint('fk_users_added_by', 'users', type_='foreignkey')

    # Drop columns from users table
    op.drop_column('users', 'password_changed_at')
    op.drop_column('users', 'added_at')
    op.drop_column('users', 'added_by')
