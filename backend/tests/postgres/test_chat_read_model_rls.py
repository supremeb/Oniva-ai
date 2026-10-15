"""U10 - chat history read model on real Postgres: RLS, backfill, dual-write, union list, continue.

Runs on the Alembic-migrated database (needs pgvector; skipped locally without it, CI runs it).
Application code runs as ``oniva_app`` (NOBYPASSRLS) with a tenant set, exactly like a request.
"""
from __future__ import annotations

import json
import uuid

import httpx
import psycopg2
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api.v1 import chat_sessions as cs
from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.config import Settings
from app.core.database import get_db, set_tenant_context
from app.models.chat_session import ChatSession, ChatSessionMessage
from app.services import chat_backfill, chat_service
from app.services.engine_client import EngineClient, get_engine_client
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, TB, _role_url, seed_row

UA, UB = str(uuid.uuid4()), str(uuid.uuid4())
PROJECT = str(uuid.uuid4())
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"

TOOL_PARTS = [
    {"type": "thinking", "text": "plan"},
    {"type": "tool_call", "id": "tc1", "toolName": "search", "args": {"q": "x"}, "status": "success",
     "result": {"hits": 2}},
    {"type": "source", "url": "https://example.com", "title": "Ex"},
    {"type": "text", "text": "final answer"},
]


def legacy_messages():
    return [
        {"role": "user", "content": "hello", "timestamp": "2026-01-01T10:00:00", "metadata": {}},
        {"role": "assistant", "content": "final answer", "timestamp": "2026-01-01T10:00:05",
         "metadata": {"parts": TOOL_PARTS, "model": "m"}},
    ]


@pytest.fixture(scope="module")
def db(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_chat_rm")
    owner = pg_server.url("oniva_chat_rm")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
        c.execute(text("SET session_replication_role = replica"))
        for t, slug in ((TA, "ta"), (TB, "tb")):
            c.execute(text("INSERT INTO tenants (id, name, slug, settings) VALUES (:i, :n, :n, CAST(:s AS jsonb))"),
                      {"i": t, "n": slug, "s": json.dumps({"features": {"ENABLE_ENGINE_CHAT": t == TA}})})
        for u, t in ((UA, TA), (UB, TB)):
            assert seed_row(c, "users", t, {"id": f"'{u}'"}), "cannot seed users"
        assert seed_row(c, "projects", TA, {"id": f"'{PROJECT}'"}), "cannot seed projects"
    eng.dispose()
    app_engine = create_async_engine(
        _role_url(owner, "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    sys_engine = create_async_engine(
        _role_url(owner, "oniva_system", "postgresql+asyncpg"), poolclass=NullPool)
    return {
        "owner": owner,
        "app": async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False),
        "sys": async_sessionmaker(sys_engine, class_=AsyncSession, expire_on_commit=False),
    }


def owner_exec(db, sql, params=None):
    eng = create_engine(db["owner"])
    try:
        with eng.begin() as c:
            c.execute(text("SET session_replication_role = replica"))
            return c.execute(text(sql), params or {}).fetchall() if sql.lstrip().upper().startswith("SELECT") \
                else c.execute(text(sql), params or {})
    finally:
        eng.dispose()


def seed_session(db, tenant=TA, user=UA, messages=None, title="t", updated="2026-01-01T00:00:00Z") -> str:
    sid = str(uuid.uuid4())
    msgs = legacy_messages() if messages is None else messages
    owner_exec(
        db,
        "INSERT INTO chat_sessions (id, tenant_id, user_id, context_type, title, messages, session_metadata, "
        "is_draft, is_pinned, message_count, updated_at) VALUES (:i, :t, :u, 'agent', :ti, CAST(:m AS jsonb), "
        "'{}'::jsonb, false, false, :n, :up)",
        {"i": sid, "t": tenant, "u": user, "ti": title, "m": json.dumps(msgs), "n": len(msgs), "up": updated})
    return sid


def rows_for(db, sid):
    return owner_exec(db, "SELECT seq, role, parts, parts_valid FROM chat_session_messages "
                          "WHERE session_id = :s ORDER BY seq", {"s": sid})


def messages_of(db, sid):
    return owner_exec(db, "SELECT messages FROM chat_sessions WHERE id = :s", {"s": sid})[0][0]


async def tenant_session(db, tenant) -> AsyncSession:
    s = db["app"]()
    await set_tenant_context(s, tenant)
    return s


# --- RLS -------------------------------------------------------------------------------------

def test_rls_isolation_and_no_tenant(db):
    sa = seed_session(db, TA, UA)
    sb = seed_session(db, TB, UB)
    for s in (sa, sb):
        owner_exec(db, "INSERT INTO chat_session_messages (id, tenant_id, session_id, seq, role, parts) "
                       "SELECT gen_random_uuid(), tenant_id, id, 0, 'user', '[]'::jsonb FROM chat_sessions WHERE id=:s",
                   {"s": s})

    def visible(tenant):
        conn = psycopg2.connect(_role_url(db["owner"], "oniva_app"))
        try:
            cur = conn.cursor()
            if tenant:
                cur.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant,))
            cur.execute("SELECT session_id::text FROM chat_session_messages")
            return {r[0] for r in cur.fetchall()}
        finally:
            conn.close()

    assert sa in visible(TA) and sb not in visible(TA)
    assert sb in visible(TB) and sa not in visible(TB)
    assert visible(None) == set()


async def test_with_check_blocks_cross_tenant_insert(db):
    sb = seed_session(db, TB, UB)
    s = await tenant_session(db, TA)
    with pytest.raises(Exception) as ei:
        await s.execute(text(
            "INSERT INTO chat_session_messages (id, tenant_id, session_id, seq, role, parts) "
            "VALUES (gen_random_uuid(), :t, :s, 0, 'user', '[]'::jsonb)"), {"t": TB, "s": sb})
    assert "row-level security" in str(ei.value)
    await s.rollback()
    await s.close()


# --- backfill --------------------------------------------------------------------------------

async def test_lazy_backfill_copies_parts_exactly_and_is_idempotent(db):
    sid = seed_session(db)
    s = await tenant_session(db, TA)
    session = (await s.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sid)))).scalar_one()
    assert await chat_backfill.ensure_backfilled(db=s, session=session) == 2
    assert await chat_backfill.ensure_backfilled(db=s, session=session) == 0
    await s.commit()
    await s.close()
    rows = rows_for(db, sid)
    assert [r[0] for r in rows] == [0, 1]
    assert rows[1][2] == TOOL_PARTS                      # equal to the stored JSONB parts
    assert rows[0][2] == [{"type": "text", "text": "hello"}]  # no parts -> single text part
    assert all(r[3] for r in rows)
    assert messages_of(db, sid) == legacy_messages()      # legacy rows untouched


async def test_invalid_parts_are_flagged_not_dropped(db):
    bad = [{"role": "assistant", "content": "x", "metadata": {"parts": [{"no_type": 1}]}}]
    sid = seed_session(db, messages=bad)
    s = await tenant_session(db, TA)
    session = (await s.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sid)))).scalar_one()
    await chat_backfill.ensure_backfilled(s, session)
    await s.commit()
    await s.close()
    rows = rows_for(db, sid)
    assert len(rows) == 1 and rows[0][2] == [{"no_type": 1}] and rows[0][3] is False


async def test_backfill_resumes_after_interruption(db):
    sid = seed_session(db, messages=legacy_messages() * 2)  # 4 messages
    owner_exec(db, "INSERT INTO chat_session_messages (id, tenant_id, session_id, seq, role, parts) "
                   "VALUES (gen_random_uuid(), :t, :s, 1, 'assistant', '[]'::jsonb)", {"t": TA, "s": sid})
    s = await tenant_session(db, TA)
    session = (await s.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sid)))).scalar_one()
    assert await chat_backfill.ensure_backfilled(s, session) == 3     # fills 0, 2, 3; keeps existing 1
    await s.commit()
    await s.close()
    assert [r[0] for r in rows_for(db, sid)] == [0, 1, 2, 3]


async def test_run_is_batched_resumable_and_idempotent(db):
    owner_exec(db, "DELETE FROM chat_sessions")
    ids = sorted(seed_session(db) for _ in range(3))
    async with db["sys"]() as s:
        first = await chat_backfill.run(s, batch=2, max_batches=1)
    assert first.sessions == 2 and first.rows == 4 and first.last_session_id == uuid.UUID(ids[1])
    async with db["sys"]() as s:
        rest = await chat_backfill.run(s, batch=2, after=first.last_session_id)
    assert rest.sessions == 1 and rest.rows == 2
    async with db["sys"]() as s:
        again = await chat_backfill.run(s, batch=2)
    assert again.sessions == 3 and again.rows == 0
    assert all(len(rows_for(db, i)) == 2 for i in ids)


# --- dual-write ------------------------------------------------------------------------------

@pytest.fixture
def app_sessions(db, monkeypatch):
    monkeypatch.setattr(chat_service, "AsyncSessionLocal", db["app"])
    monkeypatch.setattr(chat_service, "_log_to_session_history", lambda *a, **k: None)


async def test_persist_session_writes_both_stores(db, app_sessions):
    sid = await chat_service.persist_session(
        uuid.UUID(TA), uuid.UUID(UA), None, "hi", "yo", message_parts=[{"type": "text", "text": "yo"}])
    sid = str(sid)
    rows = rows_for(db, sid)
    assert [(r[0], r[1]) for r in rows] == [(0, "user"), (1, "assistant")]
    assert rows[1][2] == [{"type": "text", "text": "yo"}]
    assert len(messages_of(db, sid)) == 2


async def test_persist_session_lazy_backfills_existing_history_first(db, app_sessions):
    sid = seed_session(db)  # 2 legacy messages, no read-model rows
    await chat_service.persist_session(uuid.UUID(TA), uuid.UUID(UA), uuid.UUID(sid), "more", "ok")
    assert [r[0] for r in rows_for(db, sid)] == [0, 1, 2, 3]
    assert len(messages_of(db, sid)) == 4


async def test_dual_write_failure_rolls_back_both(db, app_sessions, monkeypatch):
    sid = seed_session(db)
    real = chat_service.append_message_row
    calls = {"n": 0}

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return await real(*a, **k)

    monkeypatch.setattr(chat_service, "append_message_row", flaky)
    with pytest.raises(RuntimeError):
        await chat_service.persist_session(uuid.UUID(TA), uuid.UUID(UA), uuid.UUID(sid), "more", "ok")
    assert messages_of(db, sid) == legacy_messages()
    assert rows_for(db, sid) == []


# --- API: detail, add message, clear, union list, continue -----------------------------------

def make_app(db, user, engine_calls=None, tenant_cfg=True):
    cfg = Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_SERVICE_TOKEN=SVC, ENGINE_URL="http://engine.test",
                   ENGINE_SESSION_ID="sess-1", ENABLE_ENGINE_API=True, ENABLE_ENGINE_CHAT=True)
    calls = engine_calls if engine_calls is not None else []

    def handle(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"ok": True})

    client = EngineClient(cfg, transport=httpx.MockTransport(handle))
    app = FastAPI()
    app.state.engine_chat_settings = cfg
    app.include_router(cs.router, prefix="/api/v1/chat-sessions")

    async def override_db():
        s = db["app"]()
        await set_tenant_context(s, user.tenant_id)
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
        finally:
            await s.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_engine_client] = lambda: client
    return TestClient(app, raise_server_exceptions=False), calls


def au(uid=UA, tenant=TA) -> AuthUser:
    return AuthUser(id=uid, email="u@x.io", tenant_id=tenant, tenant_slug="t", role="analyst", auth_provider="local")


def test_detail_serves_transcript_from_read_model(db):
    sid = seed_session(db)
    http, _ = make_app(db, au())
    r = http.get(f"/api/v1/chat-sessions/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["role"] for t in body["transcript"]] == ["user", "assistant"]
    assert body["transcript"][1]["parts"] == TOOL_PARTS
    assert body["messages"] == legacy_messages()  # legacy shape unchanged
    assert len(rows_for(db, sid)) == 2


def test_other_users_session_is_404(db):
    sid = seed_session(db, TB, UB)
    http, _ = make_app(db, au())
    assert http.get(f"/api/v1/chat-sessions/{sid}").status_code == 404


def test_add_message_dual_writes_and_clear_empties_read_model(db):
    sid = seed_session(db)
    http, _ = make_app(db, au())
    r = http.post(f"/api/v1/chat-sessions/{sid}/messages", json={"message": {"role": "user", "content": "again"}})
    assert r.status_code == 200, r.text
    assert [x[0] for x in rows_for(db, sid)] == [0, 1, 2]
    assert http.post(f"/api/v1/chat-sessions/{sid}/clear").status_code == 200
    assert rows_for(db, sid) == []


def test_union_list_orders_by_updated_at_with_stable_pages(db):
    owner_exec(db, "DELETE FROM engine_conversations")
    owner_exec(db, "DELETE FROM chat_sessions")
    for i in range(3):
        seed_session(db, title=f"legacy-{i}", updated=f"2026-02-0{i + 1}T00:00:00Z")
    seed_session(db, TB, UB, title="other-tenant", updated="2026-03-01T00:00:00Z")
    conv_ids = []
    for i in range(3):
        cid = str(uuid.uuid4())
        conv_ids.append(cid)
        owner_exec(db, "INSERT INTO engine_conversations (conversation_id, tenant_id, project_id, user_id, "
                       "session_id, title, updated_at) VALUES (:c, :t, :p, :u, 's', :ti, :up)",
                   {"c": cid, "t": TA, "p": PROJECT, "u": UA, "ti": None if i == 0 else f"engine-{i}",
                    "up": f"2026-02-0{i + 1}T12:00:00Z"})
    http, _ = make_app(db, au())
    full = http.get("/api/v1/chat-sessions", params={"include": "engine", "page_size": 100}).json()
    assert full["total"] == 6
    stamps = [i["updated_at"] for i in full["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert {i["source"] for i in full["items"]} == {"legacy", "engine"}
    assert "New conversation" in {i["title"] for i in full["items"]}
    assert "other-tenant" not in {i["title"] for i in full["items"]}
    paged = []
    for page in (1, 2, 3):
        paged += http.get("/api/v1/chat-sessions",
                          params={"include": "engine", "page": page, "page_size": 2}).json()["items"]
    assert [i["id"] for i in paged] == [i["id"] for i in full["items"]]
    # default list is unchanged: legacy shape, no engine rows
    legacy_only = http.get("/api/v1/chat-sessions").json()
    assert "sessions" in legacy_only and legacy_only["total"] == 3


def test_union_list_hides_archived_engine_conversations_and_exposes_pinned(db):
    owner_exec(db, "DELETE FROM engine_conversations")
    owner_exec(db, "DELETE FROM chat_sessions")
    ids = {}
    for name, extra in (("keep", ""), ("pinned", ", pinned"), ("gone", ", archived_at")):
        cid = str(uuid.uuid4())
        ids[name] = cid
        val = {"": "", ", pinned": ", true", ", archived_at": ", now()"}[extra]
        owner_exec(db, "INSERT INTO engine_conversations (conversation_id, tenant_id, project_id, user_id, "
                       f"session_id, title{extra}) VALUES (:c, :t, :p, :u, 's', :ti{val})",
                   {"c": cid, "t": TA, "p": PROJECT, "u": UA, "ti": name})
    http, _ = make_app(db, au())
    body = http.get("/api/v1/chat-sessions", params={"include": "engine"}).json()
    by_title = {i["title"]: i for i in body["items"]}
    assert set(by_title) == {"keep", "pinned"} and body["total"] == 2
    assert by_title["pinned"]["pinned"] is True and by_title["keep"]["pinned"] is False


def test_continue_creates_engine_conversation_with_summary_and_link(db, monkeypatch):
    sid = seed_session(db, title="Quarterly")
    before = messages_of(db, sid)

    class Built:
        agent = {"systemPrompt": "x"}
        selections = {"skills": ["a"]}
        dropped = []

    seen = {}

    async def fake_build(*a, **k):
        seen.update(k)
        return Built()

    async def fake_role(self, *a, **k):
        return "owner"

    async def fake_summary(db_, **k):
        return "SUMMARY TEXT", "model"

    monkeypatch.setattr(cs, "build_engine_chat_agent", fake_build)
    monkeypatch.setattr(cs.ProjectService, "get_user_role", fake_role)
    monkeypatch.setattr(cs, "build_continue_summary", fake_summary)
    http, calls = make_app(db, au())
    r = http.post(f"/api/v1/chat-sessions/{sid}/continue", json={"project_id": PROJECT})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["legacy_session_id"] == sid and body["summary_source"] == "model" and not body["existing"]
    create, submit = calls
    assert create.url.path == "/v1/conversations"
    sub = json.loads(submit.content)
    assert sub == {"text": "SUMMARY TEXT", "type": "write", "requestId": f"legacy_session:{sid}"}
    assert submit.headers["Idempotency-Key"] == f"legacy_session:{sid}"
    row = owner_exec(db, "SELECT legacy_session_id::text, title, user_id::text FROM engine_conversations "
                         "WHERE conversation_id = :c", {"c": body["conversation_id"]})[0]
    assert row == (sid, "Quarterly", UA)
    assert seen["continuation_summary"] == "SUMMARY TEXT"      # reaches the agent config (system prompt)
    assert owner_exec(db, "SELECT continuation_summary FROM engine_conversations WHERE conversation_id = :c",
                      {"c": body["conversation_id"]})[0][0] == "SUMMARY TEXT"
    assert messages_of(db, sid) == before                       # legacy rows not mutated
    again = http.post(f"/api/v1/chat-sessions/{sid}/continue", json={"project_id": PROJECT}).json()
    assert again["existing"] and again["conversation_id"] == body["conversation_id"]
    assert len(calls) == 2                                       # no second engine conversation


def test_continue_is_404_when_flag_off_or_not_owner(db, monkeypatch):
    sid = seed_session(db, TB, UB)
    http, calls = make_app(db, au(UB, TB))      # tenant B has the flag off
    assert http.post(f"/api/v1/chat-sessions/{sid}/continue", json={"project_id": PROJECT}).status_code == 404
    sid_a = seed_session(db, TA, UA)
    http, calls = make_app(db, au(str(uuid.uuid4()), TA))   # flag on, but not the owner
    assert http.post(f"/api/v1/chat-sessions/{sid_a}/continue", json={"project_id": PROJECT}).status_code == 404
    assert calls == []
