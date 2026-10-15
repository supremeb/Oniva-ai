"""U8 / R14-R16 / AE7: authenticated browser routes in front of the engine (mocked engine)."""
import asyncio
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.v1 import engine_conversations as ec
from app.auth.dependencies import get_current_user, get_current_user_with_query_token
from app.auth.providers.base import AuthUser
from app.config import Settings
from app.core.database import get_db
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.services.engine_chat_agent import BuiltAgent
from app.services.engine_client import EngineClient, get_engine_client

SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
TENANT_A, TENANT_B = str(uuid.uuid4()), str(uuid.uuid4())
PROJECT = str(uuid.uuid4())


def settings(**kw) -> Settings:
    base = dict(SECRET_KEY=SECRET, ENGINE_SERVICE_TOKEN=SVC, ENGINE_URL="http://engine.test",
                ENGINE_SESSION_ID="sess-1", ENABLE_ENGINE_API=True, ENABLE_ENGINE_CHAT=True)
    base.update(kw)
    return Settings(_env_file=None, **base)


def user(tenant=TENANT_A, role="analyst", uid=None) -> AuthUser:
    return AuthUser(id=uid or str(uuid.uuid4()), email="u@x.io", tenant_id=tenant,
                    tenant_slug="t", role=role, auth_provider="local")


class FakeDB:
    def __init__(self):
        self.rows = {}

    async def get(self, model, key):
        if model is Tenant:  # engine chat is on for every tenant in these route tests
            return Tenant(id=key, name="t", slug="t", settings={"features": {"ENABLE_ENGINE_CHAT": True}})
        return self.rows.get(key)

    def add(self, obj):
        self.rows[obj.conversation_id] = obj

    async def flush(self):
        pass


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, state):
        self.chunks, self.state = chunks, state

    async def __aiter__(self):
        for c in self.chunks:
            self.state["sent"] += 1
            yield c
            await asyncio.sleep(0)

    async def aclose(self):
        self.state["closed"] = True


class Env:
    def __init__(self, enabled=True):
        self.calls = []
        self.state = {"sent": 0, "closed": False}
        self.respond = None  # optional override(request) -> Response
        self.db = FakeDB()
        self.current = user()
        cfg = settings(ENABLE_ENGINE_API=enabled)
        self.client = EngineClient(cfg, transport=httpx.MockTransport(self._handle))
        self.app = FastAPI()
        ec.mount_engine_conversations(self.app, cfg)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[get_engine_client] = lambda: self.client
        self.app.dependency_overrides[get_current_user] = lambda: self.current
        self.app.dependency_overrides[get_current_user_with_query_token] = lambda: self.current
        self.http = TestClient(self.app, raise_server_exceptions=False)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if self.respond:
            return self.respond(req)
        if req.url.path.endswith("/watch"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=Stream([b"id: 1\ndata: a\n\n", b"id: 2\ndata: b\n\n"], self.state))
        if req.url.path.endswith("/submissions"):
            return httpx.Response(202, json={"submissionId": "s1"})
        if req.url.path.endswith("/decision"):
            return httpx.Response(200, json={"approval": {}})
        if req.url.path.endswith("/abort"):
            return httpx.Response(200, json={"aborted": True})
        return httpx.Response(201, json={"conversationId": "x"})

    def seed(self, tenant=TENANT_A, owner=None) -> str:
        cid = uuid.uuid4()
        self.db.rows[cid] = EngineConversation(
            conversation_id=cid, tenant_id=uuid.UUID(tenant), project_id=uuid.UUID(PROJECT),
            user_id=uuid.UUID(owner or self.current.id), session_id="sess-1")
        return str(cid)


@pytest.fixture
def env(monkeypatch):
    async def role(self, project_id, user_id, tenant_id):
        return "owner" if str(tenant_id) == TENANT_A else None

    monkeypatch.setattr(ec.ProjectService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ec.ProjectService, "get_user_role", role)

    async def built(db, **kw):  # agent building has its own tests (test_engine_chat_agent.py)
        return BuiltAgent(agent={"systemPrompt": "p", "tools": []}, selections={}, dropped={})

    monkeypatch.setattr(ec, "build_engine_chat_agent", built)
    return Env()


def test_disabled_flag_means_no_routes():
    e = Env(enabled=False)
    assert e.http.post("/api/v1/engine/conversations", json={"project_id": PROJECT}).status_code == 404
    assert not e.calls


def test_unconfigured_engine_does_not_mount():
    app = FastAPI()
    assert ec.mount_engine_conversations(app, settings(ENGINE_URL="")) is False
    assert ec.mount_engine_conversations(app, settings(ENGINE_SERVICE_TOKEN=None)) is False


def test_create_records_binding_and_uses_user_tenant(env):
    r = env.http.post("/api/v1/engine/conversations",
                      json={"project_id": PROJECT, "tenant_id": TENANT_B})
    assert r.status_code == 201
    cid = uuid.UUID(r.json()["conversationId"])
    row = env.db.rows[cid]
    assert (str(row.tenant_id), str(row.user_id), row.session_id) == (TENANT_A, env.current.id, "sess-1")
    req = env.calls[0]
    assert req.headers["x-oniva-tenant"] == TENANT_A and req.headers["x-oniva-project"] == PROJECT
    assert req.headers["authorization"] == f"Bearer {SVC}"
    assert json.loads(req.content)["id"] == str(cid)


def test_create_unknown_project_404_no_engine_call(env):
    env.current = user(tenant=TENANT_B)
    r = env.http.post("/api/v1/engine/conversations", json={"project_id": PROJECT})
    assert r.status_code == 404 and not env.calls


def test_other_tenant_gets_404_on_every_route_and_engine_untouched(env):
    cid = env.seed(tenant=TENANT_A)
    env.current = user(tenant=TENANT_B)
    base = f"/api/v1/engine/conversations/{cid}"
    for r in (
        env.http.get(f"{base}/watch"),
        env.http.post(f"{base}/submissions", json={"text": "hi"}),
        env.http.post(f"{base}/abort", json={}),
        env.http.post(f"{base}/approvals/t1/decision", json={"decision": "approve"}),
    ):
        assert r.status_code == 404
    assert not env.calls


def test_submit_forwards_idempotency_and_binding_headers(env):
    cid = env.seed()
    r = env.http.post(f"/api/v1/engine/conversations/{cid}/submissions",
                      json={"text": "hi"}, headers={"Idempotency-Key": "k-1"})
    assert r.status_code == 202
    req = env.calls[0]
    assert req.headers["idempotency-key"] == "k-1"
    assert json.loads(req.content) == {"text": "hi", "requestId": "k-1"}
    assert req.headers["x-oniva-tenant"] == TENANT_A


def test_watch_streams_through_and_forwards_resume(env):
    cid = env.seed()
    with env.http.stream("GET", f"/api/v1/engine/conversations/{cid}/watch?cursor=5",
                         headers={"Last-Event-ID": "5"}) as r:
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
        body = b"".join(r.iter_raw())
    assert body == b"id: 1\ndata: a\n\nid: 2\ndata: b\n\n"
    req = env.calls[0]
    assert req.headers["last-event-id"] == "5" and req.url.params["cursor"] == "5"
    assert env.state["closed"]


def test_watch_rejects_bad_cursor(env):
    cid = env.seed()
    assert env.http.get(f"/api/v1/engine/conversations/{cid}/watch?cursor=abc").status_code == 422
    assert not env.calls


@pytest.mark.asyncio
async def test_watch_closes_upstream_when_browser_disconnects(env):
    cid = env.seed()
    resp = await ec.watch(
        uuid.UUID(cid), request=None, cursor=None, last_event_id=None,
        db=env.db, user=env.current, engine=env.client)
    it = resp.body_iterator
    first = await it.__anext__()
    assert first.startswith(b"id: 1")
    await it.aclose()  # what Starlette does on client disconnect
    assert env.state["sent"] == 1 and env.state["closed"]


def test_not_owner_becomes_503_retry_after(env):
    cid = env.seed()
    env.respond = lambda req: httpx.Response(409, json={"error": "not_owner", "ownerId": "w9"})
    for r in (env.http.post(f"/api/v1/engine/conversations/{cid}/submissions", json={"text": "x"}),
              env.http.get(f"/api/v1/engine/conversations/{cid}/watch")):
        assert r.status_code == 503 and r.headers["retry-after"] == "1"
        assert "w9" not in r.text


def test_engine_5xx_and_auth_faults_do_not_leak_token(env):
    cid = env.seed()
    env.respond = lambda req: httpx.Response(500, text=f"boom {SVC}")
    r = env.http.post(f"/api/v1/engine/conversations/{cid}/abort", json={})
    assert r.status_code == 502 and SVC not in r.text
    env.respond = lambda req: httpx.Response(401, json={"error": "unauthorized"})
    r = env.http.post(f"/api/v1/engine/conversations/{cid}/abort", json={})
    assert r.status_code == 502 and SVC not in r.text


def test_decision_by_non_owner_non_admin_is_403_no_engine_call(env):
    cid = env.seed(owner=str(uuid.uuid4()))
    r = env.http.post(f"/api/v1/engine/conversations/{cid}/approvals/t1/decision",
                      json={"decision": "approve"})
    assert r.status_code == 403 and not env.calls


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_decision_carries_verified_principal_ignoring_body(env, who):
    if who == "owner":
        cid = env.seed()
    else:
        cid = env.seed(owner=str(uuid.uuid4()))
        env.current = user(role="admin")
    r = env.http.post(
        f"/api/v1/engine/conversations/{cid}/approvals/t1/decision",
        json={"decision": "approve", "note": "ok", "principal": "attacker"})
    assert r.status_code == 200
    req = env.calls[0]
    assert req.headers["x-oniva-principal"] == env.current.id
    assert json.loads(req.content) == {"decision": "approve", "note": "ok"}
    assert req.url.path.endswith("/approvals/t1/decision")


def test_decision_rejects_invalid_value(env):
    cid = env.seed()
    r = env.http.post(f"/api/v1/engine/conversations/{cid}/approvals/t1/decision",
                      json={"decision": "maybe"})
    assert r.status_code == 422 and not env.calls
