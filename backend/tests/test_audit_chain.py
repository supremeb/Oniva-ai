"""U1 - the tamper-evident audit chain on real Postgres.

Runs on the Alembic-migrated database (needs pgvector; skipped locally without it, CI runs it).
The immutability trigger is disabled here so the ORM can stage the tampering the chain must
detect; the trigger itself is exercised for real in ``tests/postgres/test_audit_immutability.py``.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import create_engine, insert, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import set_tenant_context
from app.models.compliance_audit_log import AuditAction, ComplianceAuditLog
from app.security.kms import LocalDevKMS
from app.services.audit_chain import AuditChain
from tests.postgres.conftest import pg_has_vector, pg_server, run_alembic  # noqa: F401
from tests.postgres.test_rls import PW, TA, TB, _role_url, seed_row

UA = str(uuid.uuid4())


@pytest.fixture(scope="module")
def chain_db(pg_server, pg_has_vector, tmp_path_factory):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_audit_chain")
    owner = pg_server.url("oniva_audit_chain")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
        for t, slug in ((TA, "ta"), (TB, "tb")):
            c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :s)"),
                      {"i": t, "n": slug, "s": slug})
        assert seed_row(c, "users", TA, {"id": f"'{UA}'"}), "cannot seed users"
        # Test-only: let the ORM stage tampering; the real trigger is tested separately.
        c.execute(text("ALTER TABLE compliance_audit_logs DISABLE TRIGGER compliance_audit_logs_immutable"))
    eng.dispose()

    app_engine = create_async_engine(_role_url(owner, "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    sys_engine = create_async_engine(_role_url(owner, "oniva_system", "postgresql+asyncpg"), poolclass=NullPool)
    own_engine = create_async_engine(owner.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    kms = LocalDevKMS(tmp_path_factory.mktemp("audit-kms"))
    yield {
        "owner": owner,
        "app": async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False),
        "own": async_sessionmaker(own_engine, class_=AsyncSession, expire_on_commit=False),
        "kms": kms,
    }


@pytest.fixture(autouse=True)
def clean_chain(chain_db):
    eng = create_engine(chain_db["owner"])
    with eng.begin() as c:
        c.execute(text("DELETE FROM compliance_audit_logs"))
        c.execute(text("DELETE FROM audit_chain_heads"))
    eng.dispose()


@asynccontextmanager
async def app_factory(db, tenant=TA):
    async with db["app"]() as session:
        await set_tenant_context(session, tenant)
        yield session
        await session.commit()


@asynccontextmanager
async def own_session(db):
    async with db["own"]() as session:
        yield session
        await session.commit()


async def do_append(db, tenant=TA, **kwargs) -> ComplianceAuditLog:
    async with app_factory(db, tenant) as session:
        chain = AuditChain(session, kms=db["kms"])
        return await chain.append(
            tenant_id=tenant, agent_id="agent", action=AuditAction.AGENT_DECISION,
            decision=kwargs.pop("decision", "approve"), rationale=kwargs.pop("rationale", "because"),
            **kwargs,
        )


async def verify(db, tenant=TA):
    async with app_factory(db, tenant) as session:
        return await AuditChain(session, kms=db["kms"]).verify_chain(tenant)


async def test_three_sequential_appends_form_a_chain(chain_db):
    rows = [await do_append(chain_db) for _ in range(3)]
    assert [r.seq for r in rows] == [1, 2, 3]
    assert rows[0].previous_hash is None
    assert rows[1].previous_hash == rows[0].entry_hash
    assert rows[2].previous_hash == rows[1].entry_hash
    v = await verify(chain_db)
    assert v == {"ok": True, "checked": 3, "legacy_unchained": 0, "first_break": None}


async def test_restart_links_to_the_last_committed_entry(chain_db):
    first = await do_append(chain_db)
    second = await do_append(chain_db)  # a fresh session/chain instance, as after a restart
    assert second.seq == 2 and second.previous_hash == first.entry_hash
    # the head was not reset to None
    async with own_session(chain_db) as s:
        head = (await s.execute(text(
            "SELECT seq, head_hash FROM audit_chain_heads WHERE tenant_id = :t"), {"t": TA})).one()
    assert head[0] == 2 and head[1] == second.entry_hash


async def test_two_tenants_have_independent_chains(chain_db):
    a1 = await do_append(chain_db, TA)
    b1 = await do_append(chain_db, TB)
    a2 = await do_append(chain_db, TA)
    b2 = await do_append(chain_db, TB)
    assert [a1.seq, a2.seq] == [1, 2]
    assert [b1.seq, b2.seq] == [1, 2]
    assert b1.previous_hash is None
    assert (await verify(chain_db, TA))["checked"] == 2
    assert (await verify(chain_db, TB))["checked"] == 2


async def test_concurrent_appends_are_serialized(chain_db):
    async def one():
        async with app_factory(chain_db) as session:
            return await AuditChain(session, kms=chain_db["kms"]).append(
                tenant_id=TA, agent_id="a", action=AuditAction.AGENT_DECISION,
                decision="d", rationale="r")

    rows = await asyncio.gather(*(one() for _ in range(20)))
    assert sorted(r.seq for r in rows) == list(range(1, 21))
    v = await verify(chain_db)
    assert v == {"ok": True, "checked": 20, "legacy_unchained": 0, "first_break": None}


async def test_missing_head_row_is_rebuilt_on_append(chain_db, caplog):
    await do_append(chain_db)
    second = await do_append(chain_db)
    async with own_session(chain_db) as s:
        await s.execute(text("DELETE FROM audit_chain_heads WHERE tenant_id = :t"), {"t": TA})
    with caplog.at_level(logging.INFO, logger="app.services.audit_chain"):
        third = await do_append(chain_db)
    assert third.seq == 3 and third.previous_hash == second.entry_hash
    assert "audit_head_repaired" in caplog.text
    assert (await verify(chain_db))["ok"] is True


async def test_wrong_head_hash_is_repaired_on_append(chain_db, caplog):
    first = await do_append(chain_db)
    async with own_session(chain_db) as s:
        await s.execute(text("UPDATE audit_chain_heads SET head_hash = 'deadbeef' WHERE tenant_id = :t"),
                        {"t": TA})
    with caplog.at_level(logging.INFO, logger="app.services.audit_chain"):
        second = await do_append(chain_db)
    assert second.seq == 2 and second.previous_hash == first.entry_hash
    assert "audit_head_repaired" in caplog.text
    assert (await verify(chain_db))["ok"] is True


async def test_actor_snapshot_is_hashed_but_user_id_is_not(chain_db):
    row = await do_append(chain_db, user_id=UA, actor_email="ann@example.com", actor_name="Ann")
    assert row.actor_email == "ann@example.com" and row.actor_name == "Ann"
    async with own_session(chain_db) as s:
        await s.execute(update(ComplianceAuditLog).where(ComplianceAuditLog.id == row.id)
                        .values(actor_email="evil@example.com"))
    v = await verify(chain_db)
    assert v["ok"] is False and v["first_break"]["seq"] == row.seq
    async with own_session(chain_db) as s:
        await s.execute(update(ComplianceAuditLog).where(ComplianceAuditLog.id == row.id)
                        .values(actor_email="ann@example.com", user_id=None))
    assert (await verify(chain_db))["ok"] is True


async def test_edited_decision_is_detected(chain_db):
    row = await do_append(chain_db, decision="approve")
    async with own_session(chain_db) as s:
        await s.execute(update(ComplianceAuditLog).where(ComplianceAuditLog.id == row.id)
                        .values(decision="deny"))
    v = await verify(chain_db)
    assert v["ok"] is False and v["first_break"] == {"seq": row.seq, "reason": "entry_hash"}


async def test_edited_rationale_is_detected(chain_db):
    row = await do_append(chain_db, rationale="because")
    async with own_session(chain_db) as s:
        await s.execute(update(ComplianceAuditLog).where(ComplianceAuditLog.id == row.id)
                        .values(rationale="tampered"))
    v = await verify(chain_db)
    assert v["ok"] is False and v["first_break"]["seq"] == row.seq


async def test_removed_middle_row_is_detected(chain_db):
    rows = [await do_append(chain_db) for _ in range(3)]
    async with own_session(chain_db) as s:
        await s.execute(text("DELETE FROM compliance_audit_logs WHERE id = :i"), {"i": rows[1].id})
    v = await verify(chain_db)
    assert v["ok"] is False
    assert v["first_break"]["seq"] == rows[2].seq


async def test_forged_signature_is_detected(chain_db):
    row = await do_append(chain_db)
    forged = base64.b64encode(chain_db["kms"].sign(b"forged")).decode()
    async with own_session(chain_db) as s:
        await s.execute(update(ComplianceAuditLog).where(ComplianceAuditLog.id == row.id)
                        .values(signature=forged))
    v = await verify(chain_db)
    assert v["ok"] is False and v["first_break"] == {"seq": row.seq, "reason": "signature"}


async def test_duplicate_seq_is_rejected_by_the_unique_index(chain_db):
    row = await do_append(chain_db)
    async with own_session(chain_db) as s:
        with pytest.raises(IntegrityError):
            await s.execute(insert(ComplianceAuditLog).values(
                tenant_id=uuid.UUID(TA), agent_id="x", action="agent_decision", decision="d",
                rationale="r", data_sources=[], tools_called=[], signature="x", entry_hash="y",
                seq=row.seq, retention_until=row.retention_until))
        await s.rollback()


async def test_legacy_rows_are_counted_not_broken(chain_db):
    row = await do_append(chain_db)
    async with own_session(chain_db) as s:
        await s.execute(insert(ComplianceAuditLog).values(
            tenant_id=uuid.UUID(TA), agent_id="legacy", action="agent_decision", decision="d",
            rationale="r", data_sources=[], tools_called=[], signature="x", entry_hash="legacy",
            seq=None, retention_until=row.retention_until))
    v = await verify(chain_db)
    assert v["ok"] is True and v["checked"] == 1 and v["legacy_unchained"] == 1
