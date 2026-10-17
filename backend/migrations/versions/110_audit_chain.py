"""Tamper-evident audit chain (Approvals & Policy plan, U1 / KTD9).

Revision ID: 110_audit_chain
Revises: 109_llm_usage_agent_role

Adds the per-tenant chain position to ``compliance_audit_logs`` (``seq``, ``signing_key_id``,
``actor_email``, ``actor_name``), the unique ``(tenant_id, seq)`` index that makes a fork
impossible, and ``audit_chain_heads`` (a rebuildable cache of the last committed entry, under
tenant RLS like the other read-model tables).

``compliance_audit_logs`` becomes append-only at the database. The table owner is the migration
role (never ``oniva_app``), so ``REVOKE UPDATE, DELETE, TRUNCATE ... FROM oniva_app`` is
effective and the owner-only trigger functions cannot be dropped by the request role. Trigger
``compliance_audit_logs_immutable`` allows exactly two deletions, both only for
``session_user = 'oniva_system'`` and a transaction-local GUC:

  * ``SET LOCAL oniva.audit_purge = 'retention'``      - only rows past ``retention_until``;
  * ``SET LOCAL oniva.audit_purge = 'tenant_delete'``  - only the tenant named in
    ``oniva.audit_purge_tenant`` (the ``ON DELETE CASCADE`` from ``tenants``).

The one allowed UPDATE is the ``ON DELETE SET NULL`` FK action from ``users``: ``user_id``
non-null -> NULL with every other column unchanged. The entry hash does not cover ``user_id``
(only the ``actor_email``/``actor_name`` snapshot), so attribution survives user deletion.

Every statement is idempotent: the baseline builds its schema from the live models, so a fresh
database may already hold the columns, index and table when this revision runs.
"""
from alembic import op

revision = "110_audit_chain"
down_revision = "109_llm_usage_agent_role"
branch_labels = None
depends_on = None

TENANT_EXPR = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"

IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION compliance_audit_logs_immutable() RETURNS trigger AS $$
DECLARE
    v_purge text := NULLIF(current_setting('oniva.audit_purge', true), '');
    v_purge_tenant text := NULLIF(current_setting('oniva.audit_purge_tenant', true), '');
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'compliance_audit_logs is append-only: TRUNCATE rejected';
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.user_id IS NOT NULL AND NEW.user_id IS NULL
           AND (to_jsonb(OLD) - 'user_id') IS NOT DISTINCT FROM (to_jsonb(NEW) - 'user_id') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'compliance_audit_logs is append-only: UPDATE rejected';
    ELSIF TG_OP = 'DELETE' THEN
        IF session_user = 'oniva_system' AND v_purge = 'retention' THEN
            IF OLD.retention_until < now() THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'compliance_audit_logs retention purge rejected: row not past retention_until';
        ELSIF session_user = 'oniva_system' AND v_purge = 'tenant_delete'
              AND OLD.tenant_id::text = v_purge_tenant THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'compliance_audit_logs is append-only: DELETE rejected';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.execute("ALTER TABLE compliance_audit_logs ADD COLUMN IF NOT EXISTS seq bigint")
    op.execute("ALTER TABLE compliance_audit_logs ADD COLUMN IF NOT EXISTS signing_key_id varchar(64)")
    op.execute("ALTER TABLE compliance_audit_logs ADD COLUMN IF NOT EXISTS actor_email varchar(255)")
    op.execute("ALTER TABLE compliance_audit_logs ADD COLUMN IF NOT EXISTS actor_name varchar(255)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_compliance_audit_logs_tenant_seq "
        "ON compliance_audit_logs (tenant_id, seq)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_chain_heads (
            tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            seq bigint NOT NULL DEFAULT 0,
            head_hash text,
            key_id text,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE audit_chain_heads ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_chain_heads FORCE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON audit_chain_heads TO oniva_system")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON audit_chain_heads TO oniva_app")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_audit_chain_heads ON audit_chain_heads")
    op.execute(
        f"CREATE POLICY tenant_isolation_audit_chain_heads ON audit_chain_heads FOR ALL TO oniva_app "
        f"USING (tenant_id = {TENANT_EXPR}) WITH CHECK (tenant_id = {TENANT_EXPR})"
    )

    op.execute(IMMUTABLE_FN)
    op.execute("DROP TRIGGER IF EXISTS compliance_audit_logs_immutable ON compliance_audit_logs")
    op.execute(
        "CREATE TRIGGER compliance_audit_logs_immutable "
        "BEFORE UPDATE OR DELETE ON compliance_audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION compliance_audit_logs_immutable()"
    )
    op.execute("DROP TRIGGER IF EXISTS compliance_audit_logs_no_truncate ON compliance_audit_logs")
    op.execute(
        "CREATE TRIGGER compliance_audit_logs_no_truncate "
        "BEFORE TRUNCATE ON compliance_audit_logs "
        "FOR EACH STATEMENT EXECUTE FUNCTION compliance_audit_logs_immutable()"
    )

    # The request role loses every mutating privilege; only oniva_system may purge, and only
    # through the two guarded modes above.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON compliance_audit_logs FROM oniva_app")
    op.execute("GRANT DELETE ON compliance_audit_logs TO oniva_system")

    # The immutability guarantees only hold if the request role does not own the table.
    op.execute(
        """
        DO $$
        DECLARE owner_name text;
        BEGIN
            SELECT pg_get_userbyid(relowner) INTO owner_name
              FROM pg_class WHERE oid = 'compliance_audit_logs'::regclass;
            IF owner_name = 'oniva_app' THEN
                RAISE EXCEPTION
                    'compliance_audit_logs must be owned by the migration/owner role, not oniva_app';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS compliance_audit_logs_no_truncate ON compliance_audit_logs")
    op.execute("DROP TRIGGER IF EXISTS compliance_audit_logs_immutable ON compliance_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS compliance_audit_logs_immutable()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_audit_chain_heads ON audit_chain_heads")
    op.execute("DROP TABLE IF EXISTS audit_chain_heads")
    op.execute("DROP INDEX IF EXISTS uq_compliance_audit_logs_tenant_seq")
    op.execute("ALTER TABLE compliance_audit_logs DROP COLUMN IF EXISTS actor_name")
    op.execute("ALTER TABLE compliance_audit_logs DROP COLUMN IF EXISTS actor_email")
    op.execute("ALTER TABLE compliance_audit_logs DROP COLUMN IF EXISTS signing_key_id")
    op.execute("ALTER TABLE compliance_audit_logs DROP COLUMN IF EXISTS seq")
    op.execute("GRANT UPDATE, DELETE ON compliance_audit_logs TO oniva_app")
