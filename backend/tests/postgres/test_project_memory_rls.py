"""U25: ``memory_files`` / ``memory_bullets`` RLS, uniqueness, cascade and migration 130 on real Postgres.

Runs under the non-bypass ``oniva_app`` role on the Alembic-migrated database (needs pgvector; skipped
locally without it, CI runs it).
"""
from __future__ import annotations

import uuid

import psycopg2
import pytest
from psycopg2 import errors
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import set_tenant_context
from app.models.project_memory import MemoryFile
from app.services import project_memory as pm
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, TB, _role_url

DB = "oniva_project_memory"
USER_A, USER_B = str(uuid.uuid4()), str(uuid.uuid4())
PROJ = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
USER = {TA: USER_A, TB: USER_B}


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
            c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :n)"), {"i": t, "n": t[:2]})
            c.execute(text("INSERT INTO users (id, tenant_id, email, role, is_active) "
                           "VALUES (:u, :t, :e, 'member', true)"), {"u": USER[t], "t": t, "e": f"{t[:2]}@x.io"})
            c.execute(text("INSERT INTO projects (id, tenant_id, owner_id, name) VALUES (:p, :t, :u, 'p')"),
                      {"p": PROJ[t], "t": t, "u": USER[t]})
    eng.dispose()
    app_engine = create_async_engine(_role_url(owner, "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    return {"owner": owner, "app": async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)}


@pytest.fixture(autouse=True)
def clean(db):
    eng = create_engine(db["owner"])
    with eng.begin() as c:
        c.execute(text("DELETE FROM memory_files"))
        c.execute(text("DELETE FROM memory_bullets"))
    eng.dispose()


def _file_sql(tenant, path="core.md", user=None, status="active"):
    return (
        "INSERT INTO memory_files (id, tenant_id, project_id, user_id, path, content, content_hash, status) "
        f"VALUES (gen_random_uuid(), '{tenant}', '{PROJ[tenant]}', {'NULL' if user is None else repr(user)}, "
        f"'{path}', 'c', 'h', '{status}')"
    )


def _bullet_sql(tenant, key="b1"):
    return (
        "INSERT INTO memory_bullets (id, tenant_id, project_id, bullet_key, section, text) "
        f"VALUES (gen_random_uuid(), '{tenant}', '{PROJ[tenant]}', '{key}', 's', 't')"
    )


def _seed(db, *sqls):
    eng = create_engine(db["owner"])
    with eng.begin() as c:
        for s in sqls:
            c.execute(text(s))
    eng.dispose()


def _tenant_conn(db, tenant):
    conn = psycopg2.connect(_role_url(db["owner"], "oniva_app"))
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant,))
    return conn, cur


TABLES = ("memory_files", "memory_bullets")


def _seed_both(db, tenant, key="b1"):
    _seed(db, _file_sql(tenant), _bullet_sql(tenant, key))


@pytest.mark.parametrize("table", TABLES)
def test_without_a_tenant_oniva_app_sees_no_rows(db, table):
    _seed_both(db, TA)
    conn = psycopg2.connect(_role_url(db["owner"], "oniva_app"))
    cur = conn.cursor()
    cur.execute(f"SELECT count(*) FROM {table}")
    assert cur.fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("table", TABLES)
def test_a_tenant_never_sees_another_tenants_rows(db, table):
    _seed_both(db, TA)
    _seed_both(db, TB)
    for me in (TA, TB):
        conn, cur = _tenant_conn(db, me)
        cur.execute(f"SELECT tenant_id::text FROM {table}")
        assert cur.fetchall() == [(me,)]
        cur.execute(f"UPDATE {table} SET updated_at = now() WHERE tenant_id <> %s", (me,))
        assert cur.rowcount == 0
        cur.execute(f"DELETE FROM {table} WHERE tenant_id <> %s", (me,))
        assert cur.rowcount == 0
        conn.close()
    eng = create_engine(db["owner"])
    with eng.connect() as c:
        assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 2
    eng.dispose()


@pytest.mark.parametrize("make", [_file_sql, _bullet_sql])
def test_with_check_blocks_a_cross_tenant_insert(db, make):
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(psycopg2.Error):
        cur.execute(make(TB))
    conn.close()


@pytest.mark.parametrize("table", TABLES)
def test_both_tables_enable_and_force_rls_with_a_policy(db, table):
    eng = create_engine(db["owner"])
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity, "
            "(SELECT count(*) FROM pg_policies WHERE tablename = :t) FROM pg_class WHERE relname = :t"),
            {"t": table}).one()
    eng.dispose()
    assert tuple(row) == (True, True, 1)


def test_path_is_unique_per_scope(db):
    _seed(db, _file_sql(TA))
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(errors.UniqueViolation):
        cur.execute(_file_sql(TA))  # same project-level path
    cur.execute(_file_sql(TA, user=USER_A))  # a per-user file may share the path
    with pytest.raises(errors.UniqueViolation):
        cur.execute(_file_sql(TA, user=USER_A))
    cur.execute(_file_sql(TA, path="other.md"))
    conn.close()
    _seed(db, _file_sql(TB))  # another tenant's project: no clash


def test_bullet_key_is_unique_per_project(db):
    _seed(db, _bullet_sql(TA))
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(errors.UniqueViolation):
        cur.execute(_bullet_sql(TA))
    conn.close()


def test_check_constraints_reject_bad_status_and_counters(db):
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(errors.CheckViolation):
        cur.execute(_file_sql(TA, status="bogus"))
    with pytest.raises(errors.CheckViolation):
        cur.execute(_bullet_sql(TA).replace("'t')", "'t')").replace(
            "section, text)", "section, text, helpful)").replace("'t')", "'t', -1)", 1))
    conn.close()


def test_deleting_the_project_deletes_its_rows(db):
    _seed_both(db, TA)
    _seed_both(db, TB)
    _seed(db, f"DELETE FROM projects WHERE id = '{PROJ[TA]}'")
    eng = create_engine(db["owner"])
    with eng.connect() as c:
        for t in TABLES:
            tenants = [r[0] for r in c.execute(text(f"SELECT tenant_id::text FROM {t}"))]
            assert tenants == [TB], t
    eng.dispose()
    _seed(db, f"INSERT INTO projects (id, tenant_id, owner_id, name) VALUES ('{PROJ[TA]}', '{TA}', '{USER_A}', 'p')")


async def test_service_keeps_tenant_context_across_commit_and_refresh(db):
    """CLAUDE.md gotcha: the tenant GUC is transaction-local; commit then refresh must still see the row."""
    async with db["app"]() as s:
        await set_tenant_context(s, TA)
        row = await pm.create(s, tenant_id=uuid.UUID(TA), project_id=uuid.UUID(PROJ[TA]), path="core.md",
                              content="prefers EUR", source_kind="user")
        await s.commit()
        await s.refresh(row)  # raises (row hidden by RLS) if the tenant context was lost
        assert row.version == 1
        got = await pm.view(s, tenant_id=uuid.UUID(TA), project_id=uuid.UUID(PROJ[TA]), path="core.md")
        assert got["content"] == "prefers EUR"
        await pm.str_replace(s, tenant_id=uuid.UUID(TA), project_id=uuid.UUID(PROJ[TA]), path="core.md",
                             old="EUR", new="USD")
        await s.commit()
        assert await pm.render_core(s, tenant_id=uuid.UUID(TA), project_id=uuid.UUID(PROJ[TA])) == "prefers USD"


async def test_service_as_oniva_app_cannot_reach_another_tenant(db):
    _seed(db, _file_sql(TB))
    async with db["app"]() as s:
        await set_tenant_context(s, TA)
        # Even naming the other tenant's ids explicitly returns nothing.
        with pytest.raises(pm.MemoryStoreError) as ei:
            await pm.view(s, tenant_id=uuid.UUID(TB), project_id=uuid.UUID(PROJ[TB]), path="core.md")
        assert ei.value.code == "not_found"
        assert (await s.execute(select(MemoryFile))).scalars().all() == []
        # And a cross-tenant write is refused by WITH CHECK (surfaced as a database error).
        with pytest.raises(Exception):
            await pm.create(s, tenant_id=uuid.UUID(TB), project_id=uuid.UUID(PROJ[TB]), path="x.md",
                            content="x", source_kind="user")


# --- migration 130 -------------------------------------------------------------

def _scalar(url, sql):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text(sql)).scalar()
    finally:
        eng.dispose()


def test_migration_130_upgrade_downgrade_upgrade(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    name = "oniva_project_memory_mig"
    pg_server.recreate_db(name)
    url = pg_server.url(name)
    assert run_alembic(url, "upgrade", "head").returncode == 0
    assert _scalar(url, "SELECT version_num FROM alembic_version") == "130_project_memory"
    for t in TABLES:
        assert _scalar(url, f"SELECT to_regclass('public.{t}')")
    down = run_alembic(url, "downgrade", "121_encrypt_webhook_secrets")
    assert down.returncode == 0, down.stderr[-2000:]
    assert _scalar(url, "SELECT version_num FROM alembic_version") == "121_encrypt_webhook_secrets"
    for t in TABLES:
        assert _scalar(url, f"SELECT to_regclass('public.{t}')") is None
    up = run_alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    for t in TABLES:
        assert _scalar(url, f"SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = '{t}'")
        assert _scalar(url, f"SELECT count(*) FROM pg_policies WHERE tablename = '{t}'") == 1
    # Re-running the upgrade statements against existing tables (the baseline already builds them on a
    # fresh database) is a no-op, not an error.
    assert run_alembic(url, "downgrade", "-1").returncode == 0
    assert run_alembic(url, "upgrade", "head").returncode == 0
    pg_server.recreate_db(name)
