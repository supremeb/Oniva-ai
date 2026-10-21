"""U32: the project memory API under the non-bypass ``oniva_app`` role on the Alembic-migrated database.

Drives the real router with a request session built the way ``get_db`` builds it (tenant context set,
commit on success), the real audit chain, and real RLS. Covers cross-tenant isolation, the CLAUDE.md
commit-then-query gotcha through the HTTP layer, purge and the retention sweep. Needs pgvector (skipped
locally without it; CI runs it).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1 import project_memory as api
from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.core.database import get_db, set_tenant_context
from app.services.memory_retention import sweep_expired
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, TB, _role_url

DB = "oniva_project_memory_api"
ADMIN = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
EDITOR = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
PROJ = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
FEATURES = '{"features": {"ENABLE_PROJECT_MEMORY": true}}'


def _exec(db, *sqls):
    eng = create_engine(db["owner"])
    with eng.begin() as c:
        for s in sqls:
            c.execute(text(s))
    eng.dispose()


def _scalar(db, sql, **params):
    eng = create_engine(db["owner"])
    with eng.connect() as c:
        value = c.execute(text(sql), params).scalar()
    eng.dispose()
    return value


@pytest.fixture(scope="module")
def db(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db(DB)
    owner = pg_server.url(DB)
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
        for t in (TA, TB):
            c.execute(text("INSERT INTO tenants (id, name, slug, settings) VALUES (:i, :n, :n, CAST(:s AS jsonb))"),
                      {"i": t, "n": t[:2], "s": FEATURES})
            for uid, role, tag in ((ADMIN[t], "admin", "a"), (EDITOR[t], "member", "e")):
                c.execute(text("INSERT INTO users (id, tenant_id, email, role, is_active) "
                               "VALUES (:u, :t, :e, :r, true)"),
                          {"u": uid, "t": t, "e": f"{tag}{t[:2]}@x.io", "r": role})
            c.execute(text("INSERT INTO projects (id, tenant_id, owner_id, name) VALUES (:p, :t, :u, 'p')"),
                      {"p": PROJ[t], "t": t, "u": EDITOR[t]})
    eng.dispose()
    app_engine = create_async_engine(_role_url(owner, "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    return {"owner": owner, "app": async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)}


@pytest.fixture(autouse=True)
def clean(db):
    _exec(db, "DELETE FROM memory_files", "DELETE FROM memory_bullets")


@pytest.fixture
async def world(db):
    """The real router; the caller (tenant + role) is switched per request via ``as_user``."""
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1/projects")
    current = {}

    async def _db():
        async with db["app"]() as session:  # mirrors core.database._open_request_session
            try:
                await set_tenant_context(session, current["tenant"])
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def as_user(tenant, who="editor"):
        uid, role = (EDITOR[tenant], "member") if who == "editor" else (ADMIN[tenant], "admin")
        current["tenant"] = tenant
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            id=uid, email=f"{who}@x.io", name=who, tenant_id=tenant, tenant_slug="t", role=role,
            auth_provider="local")

    app.dependency_overrides[get_db] = _db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield client, as_user


def base(tenant):
    return f"/api/v1/projects/{PROJ[tenant]}/memory"


async def test_write_commits_and_the_next_request_sees_it(db, world):
    client, as_user = world
    as_user(TA)
    r = await client.put(f"{base(TA)}/files/core.md", json={"content": "prefers EUR"})
    assert r.status_code == 200, r.text
    # committed for real: visible to the owner connection, and to a brand-new tenant-scoped request
    assert _scalar(db, "SELECT content FROM memory_files WHERE tenant_id = :t", t=TA) == "prefers EUR"
    got = await client.get(base(TA))
    assert [f["content"] for f in got.json()["files"]] == ["prefers EUR"]
    again = await client.put(f"{base(TA)}/files/core.md", json={"content": "prefers USD", "expected_version": 1})
    assert again.json()["version"] == 2
    assert (await client.put(f"{base(TA)}/files/core.md", json={"content": "x", "expected_version": 1})).status_code == 409


async def test_audit_events_land_in_the_real_chain(db, world):
    client, as_user = world
    before = _scalar(db, "SELECT coalesce(max(seq), 0) FROM compliance_audit_logs WHERE tenant_id = :t", t=TA)
    as_user(TA)
    await client.put(f"{base(TA)}/files/core.md", json={"content": "secret preference"})
    as_user(TA, "admin")
    assert (await client.delete(base(TA))).status_code == 200
    eng = create_engine(db["owner"])
    with eng.connect() as c:  # the audit log is append-only, so look only at this test's entries
        actions = [r[0] for r in c.execute(
            text("SELECT action FROM compliance_audit_logs WHERE tenant_id = :t AND agent_id = 'project_memory' "
                 "AND seq > :s ORDER BY seq"), {"t": TA, "s": before})]
    eng.dispose()
    assert actions == ["memory_edited", "memory_purged"]
    dump = _scalar(db, "SELECT string_agg(extra_metadata::text, ' ') FROM compliance_audit_logs "
                       "WHERE tenant_id = :t AND agent_id = 'project_memory' AND seq > :s", t=TA, s=before)
    assert "secret preference" not in dump


async def test_cross_tenant_isolation(db, world):
    client, as_user = world
    as_user(TB)
    await client.put(f"{base(TB)}/files/core.md", json={"content": "B secret"})
    _exec(db, f"INSERT INTO memory_bullets (id, tenant_id, project_id, bullet_key, section, text, status) "
              f"VALUES (gen_random_uuid(), '{TB}', '{PROJ[TB]}', 'bk', 's', 'B bullet', 'pending')")
    b_bullet = _scalar(db, "SELECT id::text FROM memory_bullets WHERE tenant_id = :t", t=TB)

    as_user(TA)
    # A cannot see B's project through A's route, nor reach B's rows by guessing ids
    assert (await client.get(base(TB))).status_code == 404
    assert (await client.put(f"{base(TB)}/files/core.md", json={"content": "pwn"})).status_code == 404
    assert (await client.post(f"{base(TA)}/pending/{b_bullet}/approve")).status_code == 404
    assert (await client.delete(f"{base(TA)}/bullets/{b_bullet}")).status_code == 404
    as_user(TA, "admin")
    assert (await client.delete(base(TB))).status_code == 404

    assert _scalar(db, "SELECT content FROM memory_files WHERE tenant_id = :t", t=TB) == "B secret"
    assert _scalar(db, "SELECT status FROM memory_bullets WHERE tenant_id = :t", t=TB) == "pending"


async def test_pending_approve_reject_and_purge_under_rls(db, world):
    client, as_user = world
    _exec(db,
          f"INSERT INTO memory_files (id, tenant_id, project_id, path, content, content_hash, status) "
          f"VALUES (gen_random_uuid(), '{TA}', '{PROJ[TA]}', 'p.md', 'proposed', 'h', 'pending')",
          f"INSERT INTO memory_bullets (id, tenant_id, project_id, bullet_key, section, text, status) "
          f"VALUES (gen_random_uuid(), '{TA}', '{PROJ[TA]}', 'k1', 's', 'one', 'pending'), "
          f"(gen_random_uuid(), '{TA}', '{PROJ[TA]}', 'k2', 's', 'two', 'pending')")
    file_id = _scalar(db, "SELECT id::text FROM memory_files")
    k1 = _scalar(db, "SELECT id::text FROM memory_bullets WHERE bullet_key = 'k1'")
    k2 = _scalar(db, "SELECT id::text FROM memory_bullets WHERE bullet_key = 'k2'")
    as_user(TA)
    assert (await client.post(f"{base(TA)}/pending/{file_id}/approve")).status_code == 200
    assert (await client.post(f"{base(TA)}/pending/{k1}/approve")).status_code == 200
    assert (await client.post(f"{base(TA)}/pending/{k2}/reject")).status_code == 200
    assert _scalar(db, "SELECT status FROM memory_files") == "active"
    assert _scalar(db, "SELECT flags->>'approved_by' FROM memory_files") == EDITOR[TA]
    assert _scalar(db, "SELECT count(*) FROM memory_bullets") == 1
    exported = (await client.post(f"{base(TA)}/export")).json()
    assert exported["meta"] == {"file_count": 1, "bullet_count": 1}

    as_user(TA, "admin")
    purged = await client.delete(base(TA))
    assert purged.json() == {"files_deleted": 1, "bullets_deleted": 1}
    assert _scalar(db, "SELECT count(*) FROM memory_files") == 0
    assert _scalar(db, "SELECT count(*) FROM memory_bullets") == 0


async def test_purge_leaves_the_other_tenants_rows(db, world):
    client, as_user = world
    for t in (TA, TB):
        as_user(t)
        await client.put(f"{base(t)}/files/core.md", json={"content": f"mine {t[:2]}"})
    as_user(TA, "admin")
    assert (await client.delete(base(TA))).json()["files_deleted"] == 1
    assert _scalar(db, "SELECT count(*) FROM memory_files WHERE tenant_id = :t", t=TB) == 1


async def test_retention_sweep_deletes_only_stale_pending_rows_of_the_tenant(db):
    old = datetime.now(timezone.utc) - timedelta(days=45)
    rows = []
    for t in (TA, TB):
        for path, status, ts in (("stale.md", "pending", old), ("fresh.md", "pending", None), ("keep.md", "active", old)):
            rows.append(
                f"INSERT INTO memory_files (id, tenant_id, project_id, path, content, content_hash, status, updated_at) "
                f"VALUES (gen_random_uuid(), '{t}', '{PROJ[t]}', '{path}', 'c', 'h', '{status}', "
                f"{'now()' if ts is None else repr(ts.isoformat())})")
    _exec(db, *rows)
    async with db["app"]() as s:
        await set_tenant_context(s, TA)
        counts = await sweep_expired(s, days=30)
        await s.commit()
    assert counts["files"] == 1  # RLS confines the sweep to tenant A even without a tenant filter
    assert sorted(r for r in _paths(db, TA)) == ["fresh.md", "keep.md"]
    assert sorted(r for r in _paths(db, TB)) == ["fresh.md", "keep.md", "stale.md"]


def _paths(db, tenant):
    eng = create_engine(db["owner"])
    with eng.connect() as c:
        out = [r[0] for r in c.execute(text("SELECT path FROM memory_files WHERE tenant_id = :t"), {"t": tenant})]
    eng.dispose()
    return out
