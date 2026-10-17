"""U6: migration 113 copies open review tickets into the ledger, idempotently, leaving legacy rows alone."""
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.postgres.conftest import run_alembic

TENANT = str(uuid.uuid4())
OPEN_A, OPEN_B, CLOSED = (str(uuid.uuid4()) for _ in range(3))


@pytest.fixture
def at_112(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_retire_tickets")
    url = pg_server.url("oniva_retire_tickets")
    res = run_alembic(url, "upgrade", "112_deep_work_awaiting_input")
    assert res.returncode == 0, res.stderr[-3000:]
    return url


def _rows(url, sql, **params):
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            res = c.execute(text(sql), params)
            return res.fetchall() if res.returns_rows else []
    finally:
        engine.dispose()


def test_open_tickets_are_copied_once_and_legacy_rows_untouched(at_112):
    url = at_112
    _rows(url, "INSERT INTO tenants (id, name, slug) VALUES (:t, 'n', :s)", t=TENANT, s=f"s{TENANT[:8]}")
    for tid, status in ((OPEN_A, "pending"), (OPEN_B, "escalated"), (CLOSED, "approved")):
        _rows(url, "INSERT INTO human_review_tickets (id, tenant_id, agent_id, title, description, "
                   "risk_level, priority, review_type, review_data, status, sla_breached) "
                   "VALUES (:i, :t, 'a', 'T', 'Do it', 'high', 'normal', 'code_execution', "
                   "'{\"code\": \"x\"}', :s, 'false')", i=tid, t=TENANT, s=status)
    before = _rows(url, "SELECT * FROM human_review_tickets ORDER BY id")

    assert run_alembic(url, "upgrade", "113_retire_review_tickets").returncode == 0
    copied = _rows(url, "SELECT legacy_ticket_id::text, kind, status, reason, "
                        "arguments_preview->>'title', tenant_id::text FROM tool_approvals "
                        "ORDER BY legacy_ticket_id")
    assert sorted(r[0] for r in copied) == sorted([OPEN_A, OPEN_B])
    assert all(r[1:3] == ("review", "pending") and r[3] == "Do it" and r[4] == "T" and r[5] == TENANT
               for r in copied)

    # Re-running the copy adds nothing (idempotent on legacy_ticket_id).
    assert run_alembic(url, "downgrade", "112_deep_work_awaiting_input").returncode == 0
    assert run_alembic(url, "upgrade", "113_retire_review_tickets").returncode == 0
    assert _rows(url, "SELECT count(*) FROM tool_approvals")[0][0] == 2
    assert _rows(url, "SELECT * FROM human_review_tickets ORDER BY id") == before

    # FORCE RLS is back on for the ledger.
    assert _rows(url, "SELECT relforcerowsecurity FROM pg_class WHERE relname='tool_approvals'")[0][0] is True
