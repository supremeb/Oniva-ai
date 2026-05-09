"""Create run_events table for unified event observability across agent runs.

Captures a stream of structured events keyed by run_id, enabling per-run
replay, token accounting, and structured tracing.

Uses IF NOT EXISTS guards throughout so the migration is idempotent when the
table was pre-created by the SQLAlchemy model before this migration existed.

Revision ID: 082_create_run_events_table
Revises: 081_workflow_ids_custom_agent
Create Date: 2026-04-04

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "082_create_run_events_table"
down_revision = "081_workflow_ids_custom_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create table (idempotent — the model may have already created it)
    op.execute("""
        CREATE TABLE IF NOT EXISTS run_events (
            id          UUID NOT NULL DEFAULT gen_random_uuid(),
            run_id      UUID NOT NULL,
            session_id  UUID,
            tenant_id   UUID NOT NULL,
            event_type  VARCHAR(100) NOT NULL,
            agent_name  VARCHAR(255),
            "timestamp" TIMESTAMPTZ NOT NULL DEFAULT now(),
            input_tokens  INTEGER,
            output_tokens INTEGER,
            payload     JSONB,
            PRIMARY KEY (id)
        );
    """)

    # Single-column indexes (idempotent)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_run_events_run_id
            ON run_events (run_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_run_events_session_id
            ON run_events (session_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_run_events_tenant_id
            ON run_events (tenant_id);
    """)

    # Composite indexes for common query patterns (idempotent)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_run_events_run_id_timestamp
            ON run_events (run_id, "timestamp");
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_run_events_tenant_timestamp
            ON run_events (tenant_id, "timestamp" DESC);
    """)

    # RLS: tenant isolation
    op.execute("ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        DROP POLICY IF EXISTS tenant_isolation_run_events ON run_events;
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_run_events ON run_events
            FOR ALL
            TO oniva_app
            USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_run_events ON run_events;")
    op.execute("DROP INDEX IF EXISTS ix_run_events_tenant_timestamp;")
    op.execute("DROP INDEX IF EXISTS ix_run_events_run_id_timestamp;")
    op.execute("DROP INDEX IF EXISTS ix_run_events_tenant_id;")
    op.execute("DROP INDEX IF EXISTS ix_run_events_session_id;")
    op.execute("DROP INDEX IF EXISTS ix_run_events_run_id;")
    op.execute("DROP TABLE IF EXISTS run_events;")
