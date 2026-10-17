"""U2 - the decision route: RBAC through the ledger, legacy fallback, abort closing rows (AE2, AE4)."""
import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.v1 import engine_conversations as ec
from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.config import Settings
from app.core.database import get_db
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.models.tool_approval import ToolApproval
from app.models.user import User
from app.services.approval_service import ApprovalService
from app.services.engine_client import EngineClient, get_engine_client
from tests.test_approval_service import FakeAudit

SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
TENANT, OTHER = uuid.uuid4(), uuid.uuid4()
PROJECT = uuid.uuid4()
CUTOFF = "2026-10-01T00:00:00+00:00"
CUTOFF_MS = datetime.fromisoformat(CUTOFF).timestamp() * 1000


def cfg(**kw) -> Settings:
    base = dict(SECRET_KEY=SECRET, ENGINE_SERVICE_TOKEN=SVC, ENGINE_URL="http://engine.test",
                ENGINE_SESSION_ID="s1", ENABLE_ENGINE_API=True, ENABLE_ENGINE_CHAT=True)
    base.update(kw)
    return Settings(_env_file=None, **base)


class Env:
    def __init__(self, db, settings):
        self.db, self.calls, self.items = db, [], []
        self.engine_client = EngineClient(settings, transport=httpx.MockTransport(self._handle))
        self.audit = FakeAudit()
        self.app = FastAPI()
        ec.mount_engine_conversations(self.app, settings)

        async def _db():
            yield db

        self.app.dependency_overrides[get_db] = _db
        self.app.dependency_overrides[get_engine_client] = lambda: self.engine_client
        self.app.dependency_overrides[get_current_user] = lambda: self.current
        self.http = TestClient(self.app, raise_server_exceptions=False)
        self.current = None

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.method == "GET" and req.url.path.endswith("/approvals"):
            return httpx.Response(200, json={"conversationId": "x", "approvals": self.items})
        if req.url.path.endswith("/decision"):
            return httpx.Response(200, json={"approval": {"status": "approved"}})
        if req.url.path.endswith("/abort"):
            return httpx.Response(200, json={"aborted": True})
        return httpx.Response(404, json={})

    def decide_calls(self):
        return [c for c in self.calls if c.url.path.endswith("/decision")]


@pytest.fixture
async def env(test_db, monkeypatch):
    db = test_db
    db.add(Tenant(id=TENANT, name="t", slug="t", settings={"features": {"ENABLE_ENGINE_CHAT": True}}))
    db.add(Tenant(id=OTHER, name="o", slug="o", settings={}))
    await db.commit()

    async def role(self, project_id, user_id, tenant_id):
        return None if str(user_id) in e.no_project else "member"

    monkeypatch.setattr(ec.ProjectService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ec.ProjectService, "get_user_role", role)
    e = Env(db, cfg(APPROVALS_LEGACY_CUTOFF=CUTOFF))
    e.no_project = set()
    monkeypatch.setattr(ec, "ApprovalService", lambda d: ApprovalService(d, audit=e.audit))
    return e


async def make_user(env, role, tenant=TENANT):
    u = User(id=uuid.uuid4(), tenant_id=tenant, email=f"{uuid.uuid4().hex[:6]}@x.io", name=role,
             role=role, is_active=True)
    env.db.add(u)
    await env.db.commit()
    return u


def as_auth(u: User) -> AuthUser:
    return AuthUser(id=str(u.id), email=u.email, tenant_id=str(u.tenant_id), tenant_slug="t",
                    role=u.role, auth_provider="local")


async def seed(env, owner: User, task="t1", indexed=True, requested_by=None, approver_role="member"):
    conv = EngineConversation(conversation_id=uuid.uuid4(), tenant_id=TENANT, project_id=PROJECT,
                              user_id=owner.id, session_id="s1")
    env.db.add(conv)
    if indexed:
        env.db.add(ToolApproval(
            id=uuid.uuid4(), tenant_id=TENANT, conversation_id=conv.conversation_id, tool_task_id=task,
            run_id=f"r-{uuid.uuid4().hex[:6]}", tool_call_id=f"c-{uuid.uuid4().hex[:6]}", tool_name="execute_python", status="pending",
            approver_role=approver_role, requested_by=(requested_by or owner).id))
    await env.db.commit()
    return conv


def url(conv, task="t1"):
    return f"/api/v1/engine/conversations/{conv.conversation_id}/approvals/{task}/decision"


# --- AE2 -------------------------------------------------------------------------

async def test_ae2_viewer_other_project_and_other_tenant_never_reach_the_engine(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    viewer = await make_user(env, "viewer")
    outsider = await make_user(env, "admin")
    env.no_project.add(str(outsider.id))
    stranger = await make_user(env, "admin", tenant=OTHER)
    for who, status in ((viewer, 403), (outsider, 403), (stranger, 404)):
        env.current = as_auth(who)
        r = env.http.post(url(conv), json={"decision": "approve"})
        assert r.status_code == status, (who.role, r.text)
    assert env.decide_calls() == []
    row = (await env.db.execute(select(ToolApproval))).scalars().one()
    assert row.status == "pending"


async def test_authorised_admin_gets_the_engine_approval(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    admin = await make_user(env, "admin")
    env.current = as_auth(admin)
    r = env.http.post(url(conv), json={"decision": "approve", "note": "ok", "principal": "attacker"})
    assert r.status_code == 200 and r.json() == {"approval": {"status": "approved"}}
    (call,) = env.decide_calls()
    assert call.headers["x-oniva-principal"] == str(admin.id)
    assert json.loads(call.content) == {"decision": "approve", "note": "ok"}


async def test_member_may_decide_their_own_member_level_row_but_not_an_admin_row(env):
    owner = await make_user(env, "member")
    ok = await seed(env, owner)
    admin_row = await seed(env, owner, approver_role="admin")
    env.current = as_auth(owner)
    assert env.http.post(url(ok), json={"decision": "approve"}).status_code == 200
    assert env.http.post(url(admin_row), json={"decision": "approve"}).status_code == 403
    assert len(env.decide_calls()) == 1


async def test_engine_unreachable_is_502_and_row_stays_deciding(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    env.current = as_auth(owner)
    env._handle = lambda req: httpx.Response(502, json={"error": "engine_error"})
    env.engine_client._http = httpx.AsyncClient(transport=httpx.MockTransport(env._handle))
    r = env.http.post(url(conv), json={"decision": "approve"})
    assert r.status_code == 502
    row = (await env.db.execute(select(ToolApproval))).scalars().one()
    await env.db.refresh(row)
    assert row.status == "deciding"


# --- legacy fallback (KTD3) ------------------------------------------------------

async def test_legacy_item_before_cutoff_uses_owner_or_admin_rule(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner, indexed=False)
    env.items = [{"toolTaskId": "t1", "requestedAt": CUTOFF_MS - 1000}]
    other_member = await make_user(env, "member")
    env.current = as_auth(other_member)
    assert env.http.post(url(conv), json={"decision": "approve"}).status_code == 403
    assert env.decide_calls() == []
    for who in (owner, await make_user(env, "admin"), await make_user(env, "owner")):
        env.current = as_auth(who)
        assert env.http.post(url(conv), json={"decision": "approve"}).status_code == 200


async def test_missing_row_after_cutoff_or_without_cutoff_is_409_not_indexed(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner, indexed=False)
    env.current = as_auth(owner)
    env.items = [{"toolTaskId": "t1", "requestedAt": CUTOFF_MS + 1000}]
    r = env.http.post(url(conv), json={"decision": "approve"})
    assert r.status_code == 409 and r.json()["detail"] == "approval_not_indexed"
    env.items = []  # not in the engine document either
    assert env.http.post(url(conv), json={"decision": "approve"}).status_code == 409

    off = Env(env.db, cfg())  # cutoff unset: the fallback is disabled
    off.current = env.current
    off.items = [{"toolTaskId": "t1", "requestedAt": 1}]
    r = off.http.post(url(conv), json={"decision": "approve"})
    assert r.status_code == 409 and r.json()["detail"] == "approval_not_indexed"
    assert off.decide_calls() == [] and env.decide_calls() == []


# --- abort -------------------------------------------------------------------------

async def test_abort_closes_that_conversations_pending_rows_cancelled(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    elsewhere = await seed(env, owner, task="t9")
    env.current = as_auth(owner)
    r = env.http.post(f"/api/v1/engine/conversations/{conv.conversation_id}/abort", json={})
    assert r.status_code == 200
    rows = {x.conversation_id: x for x in (await env.db.execute(select(ToolApproval))).scalars().all()}
    assert rows[conv.conversation_id].status == "cancelled"
    assert rows[elsewhere.conversation_id].status == "pending"
    assert [e.action for e in env.audit.entries] == ["approval_cancelled"]
