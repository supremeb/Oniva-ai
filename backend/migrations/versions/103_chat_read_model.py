"""Chat history read model (chat-on-engine plan, U10).

Revision ID: 103_chat_read_model
Revises: 103a_engine_conversation_selections

Adds ``chat_session_messages`` (one row per legacy chat message, parts copied from the JSONB
column; unique ``(session_id, seq)``; tenant RLS FORCE) and three columns on
``engine_conversations`` (``title``, ``updated_at``, ``legacy_session_id``) used by the union
listing and by "continue legacy session".

Every statement is idempotent: the baseline builds its schema from the live models, so a fresh
database may already hold these objects when this revision runs. ``chat_sessions`` rows are never
modified.
"""
from alembic import op

revision = "103_chat_read_model"
down_revision = "103a_engine_conversation_selections"
branch_labels = None
depends_on = None

TENANT_EXPR = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"
TABLE = "chat_session_messages"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            seq integer NOT NULL,
            role varchar(20) NOT NULL,
            content text NOT NULL DEFAULT '',
            parts jsonb NOT NULL DEFAULT '[]'::jsonb,
            parts_valid boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_chat_session_messages_session_seq UNIQUE (session_id, seq)
        )
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_tenant_id ON {TABLE} (tenant_id)")

    op.execute("ALTER TABLE engine_conversations ADD COLUMN IF NOT EXISTS title varchar(255)")
    op.execute(
        "ALTER TABLE engine_conversations "
        "ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE engine_conversations ADD COLUMN IF NOT EXISTS legacy_session_id uuid "
        "REFERENCES chat_sessions(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_engine_conversations_legacy_session_id "
        "ON engine_conversations (legacy_session_id)"
    )

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
    op.execute("DROP INDEX IF EXISTS ix_engine_conversations_legacy_session_id")
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS legacy_session_id")
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE engine_conversations DROP COLUMN IF EXISTS title")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
