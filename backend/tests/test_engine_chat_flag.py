"""U1 / R1-R3 / AE1: per-tenant ENABLE_ENGINE_CHAT resolution and the engine route gate."""
import uuid

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
from app.services import feature_flags as ff
from app.services.engine_chat_agent import BuiltAgent
from app.services.engine_client import EngineClient, get_engine_client

import httpx

SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
PROJECT = uuid.uuid4()


def cfg(**kw) -> Settings:
    base = dict(SECRET_KEY=SECRET, ENGINE_SERVICE_TOKEN=SVC, ENGINE_URL="http://engine.test",
                ENGINE_SESSION_ID="sess-1", ENABLE_ENGINE_API=True, ENABLE_ENGINE_CHAT=True)
    base.update(kw)
    return Settings(_env_file=None, **base)


def tenant(flag=None) -> Tenant:
    features = {} if flag is None else {"ENABLE_ENGINE_CHAT": flag}
    return Tenant(id=uuid.uuid4(), name="t", slug=f"t-{uuid.uuid4().hex[:6]}", settings={"features": features})


def test_default_is_off():
    assert Settings(_env_file=None, SECRET_KEY=SECRET).ENABLE_ENGINE_CHAT is False


def test_tenant_on_and_off():
    s = cfg()
    assert ff.engine_chat_enabled(tenant(True), s) is True
    assert ff.engine_chat_enabled(tenant(False), s) is False


def test_tenant_without_setting_is_off():
    # Global ON alone never enables a tenant: the tenant setting must be true.
    assert ff.engine_chat_enabled(tenant(None), cfg()) is False


@pytest.mark.parametrize("override", [
    dict(ENABLE_ENGINE_CHAT=False),
    dict(ENABLE_ENGINE_API=False),
    dict(ENGINE_URL=""),
    dict(ENGINE_SERVICE_TOKEN=None),
])
def test_global_conditions_override_tenant_true(override):
    assert ff.engine_chat_enabled(tenant(True), cfg(**override)) is False


def test_feature_map_exposes_resolved_value(monkeypatch):
    on, off, glob_off = tenant(True), tenant(False), tenant(True)
    monkeypatch.setattr(ff, "get_settings", lambda: cfg())
    assert ff.get_tenant_features(on)["ENABLE_ENGINE_CHAT"] is True
    assert ff.get_tenant_features(off)["ENABLE_ENGINE_CHAT"] is False
    monkeypatch.setattr(ff, "get_settings", lambda: cfg(ENABLE_ENGINE_CHAT=False))
    assert ff.get_tenant_features(glob_off)["ENABLE_ENGINE_CHAT"] is False
    assert ff.get_feature(glob_off, "ENABLE_ENGINE_CHAT") is False


def test_flag_is_known_and_operator_only():
    assert ff.is_known_flag("ENABLE_ENGINE_CHAT")
    assert ff.is_operator_only("ENABLE_ENGINE_CHAT")


# ---- route gate (AE1) -------------------------------------------------------

class FakeDB:
    def __init__(self, tenants):
        self.rows, self.tenants = {}, tenants

    async def get(self, model, key):
        if model is Tenant:
            return self.tenants.get(str(key))
        return self.rows.get(key)

    def add(self, obj):
        self.rows[obj.conversation_id] = obj

    async def flush(self):
        pass

    async def execute(self, *a, **k):  # touch / title updates after a submit; empty approval ledger
        class _Empty:
            def scalars(self):
                return self

            def all(self):
                return []

        return _Empty()


class Env:
    def __init__(self, tenants, current, **kw):
        self.calls = []
        self.db = FakeDB({str(t.id): t for t in tenants})
        self.current = current
        c = cfg(**kw)
        self.client = EngineClient(c, transport=httpx.MockTransport(self._handle))
        self.app = FastAPI()
        ec.mount_engine_conversations(self.app, c)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[get_engine_client] = lambda: self.client
        self.app.dependency_overrides[get_current_user] = lambda: self.current
        self.app.dependency_overrides[get_current_user_with_query_token] = lambda: self.current
        self.http = TestClient(self.app, raise_server_exceptions=False)

    def _handle(self, req):
        self.calls.append(req)
        if req.url.path.endswith("/submissions"):
            return httpx.Response(202, json={"submissionId": "s1"})
        if req.url.path.endswith("/abort"):
            return httpx.Response(200, json={"aborted": True})
        if req.url.path.endswith("/decision"):
            return httpx.Response(200, json={"approval": {}})
        if req.url.path.endswith("/watch"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"id: 1\ndata: a\n\n")
        return httpx.Response(201, json={"conversationId": "x"})

    def seed(self, t: Tenant) -> str:
        cid = uuid.uuid4()
        self.db.rows[cid] = EngineConversation(
            conversation_id=cid, tenant_id=t.id, project_id=PROJECT,
            user_id=uuid.UUID(self.current.id), session_id="sess-1")
        return str(cid)


def au(t: Tenant) -> AuthUser:
    return AuthUser(id=str(uuid.uuid4()), email="u@x.io", tenant_id=str(t.id), tenant_slug="t",
                    role="analyst", auth_provider="local")


@pytest.fixture(autouse=True)
def _project_role(monkeypatch):
    async def role(self, project_id, user_id, tenant_id):
        return "owner"
    monkeypatch.setattr(ec.ProjectService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ec.ProjectService, "get_user_role", role)

    async def built(db, **kw):  # agent building is covered by tests/internal/test_engine_chat_agent.py
        return BuiltAgent(agent={"systemPrompt": "p", "tools": []}, selections={}, dropped={})

    monkeypatch.setattr(ec, "build_engine_chat_agent", built)


def test_ae1_tenant_a_works_tenant_b_404_on_every_route():
    a, b = tenant(True), tenant(False)
    ea = Env([a, b], au(a))
    assert ea.http.post("/api/v1/engine/conversations", json={"project_id": str(PROJECT)}).status_code == 201
    cid_a = ea.seed(a)
    assert ea.http.post(f"/api/v1/engine/conversations/{cid_a}/submissions", json={"text": "hi"}).status_code == 202

    eb = Env([a, b], au(b))
    cid_b = eb.seed(b)  # an existing conversation of the now-off tenant
    base = "/api/v1/engine/conversations"
    responses = [
        eb.http.post(base, json={"project_id": str(PROJECT)}),
        eb.http.post(f"{base}/{cid_b}/submissions", json={"text": "hi"}),
        eb.http.get(f"{base}/{cid_b}/watch"),
        eb.http.post(f"{base}/{cid_b}/abort"),
        eb.http.post(f"{base}/{cid_b}/approvals/t1/decision", json={"decision": "approve"}),
    ]
    assert [r.status_code for r in responses] == [404] * 5
    assert eb.calls == []  # never reached the engine


def test_global_off_overrides_tenant_true_at_the_route():
    a = tenant(True)
    e = Env([a], au(a), ENABLE_ENGINE_CHAT=False)
    assert e.http.post("/api/v1/engine/conversations", json={"project_id": str(PROJECT)}).status_code == 404
    assert e.calls == []


def test_flag_flip_takes_effect_on_next_request_and_keeps_old_conversations_in_db():
    a = tenant(True)
    e = Env([a], au(a))
    cid = e.seed(a)
    assert e.http.post(f"/api/v1/engine/conversations/{cid}/abort").status_code == 200
    a.settings = {"features": {"ENABLE_ENGINE_CHAT": False}}
    assert e.http.post(f"/api/v1/engine/conversations/{cid}/abort").status_code == 404
    assert cid_in(e, cid)  # data untouched (R2)


def cid_in(e, cid):
    return uuid.UUID(cid) in e.db.rows
