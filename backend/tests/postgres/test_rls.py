"""U9 - real tenancy: RLS applies to the request role, system role handles non-request work.

Runs on the Alembic-migrated database (needs pgvector; skipped locally without it, CI runs it).
Roles are exercised for real: ``oniva_app`` (NOSUPERUSER NOBYPASSRLS) and ``oniva_system``
(BYPASSRLS) both log in with passwords set here; the migration creates them password-less.
"""
from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.postgres.conftest import run_alembic

TA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PW = "rls-test-pw"

# Tables whose policy is user-level (app.current_user_id) rather than tenant-only.
USER_LEVEL = {"user_preferences", "episodic_memories"}


def _role_url(owner_url: str, role: str, driver: str = "postgresql") -> str:
    p = urlsplit(owner_url)
    return f"{driver}://{role}:{PW}@{p.hostname}:{p.port}{p.path}"


def _tenant_tables(conn) -> list[str]:
    return [r[0] for r in conn.execute(text(
        "SELECT table_name FROM information_schema.columns c "
        "JOIN pg_class k ON k.relname = c.table_name AND k.relkind = 'r' "
        "WHERE c.table_schema='public' AND c.column_name='tenant_id' ORDER BY 1"))]


def _dummy(col: dict, tenant: str) -> str | None:
    """SQL literal for a NOT NULL column without a default; None => cannot synthesize."""
    n, t, udt = col["column_name"], col["data_type"], col["udt_name"]
    ml = col["character_maximum_length"]
    if n == "tenant_id":
        return f"'{tenant}'"
    if udt == "uuid":
        return "gen_random_uuid()"
    if t in ("character varying", "text"):
        return f"left(md5(random()::text), {min(ml or 12, 12)})"
    if t in ("integer", "bigint", "smallint", "numeric", "double precision", "real"):
        return "0"
    if t == "boolean":
        return "false"
    if t in ("jsonb", "json"):
        return "'{}'::" + t
    if t.startswith("timestamp"):
        return "now()"
    if t == "ARRAY":
        return "'{}'"
    if t == "USER-DEFINED":
        return f"(SELECT e.enumlabel FROM pg_enum e JOIN pg_type ty ON ty.oid=e.enumtypid " \
               f"WHERE ty.typname='{udt}' ORDER BY e.enumsortorder LIMIT 1)::{udt}"
    return None


# Values for NOT NULL columns guarded by CHECK constraints (random text would be rejected).
SEED_DEFAULTS = {
    "background_jobs": {"status": "'pending'", "trigger": "'manual'"},
}


def seed_row(conn, table: str, tenant: str, overrides: dict | None = None) -> bool:
    """Insert one synthetic row for ``table`` (FKs disabled for the session). True on success."""
    cols = conn.execute(text(
        "SELECT column_name, data_type, udt_name, character_maximum_length, is_nullable, "
        "column_default, is_generated FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"), {"t": table}).mappings().all()
    names, vals = [], []
    overrides = {**SEED_DEFAULTS.get(table, {}), **(overrides or {})}
    for c in cols:
        n = c["column_name"]
        if n in overrides:
            names.append(n); vals.append(overrides[n]); continue
        if n == "tenant_id":
            names.append(n); vals.append(f"'{tenant}'"); continue
        if c["is_nullable"] == "YES" or c["column_default"] or c["is_generated"] == "ALWAYS":
            continue
        v = _dummy(c, tenant)
        if v is None:
            return False
        names.append(n); vals.append(v)
    if table == "tenants":
        return False
    sp = conn.begin_nested()
    try:
        conn.execute(text(
            f'INSERT INTO "{table}" ({", ".join(chr(34)+x+chr(34) for x in names)}) '
            f'VALUES ({", ".join(vals)})'))
        sp.commit()
        return True
    except Exception:
        sp.rollback()
        return False


@pytest.fixture(scope="module")
def tenancy_db(pg_server, pg_has_vector):
    """Migrated DB + roles with passwords + two tenants with one synthetic row per table."""
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_rls")
    owner = pg_server.url("oniva_rls")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    seeded: dict[str, set[str]] = {TA: set(), TB: set()}
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
        c.execute(text("SET session_replication_role = replica"))  # skip FK triggers for seeding
        for t, slug in ((TA, "ta"), (TB, "tb")):
            c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :s)"),
                      {"i": t, "n": slug, "s": slug})
        tables = _tenant_tables(c)
        for t in (TA, TB):
            for tbl in tables:
                if seed_row(c, tbl, t):
                    seeded[t].add(tbl)
    eng.dispose()
    return {"owner": owner, "tables": tables, "seeded": seeded}


def _app_conn(tenancy_db, role="oniva_app"):
    return psycopg2.connect(_role_url(tenancy_db["owner"], role))


def _visible(tenancy_db, table, tenant: str | None, role="oniva_app") -> list[str]:
    conn = _app_conn(tenancy_db, role)
    try:
        cur = conn.cursor()
        if tenant:
            cur.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant,))
        cur.execute(f'SELECT tenant_id::text FROM "{table}"')
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.rollback()
        conn.close()


def test_seeding_covers_most_tables(tenancy_db):
    """Guard against the isolation tests below going vacuous."""
    assert len(tenancy_db["seeded"][TA]) >= 0.7 * len(tenancy_db["tables"]), (
        sorted(set(tenancy_db["tables"]) - tenancy_db["seeded"][TA]))
    for must in ("users", "pending_users", "workflows", "background_jobs", "audit_events"):
        assert must in tenancy_db["seeded"][TA], must


def test_roles_have_expected_attributes(tenancy_db):
    conn = psycopg2.connect(tenancy_db["owner"])
    cur = conn.cursor()
    cur.execute("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname IN ('oniva_app','oniva_system')")
    got = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    conn.close()
    assert got == {"oniva_app": (False, False), "oniva_system": (False, True)}


def test_ae5_no_tenant_reads_zero_rows_from_every_tenant_table(tenancy_db):
    """AE5: as oniva_app with no tenant context, every tenant_id table (incl. users) is empty."""
    leaks = {}
    for tbl in tenancy_db["tables"]:
        rows = _visible(tenancy_db, tbl, None)
        if rows:
            leaks[tbl] = len(rows)
    assert not leaks, leaks
    # and the seed is really there (owner/superuser sees it)
    eng = create_engine(tenancy_db["owner"])
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM users")).scalar() >= 2
    eng.dispose()


def test_tenants_table_invisible_without_context(tenancy_db):
    conn = _app_conn(tenancy_db)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM tenants")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT set_config('app.current_tenant', %s, true)", (TA,))
    cur.execute("SELECT id::text FROM tenants")
    assert [r[0] for r in cur.fetchall()] == [TA]
    conn.close()


def _tables_param():
    # Parametrize over the model's tenant tables without needing a DB at collection time.
    from app.models import Base
    return sorted(n for n, t in Base.metadata.tables.items() if "tenant_id" in t.c)


@pytest.mark.parametrize("table", _tables_param())
def test_tenant_a_never_sees_tenant_b(tenancy_db, table):
    if table not in tenancy_db["seeded"][TA] or table not in tenancy_db["seeded"][TB]:
        pytest.skip(f"no synthetic row could be built for {table}")
    seen_a = _visible(tenancy_db, table, TA)
    seen_b = _visible(tenancy_db, table, TB)
    assert TB not in seen_a and TA not in seen_b
    if table not in USER_LEVEL:
        assert set(seen_a) == {TA} and set(seen_b) == {TB}, (seen_a, seen_b)
    # skills are shared-read (tenant_id may be NULL); none are seeded NULL here


def test_every_tenant_table_has_forced_rls(tenancy_db):
    conn = psycopg2.connect(tenancy_db["owner"])
    cur = conn.cursor()
    cur.execute("SELECT relname FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace "
                "AND n.nspname='public' WHERE relkind='r' AND NOT (relrowsecurity AND relforcerowsecurity) "
                "AND EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_name=cl.relname "
                "AND c.table_schema='public' AND c.column_name='tenant_id')")
    assert cur.fetchall() == []
    # every table (not only tenant_id ones) other than alembic_version is under FORCE RLS
    cur.execute("SELECT relname FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace "
                "AND n.nspname='public' WHERE relkind='r' AND relname <> 'alembic_version' "
                "AND NOT (relrowsecurity AND relforcerowsecurity)")
    assert cur.fetchall() == []
    cur.execute("SELECT relname FROM pg_class cl WHERE relkind='r' AND relnamespace='public'::regnamespace "
                "AND relname <> 'alembic_version' AND NOT EXISTS "
                "(SELECT 1 FROM pg_policies p WHERE p.schemaname='public' AND p.tablename=cl.relname)")
    assert cur.fetchall() == []
    conn.close()


def test_system_role_bypasses_rls_and_app_role_cannot_escalate(tenancy_db):
    assert len(_visible(tenancy_db, "users", None, role="oniva_system")) >= 2
    # oniva_app cannot insert a tenant (system job) nor write across tenants
    conn = _app_conn(tenancy_db)
    cur = conn.cursor()
    with pytest.raises(psycopg2.Error):
        cur.execute("INSERT INTO tenants (id, name, slug) VALUES (gen_random_uuid(), 'x', 'x-evil')")
    conn.rollback()
    cur.execute("SELECT set_config('app.current_tenant', %s, true)", (TA,))
    cur.execute("UPDATE users SET name = 'pwned' WHERE tenant_id = %s", (TB,))
    assert cur.rowcount == 0
    conn.close()


# ------------------------------------------------------------------------------------------
# Startup assertion
# ------------------------------------------------------------------------------------------

async def test_startup_refuses_superuser_and_accepts_oniva_app(tenancy_db):
    from app.core.database import assert_request_role_unprivileged

    owner_eng = create_async_engine(tenancy_db["owner"].replace("postgresql://", "postgresql+asyncpg://"),
                                    poolclass=NullPool)
    sys_eng = create_async_engine(_role_url(tenancy_db["owner"], "oniva_system", "postgresql+asyncpg"),
                                  poolclass=NullPool)
    app_eng = create_async_engine(_role_url(tenancy_db["owner"], "oniva_app", "postgresql+asyncpg"),
                                  poolclass=NullPool)
    try:
        with pytest.raises(RuntimeError, match="Refusing to start"):
            await assert_request_role_unprivileged(owner_eng)      # superuser
        with pytest.raises(RuntimeError, match="Refusing to start"):
            await assert_request_role_unprivileged(sys_eng)        # BYPASSRLS
        await assert_request_role_unprivileged(app_eng)            # ok
    finally:
        for e in (owner_eng, sys_eng, app_eng):
            await e.dispose()


# ------------------------------------------------------------------------------------------
# End-to-end auth with the app on oniva_app (+ system factory for pre-tenant lookups)
# ------------------------------------------------------------------------------------------

async def test_login_signup_check_email_end_to_end_on_oniva_app(tenancy_db, monkeypatch):
    import httpx
    import app.core.database as dbmod
    from fastapi import FastAPI
    from app.api.v1 import auth as auth_api, onboarding as onboarding_api

    # Just the routers under test (no rate-limit/Redis middleware): the point is which DB role
    # the handlers run on.
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api/v1/auth")
    app.include_router(onboarding_api.router, prefix="/api/v1/onboarding")

    app_eng = create_async_engine(_role_url(tenancy_db["owner"], "oniva_app", "postgresql+asyncpg"),
                                  poolclass=NullPool)
    sys_eng = create_async_engine(_role_url(tenancy_db["owner"], "oniva_system", "postgresql+asyncpg"),
                                  poolclass=NullPool)
    mk = lambda e: async_sessionmaker(e, class_=AsyncSession, expire_on_commit=False)  # noqa: E731
    monkeypatch.setattr(dbmod, "engine", app_eng)
    monkeypatch.setattr(dbmod, "AsyncSessionLocal", mk(app_eng))
    monkeypatch.setattr(dbmod, "system_engine", sys_eng)
    monkeypatch.setattr(dbmod, "SystemSessionLocal", mk(sys_eng))

    email = f"newhire-{uuid.uuid4().hex[:6]}@example.com"
    owner = create_engine(tenancy_db["owner"])
    with owner.begin() as c:
        c.execute(text("INSERT INTO pending_users (id, tenant_id, email, role) "
                       "VALUES (gen_random_uuid(), :t, :e, 'member')"), {"t": TA, "e": email})
    owner.dispose()

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(f"/api/v1/auth/check-email/{email}")
            assert r.status_code == 200 and r.json()["can_signup"] is True, r.text
            r = await client.get("/api/v1/auth/check-email/nobody@example.com")
            assert r.status_code == 200 and r.json()["can_signup"] is False
            r = await client.get("/api/v1/onboarding/check-slug/some-free-slug")
            assert r.status_code == 200, r.text

            r = await client.post("/api/v1/auth/signup",
                                  json={"email": email, "password": "Str0ng!Passw0rd#1", "name": "New Hire"})
            assert r.status_code == 200, r.text
            assert r.json()["user"]["tenant_id"] == TA

            r = await client.post("/api/v1/auth/login",
                                  json={"email": email, "password": "Str0ng!Passw0rd#1"})
            assert r.status_code == 200, r.text
            assert r.json()["access_token"]
            r = await client.post("/api/v1/auth/login",
                                  json={"email": email, "password": "wrong-password"})
            assert r.status_code == 401
    finally:
        await app_eng.dispose()
        await sys_eng.dispose()

    # Signup really wrote through the system role; the request role still cannot see it unscoped.
    assert _visible(tenancy_db, "users", None) == []
    assert TA in _visible(tenancy_db, "users", TA)


# ------------------------------------------------------------------------------------------
# Scheduler + startup sweep on the system role
# ------------------------------------------------------------------------------------------

def test_scheduler_uses_system_url(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "SYSTEM_DATABASE_URL", "")
    monkeypatch.setattr(s, "SCHEDULER_DATABASE_URL", "postgresql+asyncpg://sched@h/db")
    assert s.system_database_url == "postgresql+asyncpg://sched@h/db"
    monkeypatch.setattr(s, "SYSTEM_DATABASE_URL", "postgresql+asyncpg://sys@h/db")
    assert s.system_database_url == "postgresql+asyncpg://sys@h/db"


async def test_scheduler_claims_due_schedules_through_system_role(tenancy_db):
    owner = create_engine(tenancy_db["owner"])
    with owner.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        for t in (TA, TB):
            assert seed_row(c, "scheduled_workflows", t, {
                "next_run_at": "now() - interval '1 minute'", "is_enabled": "true",
                "status": "'ACTIVE'"}), "could not seed scheduled_workflows"
    owner.dispose()

    claim = text("SELECT tenant_id::text FROM scheduled_workflows WHERE is_enabled = true "
                 "AND status = 'ACTIVE' AND next_run_at <= NOW() ORDER BY next_run_at "
                 "LIMIT 10 FOR UPDATE SKIP LOCKED")
    sys_eng = create_async_engine(_role_url(tenancy_db["owner"], "oniva_system", "postgresql+asyncpg"),
                                  poolclass=NullPool)
    app_eng = create_async_engine(_role_url(tenancy_db["owner"], "oniva_app", "postgresql+asyncpg"),
                                  poolclass=NullPool)
    try:
        async with async_sessionmaker(sys_eng)() as s:
            assert {r[0] for r in (await s.execute(claim)).all()} >= {TA, TB}
        async with async_sessionmaker(app_eng)() as s:
            assert (await s.execute(claim)).all() == []   # request role sees none unscoped
    finally:
        await sys_eng.dispose()
        await app_eng.dispose()


async def test_startup_sweep_fails_stale_but_spares_fresh_jobs(tenancy_db):
    from app.services.startup_recovery import fail_stale_deep_work_jobs

    ids = {k: str(uuid.uuid4()) for k in ("stale_a", "fresh_a", "stale_b")}
    owner = create_engine(tenancy_db["owner"])
    with owner.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        for key, tenant, age in (("stale_a", TA, "2 hours"), ("fresh_a", TA, "10 seconds"),
                                 ("stale_b", TB, "2 hours")):
            assert seed_row(c, "background_jobs", tenant, {
                "id": f"'{ids[key]}'", "job_type": "'deep_work'", "status": "'running'",
                "created_at": f"now() - interval '{age}'",
                "started_at": f"now() - interval '{age}'"}), key
    owner.dispose()

    sys_eng = create_async_engine(_role_url(tenancy_db["owner"], "oniva_system", "postgresql+asyncpg"),
                                  poolclass=NullPool)
    try:
        n = await fail_stale_deep_work_jobs(async_sessionmaker(sys_eng), 3600, "restart")
    finally:
        await sys_eng.dispose()
    assert n >= 2
    owner = create_engine(tenancy_db["owner"])
    with owner.connect() as c:
        st = {r[0]: r[1] for r in c.execute(
            text("SELECT id::text, status FROM background_jobs WHERE id::text = ANY(:ids)"),
            {"ids": list(ids.values())})}
    owner.dispose()
    assert st[ids["stale_a"]] == "failed" and st[ids["stale_b"]] == "failed"
    assert st[ids["fresh_a"]] == "running"
