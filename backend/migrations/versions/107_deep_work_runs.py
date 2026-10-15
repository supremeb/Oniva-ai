"""Deep Work run state on the engine (deep-work-on-engine plan, U3).

Revision ID: 107_deep_work_runs
Revises: 106_engine_conversation_pin_archive

Adds ``deep_work_runs`` (one row per engine Deep Work session, unique on the root
``conversation_id``; tenant RLS ENABLE + FORCE). Idempotent: the baseline builds its schema from
the live models, so a fresh database may already hold the table when this revision runs.
"""
from alembic import op

revision = "107_deep_work_runs"
down_revision = "106_engine_conversation_pin_archive"
branch_labels = None
depends_on = None

TENANT_EXPR = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"
TABLE = "deep_work_runs"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL UNIQUE
                REFERENCES engine_conversations(conversation_id) ON DELETE CASCADE,
            query text NOT NULL,
            enriched_query text,
            status varchar(20) NOT NULL DEFAULT 'running',
            phase varchar(20) NOT NULL DEFAULT 'clarify',
            round integer NOT NULL DEFAULT 0,
            plan jsonb,
            rounds jsonb NOT NULL DEFAULT '[]'::jsonb,
            reflections jsonb NOT NULL DEFAULT '[]'::jsonb,
            artifacts jsonb NOT NULL DEFAULT '[]'::jsonb,
            job_id uuid REFERENCES background_jobs(id) ON DELETE SET NULL,
            is_engine boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_tenant_id ON {TABLE} (tenant_id)")

    op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{TABLE}" TO oniva_system')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{TABLE}" TO oniva_app')
    op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{TABLE} ON "{TABLE}"')
    op.execute(
        f'CREATE POLICY tenant_isolation_{TABLE} ON "{TABLE}" FOR ALL TO oniva_app '
        f"USING (tenant_id = {TENANT_EXPR}) WITH CHECK (tenant_id = {TENANT_EXPR})"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
