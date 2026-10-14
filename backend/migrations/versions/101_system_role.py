"""System role for non-request work (U9, KTD4).

Revision ID: 101_system_role
Revises: 100_baseline

Creates ``oniva_system`` (LOGIN NOSUPERUSER BYPASSRLS, NO password; set one out-of-band with
``ALTER ROLE oniva_system PASSWORD ...``). It is used ONLY through the named system session
factory in ``app/core/database.py`` by the workflow scheduler, webhook ingestion, startup
recovery and pre-tenant auth lookups (login, signup, check-email, onboarding, token -> user).

Grants: schema USAGE, DML (no TRUNCATE / DDL / ownership) on the tables, sequence usage, and
default privileges for future tables. ``audit_events`` stays append-only: UPDATE/DELETE are
revoked here on top of the immutability triggers. BYPASSRLS skips policies even under FORCE,
so the privilege boundary between the two roles is the role attribute, not a policy.

``oniva_app`` stays NOSUPERUSER NOBYPASSRLS (re-asserted here); the permissive users/tenants
signup policies are not recreated (they were already dropped from the baseline).

It also closes a gap in the baseline's systematic RLS: four child tables without their own
``tenant_id`` (team_memberships, compliance_screening_results, workflow_batch_items,
password_reset_tokens) got no RLS at all; they now get FORCE RLS + a parent-scoped policy.
"""
from alembic import op

revision = "101_system_role"
down_revision = "100_baseline"
branch_labels = None
depends_on = None

# Child tables that have no tenant_id column of their own (so the baseline's systematic policy
# skipped them): isolated through their parent, which is itself under RLS for oniva_app.
# (table, fk column, parent table)
CHILD_TABLES = [
    ("team_memberships", "team_id", "teams"),
    ("compliance_screening_results", "execution_id", "compliance_executions"),
    ("workflow_batch_items", "batch_id", "workflow_batches"),
    ("password_reset_tokens", "user_id", "users"),
]


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oniva_system') THEN
                CREATE ROLE oniva_system WITH LOGIN NOSUPERUSER BYPASSRLS;
            ELSE
                ALTER ROLE oniva_system WITH NOSUPERUSER BYPASSRLS;
            END IF;
            ALTER ROLE oniva_app WITH NOSUPERUSER NOBYPASSRLS;
        END
        $$;
        """
    )
    for table, fk, parent in CHILD_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation_{table} ON "{table}" FOR ALL TO oniva_app '
            f'USING ({fk} IN (SELECT id FROM "{parent}")) '
            f'WITH CHECK ({fk} IN (SELECT id FROM "{parent}"))'
        )

    op.execute("GRANT USAGE ON SCHEMA public TO oniva_system")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO oniva_system")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO oniva_system")
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM oniva_system")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO oniva_system"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO oniva_system"
    )


def downgrade() -> None:
    for table, _fk, _parent in CHILD_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM oniva_system")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM oniva_system")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM oniva_system")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM oniva_system")
    op.execute("REVOKE USAGE ON SCHEMA public FROM oniva_system")
    # Role itself is kept (cluster-level object; may own sessions/grants elsewhere).
