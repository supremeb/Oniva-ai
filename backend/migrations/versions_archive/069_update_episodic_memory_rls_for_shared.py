"""Update episodic_memories RLS policy to allow tenant-wide access to shared memories.

The original RLS policy (migration 032) enforces both tenant_id AND user_id isolation.
With org context shared memories (migration 068), shared memories need to be readable
by all users in the same tenant, not just the original author.

This migration replaces the single policy with two:
- user_isolation_episodic_private: Private memories stay user-scoped
- tenant_shared_episodic: Shared memories are accessible to entire tenant

Revision ID: 069_update_episodic_memory_rls
Revises: 068_add_org_context_support
Create Date: 2026-02-08
"""
from alembic import op

revision = '069_update_episodic_memory_rls'
down_revision = '068_add_org_context_support'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old policies (user_isolation_episodic from migration 032,
    # and tenant_isolation_episodic_memories if it exists from manual setup)
    op.execute("""
        DROP POLICY IF EXISTS user_isolation_episodic ON episodic_memories;
    """)
    op.execute("""
        DROP POLICY IF EXISTS tenant_isolation_episodic_memories ON episodic_memories;
    """)

    # Policy 1: Private memories - require both tenant_id and user_id match
    op.execute("""
        CREATE POLICY user_private_episodic ON episodic_memories
            FOR ALL
            TO oniva_app
            USING (
                visibility = 'private'
                AND tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
            WITH CHECK (
                visibility = 'private'
                AND tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            );
    """)

    # Policy 2: Shared memories - require only tenant_id match (any user in tenant can read)
    op.execute("""
        CREATE POLICY tenant_shared_episodic ON episodic_memories
            FOR ALL
            TO oniva_app
            USING (
                visibility = 'shared'
                AND tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            )
            WITH CHECK (
                visibility = 'shared'
                AND tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            );
    """)


def downgrade() -> None:
    # Drop the new split policies
    op.execute("DROP POLICY IF EXISTS user_private_episodic ON episodic_memories;")
    op.execute("DROP POLICY IF EXISTS tenant_shared_episodic ON episodic_memories;")

    # Restore the original combined policy
    op.execute("""
        CREATE POLICY user_isolation_episodic ON episodic_memories
            FOR ALL
            TO oniva_app
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            );
    """)
