"""Project memory tables (evals/context/memory plan, U25 / KTD16, KTD19).

Revision ID: 130_project_memory
Revises: 121_encrypt_webhook_secrets

Adds ``memory_files`` (path-addressed text, versioned) and ``memory_bullets`` (structured playbook
items with helpful/harmful counters). Both are tenant tables: RLS ENABLE + FORCE with the standard
``tenant_isolation_*`` policy for ``oniva_app``, and ``project_id`` foreign keys with ON DELETE CASCADE
(erasure follows the project). ``flags`` (jsonb) carries instruction-shaped-text flags for the UI (U27).
The file path is unique per (tenant, project, scope) through two partial unique indexes (project-level
rows where ``user_id`` is null, per-user rows otherwise). Idempotent: the baseline builds from live
models, so a fresh database may already hold the tables when this revision runs. This plan owns
migrations 130-139; the coordinator rebases only ``down_revision`` when other plans land first.
"""
from alembic import op

revision = "130_project_memory"
down_revision = "121_encrypt_webhook_secrets"
branch_labels = None
depends_on = None

TENANT_EXPR = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"
TABLES = ("memory_files", "memory_bullets")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_files (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id uuid REFERENCES users(id) ON DELETE CASCADE,
            path varchar(128) NOT NULL,
            content text NOT NULL DEFAULT '',
            content_hash varchar(64) NOT NULL,
            version integer NOT NULL DEFAULT 1,
            status varchar(10) NOT NULL DEFAULT 'active',
            source_kind varchar(20) NOT NULL DEFAULT 'user',
            source_conversation_id uuid,
            flags jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_memory_files_status CHECK (status IN ('active','pending')),
            CONSTRAINT ck_memory_files_version CHECK (version >= 1)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_files_project_path "
        "ON memory_files (tenant_id, project_id, path) WHERE user_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_files_user_path "
        "ON memory_files (tenant_id, project_id, user_id, path) WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_files_tenant_project "
        "ON memory_files (tenant_id, project_id, status)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_bullets (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            bullet_key varchar(64) NOT NULL,
            section varchar(64) NOT NULL,
            text text NOT NULL,
            helpful integer NOT NULL DEFAULT 0,
            harmful integer NOT NULL DEFAULT 0,
            last_used_at timestamptz,
            status varchar(10) NOT NULL DEFAULT 'active',
            source_kind varchar(20) NOT NULL DEFAULT 'curator',
            source_conversation_id uuid,
            source_run_id varchar(255),
            flags jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_memory_bullets_status CHECK (status IN ('active','pending')),
            CONSTRAINT ck_memory_bullets_counters CHECK (helpful >= 0 AND harmful >= 0)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_bullets_key "
        "ON memory_bullets (tenant_id, project_id, bullet_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_bullets_tenant_project "
        "ON memory_bullets (tenant_id, project_id, status)"
    )

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO oniva_system')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO oniva_app')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation_{table} ON "{table}" FOR ALL TO oniva_app '
            f"USING (tenant_id = {TENANT_EXPR}) WITH CHECK (tenant_id = {TENANT_EXPR})"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_bullets")
    op.execute("DROP TABLE IF EXISTS memory_files")
