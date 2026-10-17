"""Approval ledger (Approvals & Policy plan, U2 / KTD3).

Revision ID: 111_tool_approvals
Revises: 110_audit_chain

Adds ``tool_approvals`` (tenant RLS ENABLE + FORCE) with the two partial unique keys:
``(tenant_id, conversation_id, tool_task_id)`` for the decision route and
``(tenant_id, run_id, tool_call_id)`` for the execute_tool guard and ``requestId`` idempotency.
Idempotent: the baseline builds its schema from the live models, so a fresh database may already
hold the table when this revision runs.
"""
from alembic import op

revision = "111_tool_approvals"
down_revision = "110_audit_chain"
branch_labels = None
depends_on = None

TENANT_EXPR = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"
TABLE = "tool_approvals"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            kind varchar(20) NOT NULL DEFAULT 'tool_call',
            status varchar(20) NOT NULL DEFAULT 'pending',
            project_id uuid,
            conversation_id uuid REFERENCES engine_conversations(conversation_id) ON DELETE CASCADE,
            engine_conversation_id varchar(255),
            run_id varchar(255),
            tool_call_id varchar(255),
            tool_task_id varchar(255),
            tool_name varchar(255),
            arguments_preview jsonb,
            arguments_hash varchar(64),
            reason text,
            rule varchar(30) NOT NULL DEFAULT 'requires_approval',
            approver_role varchar(20) NOT NULL DEFAULT 'member',
            requested_by uuid REFERENCES users(id) ON DELETE SET NULL,
            decided_by uuid REFERENCES users(id) ON DELETE SET NULL,
            decision varchar(10),
            note text,
            self_approved boolean NOT NULL DEFAULT false,
            taint_sources jsonb,
            expires_at timestamptz,
            decided_at timestamptz,
            consumed_at timestamptz,
            legacy_ticket_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_tool_approvals_kind CHECK (kind IN ('tool_call','review','question')),
            CONSTRAINT ck_tool_approvals_status CHECK (
                status IN ('pending','deciding','approved','denied','answered','expired','cancelled'))
        )
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_tenant_id ON {TABLE} (tenant_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_tenant_status ON {TABLE} (tenant_id, status)")
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{TABLE}_root_task "
        f"ON {TABLE} (tenant_id, conversation_id, tool_task_id) WHERE tool_task_id IS NOT NULL"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{TABLE}_run_call "
        f"ON {TABLE} (tenant_id, run_id, tool_call_id) WHERE run_id IS NOT NULL AND tool_call_id IS NOT NULL"
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
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
