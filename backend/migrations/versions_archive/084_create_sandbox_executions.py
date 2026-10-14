"""Catch-up: create sandbox_executions (previously created only by init_db create_all)

Revision ID: 084_create_sandbox_executions
Revises: 083_agent_versioning_lifecycle
Create Date: 2026-10-14

The SandboxExecution model had no migration; the table only existed on databases where
``init_db()`` ran ``Base.metadata.create_all``. Idempotent (IF NOT EXISTS) so it is a no-op on
those databases. RLS for this table is deliberately not added here (tenancy unit owns it).
The ``vector`` extension is already created by 001/003.
"""

from alembic import op

revision = "084_create_sandbox_executions"
down_revision = "083_agent_versioning_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sandbox_executions (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            sandbox_id VARCHAR(255) NOT NULL,
            code TEXT NOT NULL,
            status VARCHAR(50) NOT NULL,
            stdout TEXT,
            stderr TEXT,
            duration_seconds DOUBLE PRECISION,
            created_at TIMESTAMPTZ DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_sandbox_executions_session_id ON sandbox_executions (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_executions_tenant_created "
               "ON sandbox_executions (tenant_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_executions_status_active "
               "ON sandbox_executions (status) WHERE status NOT IN ('completed', 'failed')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sandbox_executions")
