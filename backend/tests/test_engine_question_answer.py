"""U11 - answering an ask_user question through the control plane (R10, AE8)."""
import json
import uuid
from datetime import datetime, timedelta, timezone

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
from app.services.approval_service import ApprovalService, reset_reconcile_throttle
from app.services.engine_client import EngineClient, get_engine_client
from app.services.inbox_service import InboxService
from tests.test_approval_service import FakeAudit

SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
TENANT, OTHER = uuid.uuid4(), uuid.uuid4()
PROJECT = uuid.uuid4()
ANSWER = {"answer": {"optionIds": ["a"]}}


def cfg() -> Settings:
    return Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_SERVICE_TOKEN=SVC, ENGINE_URL="http://engine.test",
                    ENGINE_SESSION_ID="s1", ENABLE_ENGINE_API=True, ENABLE_ENGINE_CHAT=True)


class Env:
    def __init__(self, db):
        self.db, self.calls, self.questions = db, [], []
        self.answer_response = None  # (status, body) override
        self.engine_client = EngineClient(cfg(), transport=httpx.MockTransport(self._handle))
        self.audit = FakeAudit()
        self.app = FastAPI()
        ec.mount_engine_conversations(self.app, cfg())

        async def _db():
            yield db

        self.app.dependency_overrides[get_db] = _db
        self.app.dependency_overrides[get_engine_client] = lambda: self.engine_client
        self.app.dependency_overrides[get_current_user] = lambda: self.current
        self.http = TestClient(self.app, raise_server_exceptions=False)
        self.current = None

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.method == "GET" and req.url.path.endswith("/questions"):
            return httpx.Response(200, json={"conversationId": "x", "questions": {"items": self.questions}})
        if req.url.path.endswith("/answer"):
            if self.answer_response is not None:
                return httpx.Response(self.answer_response[0], json=self.answer_response[1])
            return httpx.Response(200, json={"question": {"status": "answered"}})
        return httpx.Response(404, json={})

    def answer_calls(self):
        return [c for c in self.calls if c.url.path.endswith("/answer")]


@pytest.fixture
async def env(test_db, monkeypatch):
    db = test_db
    db.add(Tenant(id=TENANT, name="t", slug="t", settings={"features": {"ENABLE_ENGINE_CHAT": True}}))
    db.add(Tenant(id=OTHER, name="o", slug="o", settings={}))
    await db.commit()

    async def role(self, project_id, user_id, tenant_id):
        return "member"

    monkeypatch.setattr(ec.ProjectService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ec.ProjectService, "get_user_role", role)
    e = Env(db)
    monkeypatch.setattr(ec, "ApprovalService", lambda d: ApprovalService(d, audit=e.audit))
    reset_reconcile_throttle()
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


async def seed(env, owner: User, task="q1", **row_kw):
    conv = EngineConversation(conversation_id=uuid.uuid4(), tenant_id=TENANT, project_id=PROJECT,
                              user_id=owner.id, session_id="s1")
    env.db.add(conv)
    env.db.add(ToolApproval(
        id=uuid.uuid4(), tenant_id=TENANT, conversation_id=conv.conversation_id, tool_task_id=task,
        run_id=f"r-{uuid.uuid4().hex[:6]}", tool_call_id=f"c-{uuid.uuid4().hex[:6]}", tool_name="ask_user",
        kind="question", rule="question", status=row_kw.pop("status", "pending"),
        approver_role="member", requested_by=owner.id, arguments_preview={"question": "Which region?"},
        **row_kw))
    await env.db.commit()
    return conv


def url(conv, task="q1"):
    return f"/api/v1/engine/conversations/{conv.conversation_id}/questions/{task}/answer"


async def the_row(env) -> ToolApproval:
    row = (await env.db.execute(select(ToolApproval))).scalars().one()
    await env.db.refresh(row)
    return row


async def test_owner_answers_engine_sees_one_answer_with_principal(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    env.current = as_auth(owner)
    r = env.http.post(url(conv), json={**ANSWER, "principal": "attacker"})
    assert r.status_code == 200 and r.json() == {"question": {"status": "answered"}}
    (call,) = env.answer_calls()
    assert call.headers["x-oniva-principal"] == str(owner.id)
    assert json.loads(call.content) == ANSWER
    row = await the_row(env)
    assert row.status == "answered" and row.decided_by == owner.id and row.decided_at is not None
    (entry,) = env.audit.entries
    assert entry.action == "question_answered" and entry.user_id == owner.id
    assert entry.extra_metadata["tool_task_id"] == "q1"
    assert "Which region" not in json.dumps(entry.extra_metadata, default=str)


async def test_other_member_403_other_tenant_404_admin_and_owner_200(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    env.current = as_auth(await make_user(env, "member"))
    assert env.http.post(url(conv), json=ANSWER).status_code == 403
    env.current = as_auth(await make_user(env, "admin", tenant=OTHER))
    assert env.http.post(url(conv), json=ANSWER).status_code == 404
    assert env.answer_calls() == [] and (await the_row(env)).status == "pending"
    env.current = as_auth(await make_user(env, "admin"))
    assert env.http.post(url(conv), json=ANSWER).status_code == 200
    row = await the_row(env)
    assert row.status == "answered" and row.decided_by != owner.id

    conv2 = await seed(env, owner, task="q2")
    env.current = as_auth(await make_user(env, "owner"))
    assert env.http.post(url(conv2, "q2"), json=ANSWER).status_code == 200


async def test_unknown_question_is_404(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    env.current = as_auth(owner)
    assert env.http.post(url(conv, "nope"), json=ANSWER).status_code == 404
    assert env.answer_calls() == []


async def test_second_answer_is_409(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    admin = await make_user(env, "admin")
    env.current = as_auth(owner)
    assert env.http.post(url(conv), json=ANSWER).status_code == 200
    env.current = as_auth(admin)
    r = env.http.post(url(conv), json=ANSWER)
    assert r.status_code == 409 and r.json()["detail"] == "already_answered"
    assert len(env.answer_calls()) == 1


async def test_engine_502_leaves_deciding_only_the_answerer_retries(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    admin = await make_user(env, "admin")
    env.current = as_auth(owner)
    env.answer_response = (502, {"error": "engine_error"})
    assert env.http.post(url(conv), json=ANSWER).status_code == 502
    assert (await the_row(env)).status == "deciding"
    env.current = as_auth(admin)
    r = env.http.post(url(conv), json=ANSWER)
    assert r.status_code == 409 and r.json()["detail"] == "decision_in_progress"
    env.answer_response = None
    env.current = as_auth(owner)
    # the retry repeats the original inputs, not the new body
    assert env.http.post(url(conv), json={"answer": {"optionIds": ["z"]}}).status_code == 200
    assert json.loads(env.answer_calls()[-1].content) == ANSWER
    assert (await the_row(env)).status == "answered"
    assert [e.action for e in env.audit.entries] == ["question_answered"]


async def test_engine_rejections_map_and_settle_the_row(env):
    owner = await make_user(env, "member")
    for reason, status, code, final in (("expired", 410, "approval_expired", "expired"),
                                        ("not_waiting", 409, "not_waiting", "cancelled")):
        conv = await seed_fresh(env, owner)
        env.current = as_auth(owner)
        env.answer_response = (status, {"error": "question_rejected", "reason": reason})
        r = env.http.post(url(conv), json=ANSWER)
        assert r.status_code == status and r.json()["detail"] == code
        assert (await the_row(env)).status == final


async def seed_fresh(env, owner):
    for row in (await env.db.execute(select(ToolApproval))).scalars().all():
        await env.db.delete(row)
    await env.db.commit()
    return await seed(env, owner)


async def test_invalid_answer_is_400_and_row_returns_to_pending(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    env.current = as_auth(owner)
    env.answer_response = (400, {"error": "question_rejected", "reason": "invalid"})
    assert env.http.post(url(conv), json=ANSWER).status_code == 400
    row = await the_row(env)
    assert row.status == "pending" and row.decided_by is None
    env.answer_response = None
    assert env.http.post(url(conv), json=ANSWER).status_code == 200


async def test_engine_already_answered_finalises_from_engine_principal(env):
    owner = await make_user(env, "member")
    admin = await make_user(env, "admin")
    conv = await seed(env, owner)
    item = {"status": "answered", "toolTaskId": "q1",
            "answer": {"optionIds": ["a"], "text": None, "principal": str(admin.id), "answeredAt": 1_800_000_000_000}}
    env.answer_response = (409, {"error": "question_rejected", "reason": "already_answered", "question": item})
    env.current = as_auth(owner)
    r = env.http.post(url(conv), json=ANSWER)
    assert r.status_code == 409
    row = await the_row(env)
    assert row.status == "answered" and row.decided_by == admin.id
    (entry,) = env.audit.entries
    assert entry.user_id == admin.id and entry.extra_metadata["from_engine"] is True


async def test_engine_already_answered_by_same_caller_is_200(env):
    owner = await make_user(env, "member")
    conv = await seed(env, owner)
    item = {"status": "answered", "toolTaskId": "q1",
            "answer": {"optionIds": ["a"], "text": None, "principal": str(owner.id), "answeredAt": 1_800_000_000_000}}
    env.answer_response = (409, {"error": "question_rejected", "reason": "already_answered", "question": item})
    env.current = as_auth(owner)
    r = env.http.post(url(conv), json=ANSWER)
    assert r.status_code == 200 and r.json() == {"question": item}
    assert (await the_row(env)).status == "answered"


async def test_inbox_shows_question_with_answer_action_then_removes_it(env):
    owner = await make_user(env, "member")
    other = await make_user(env, "member")
    admin = await make_user(env, "admin")
    conv = await seed(env, owner)
    svc = InboxService(env.db, approvals=ApprovalService(env.db, audit=env.audit))
    (item,) = (await svc.list_for_user(as_auth(owner), kinds=["question"]))[0]
    (action,) = item["actions"]
    assert action["type"] == "answer" and action["allowed"] is True
    assert action["endpoint"].endswith(f"/{conv.conversation_id}/questions/q1/answer")
    assert item["summary"] == "Which region?"
    adm = (await svc.list_for_user(as_auth(admin), kinds=["question"]))[0]
    assert adm[0]["actions"][0]["type"] == "answer"
    assert (await svc.list_for_user(as_auth(other), kinds=["question"]))[0] == []
    env.current = as_auth(owner)
    assert env.http.post(url(conv), json=ANSWER).status_code == 200
    assert (await svc.list_for_user(as_auth(owner), kinds=["question"]))[0] == []


async def test_reconcile_finalises_answered_and_cancels_missing_questions(env):
    owner = await make_user(env, "member")
    admin = await make_user(env, "admin")
    conv = await seed(env, owner)
    env.questions = [{"toolTaskId": "q1", "status": "answered",
                      "answer": {"optionIds": ["a"], "text": None, "principal": str(admin.id),
                                 "answeredAt": 1_800_000_000_000}}]
    svc = ApprovalService(env.db, audit=env.audit)
    assert await svc.reconcile(TENANT, env.engine_client, force=True) == 1
    row = await the_row(env)
    assert row.status == "answered" and row.decided_by == admin.id

    # a cancelled question and a question missing from the document past the grace period
    for item_status in ("cancelled", None):
        await seed_fresh(env, owner)
        row = await the_row(env)
        row.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        env.questions = [{"toolTaskId": "q1", "status": "cancelled"}] if item_status else []
        await env.db.commit()
        assert await svc.reconcile(TENANT, env.engine_client, force=True) == 1
        assert (await the_row(env)).status == "cancelled"
    assert not any(c.url.path.endswith("/approvals") for c in env.calls)
