"""Carry open review tickets into the approval ledger (Approvals & Policy plan, U6 / KTD7).

Revision ID: 113_retire_review_tickets
Revises: 112_deep_work_awaiting_input

Data only. Copies every open ``human_review_tickets`` row (pending, assigned, in_review,
escalated) into ``tool_approvals`` with ``kind='review'`` and ``legacy_ticket_id``, skipping any
ticket already copied, so re-running it adds nothing. Legacy rows are never modified.

The ledger has FORCE row level security with a policy for ``oniva_app`` only, so the owner role
running this migration would see no rows under it; FORCE is switched off for the copy and each
table's original setting is restored afterwards (metadata only, in the same transaction).
"""
from alembic import op
from sqlalchemy import text

revision = "113_retire_review_tickets"
down_revision = "112_deep_work_awaiting_input"
branch_labels = None
depends_on = None

COPY_SQL = """
INSERT INTO tool_approvals (
    id, tenant_id, kind, status, requested_by, reason, rule, approver_role,
    arguments_preview, expires_at, legacy_ticket_id, created_at, updated_at
)
SELECT
    gen_random_uuid(), t.tenant_id, 'review', 'pending', t.requested_by, t.description,
    'requires_approval', 'admin',
    jsonb_build_object(
        'title', t.title, 'review_type', t.review_type, 'risk_level', t.risk_level,
        'priority', t.priority, 'agent_id', t.agent_id,
        'session_id', t.session_id, 'workflow_id', t.workflow_id,
        'review_data', COALESCE(t.review_data, '{}'::jsonb)
    ),
    t.due_at, t.id, t.created_at, now()
FROM human_review_tickets t
WHERE t.status IN ('pending', 'assigned', 'in_review', 'escalated')
  AND NOT EXISTS (SELECT 1 FROM tool_approvals a WHERE a.legacy_ticket_id = t.id)
"""


def _relax_force(bind, tables):
    """Return the tables that had FORCE RLS and turn it off for them."""
    forced = []
    for table in tables:
        flag = bind.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:t)"), {"t": table}
        ).scalar()
        if flag:
            forced.append(table)
            bind.execute(text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
    return forced


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not bind.execute(text("SELECT to_regclass('human_review_tickets')")).scalar():
        return
    forced = _relax_force(bind, ("tool_approvals", "human_review_tickets"))
    try:
        bind.execute(text(COPY_SQL))
    finally:
        for table in forced:
            bind.execute(text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))


def downgrade() -> None:
    # Data only: the copied ledger rows are history now and are left in place.
    pass
