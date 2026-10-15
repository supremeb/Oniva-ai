"""Engine interfaces persistence (plan 003, U2).

Revision ID: 102_engine_interfaces
Revises: 101_system_role

Adds ``tool_invocations`` (idempotency outcome store), ``engine_fences`` (per-session fence
high-water mark) and ``engine_conversations``, plus ``request_id`` / ``run_id`` /
``conversation_id`` on ``llm_usage_records`` with a partial unique index on
``(tenant_id, request_id)``.

Every statement is idempotent: the baseline builds its schema from the live models, so a fresh
database may already hold these objects when this revision runs. RLS follows the baseline's
systematic pattern for the two ``tenant_id`` tables (ENABLE + FORCE + FOR ALL TO oniva_app with
WITH CHECK). ``engine_fences`` has no ``tenant_id``: it is system-role only, so ``oniva_app`` is
revoked and also denied by an explicit policy (keeps the "every table has a policy" invariant).
"""
from alembic import op

revision = "102_engine_interfaces"
down_revision = "101_system_role"
branch_labels = None
depends_on = None

TENANT_EXPR = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"
TENANT_TABLES = ("tool_invocations", "engine_conversations")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_invocations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            idempotency_key varchar(255) NOT NULL,
            session_id varchar(255) NOT NULL,
            tool varchar(100) NOT NULL,
            args_hash varchar(64) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'in_flight',
            outcome jsonb,
            fence_token bigint,
            started_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            CONSTRAINT tool_invocations_idempotency_key_key UNIQUE (idempotency_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_tool_invocations_tenant_id ON tool_invocations (tenant_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tool_invocations_session_status_started "
        "ON tool_invocations (session_id, status, started_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS engine_fences (
            session_id varchar(255) PRIMARY KEY,
            high_water bigint NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS engine_conversations (
            conversation_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_engine_conversations_tenant_id ON engine_conversations (tenant_id)")

    op.execute("ALTER TABLE llm_usage_records ADD COLUMN IF NOT EXISTS request_id varchar(255)")
    op.execute("ALTER TABLE llm_usage_records ADD COLUMN IF NOT EXISTS run_id varchar(128)")
    op.execute("ALTER TABLE llm_usage_records ADD COLUMN IF NOT EXISTS conversation_id uuid")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_usage_tenant_request "
        "ON llm_usage_records (tenant_id, request_id) WHERE request_id IS NOT NULL"
    )

    # Grants: oniva_app gets DML on the tenant tables only; oniva_system on everything.
    for table in (*TENANT_TABLES, "engine_fences"):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO oniva_system')
    for table in TENANT_TABLES:
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO oniva_app')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation_{table} ON "{table}" FOR ALL TO oniva_app '
            f"USING (tenant_id = {TENANT_EXPR}) WITH CHECK (tenant_id = {TENANT_EXPR})"
        )
    op.execute('REVOKE ALL ON "engine_fences" FROM oniva_app')
    op.execute('DROP POLICY IF EXISTS system_only_engine_fences ON "engine_fences"')
    op.execute(
        'CREATE POLICY system_only_engine_fences ON "engine_fences" FOR ALL TO oniva_app '
        "USING (false) WITH CHECK (false)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_llm_usage_tenant_request")
    op.execute("ALTER TABLE llm_usage_records DROP COLUMN IF EXISTS conversation_id")
    op.execute("ALTER TABLE llm_usage_records DROP COLUMN IF EXISTS run_id")
    op.execute("ALTER TABLE llm_usage_records DROP COLUMN IF EXISTS request_id")
    op.execute("DROP TABLE IF EXISTS engine_conversations")
    op.execute("DROP TABLE IF EXISTS engine_fences")
    op.execute("DROP TABLE IF EXISTS tool_invocations")
