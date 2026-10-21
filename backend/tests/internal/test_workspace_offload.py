"""U15: POST /internal/workspace/offload."""
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.internal_api.invocations import get_session_factory
from app.internal_api.router import mount_internal_router
from app.models.engine import EngineConversation
from app.services import conversation_workspace as cw
from app.services import session_storage as ss
from tests.internal.test_automation_scope import seed
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
AUTH = {"Authorization": f"Bearer {GATEWAY}"}


@pytest.fixture(autouse=True)
def _storage(tmp_path, monkeypatch):
    storage = ss.SessionStorage(base_path=tmp_path, checkpointing_enabled=False)
    monkeypatch.setattr(ss, "_session_storage", storage)
    monkeypatch.setattr(cw, "get_session_storage", lambda: storage)


@pytest.fixture
async def client(factory):  # noqa: F811
    app = FastAPI()
    mount_internal_router(app, Settings(
        _env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC))
    app.dependency_overrides[get_session_factory] = lambda: factory
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def world(maker):  # noqa: F811
    from app.models.project import Project

    (t,), (u,) = await seed(maker)
    project, conv = uuid.uuid4(), uuid.uuid4()
    async with maker() as db:
        db.add(Project(id=project, tenant_id=t, name="p", owner_id=u))
        await db.commit()
        db.add(EngineConversation(conversation_id=conv, tenant_id=t, project_id=project, user_id=u,
                                  session_id=f"{t}/{project}"))
        await db.commit()
    return t, project, conv


def body(t, project, conv, call="call_1", content="hello", run="run-1", **kw):
    return {"runId": run, "toolCallId": call, "tenantId": str(t), "projectId": str(project),
            "conversationId": str(conv), "content": content, **kw}


def headers(call="call_1", run="run-1"):
    return {**AUTH, "idempotency-key": f"{run}:{call}"}


async def test_requires_the_service_token(client):
    r = await client.post("/internal/workspace/offload", json={})
    assert r.status_code == 401


async def test_put_is_idempotent(client, maker):  # noqa: F811
    t, p, c = await world(maker)
    first = await client.post("/internal/workspace/offload", json=body(t, p, c), headers=headers())
    again = await client.post("/internal/workspace/offload", json=body(t, p, c, content="OTHER"), headers=headers())
    assert first.status_code == again.status_code == 200
    assert first.json()["path"] == again.json()["path"] == "tool_results/call_1.txt"
    assert first.json()["created"] is True and again.json()["created"] is False
    assert await cw.read(str(t), str(c), "tool_results/call_1.txt") == "hello"


async def test_two_calls_two_files(client, maker):  # noqa: F811
    t, p, c = await world(maker)
    a = await client.post("/internal/workspace/offload", json=body(t, p, c, call="a"), headers=headers("a"))
    b = await client.post("/internal/workspace/offload", json=body(t, p, c, call="b"), headers=headers("b"))
    assert a.json()["path"] != b.json()["path"]


async def test_oversize_is_413(client, maker):  # noqa: F811
    t, p, c = await world(maker)
    r = await client.post("/internal/workspace/offload",
                          json=body(t, p, c, content="x" * (cw.MAX_BYTES + 1)), headers=headers())
    assert r.status_code == 413


async def test_history_kind(client, maker):  # noqa: F811
    t, p, c = await world(maker)
    r = await client.post("/internal/workspace/offload", headers=headers("cmp-1"),
                          json=body(t, p, c, call="cmp-1", kind="history", content="# s"))
    assert r.status_code == 200 and r.json()["path"] == "conversation_history/cmp-1.md"


@pytest.mark.parametrize("call", ["../x", "a/b", "", ".h"])
async def test_bad_id_is_400(client, maker, call):  # noqa: F811
    t, p, c = await world(maker)
    r = await client.post("/internal/workspace/offload", json=body(t, p, c, call=call),
                          headers={**AUTH, "idempotency-key": f"run-1:{call}"})
    assert r.status_code == 400


async def test_idempotency_key_must_match(client, maker):  # noqa: F811
    t, p, c = await world(maker)
    r = await client.post("/internal/workspace/offload", json=body(t, p, c), headers={**AUTH, "idempotency-key": "x:y"})
    assert r.status_code == 400


async def test_unknown_conversation_and_cross_tenant_are_404(client, maker):  # noqa: F811
    t, p, c = await world(maker)
    other_t, other_p, other_c = await world(maker)
    r = await client.post("/internal/workspace/offload", json=body(t, p, uuid.uuid4()), headers=headers())
    assert r.status_code == 404
    # tenant A naming tenant B's conversation
    r = await client.post("/internal/workspace/offload", json=body(t, p, other_c), headers=headers())
    assert r.status_code == 404
    assert await cw.read(str(t), str(other_c), "tool_results/call_1.txt") is None


async def test_conversation_id_is_required(client, maker):  # noqa: F811
    t, p, c = await world(maker)
    b = body(t, p, c)
    del b["conversationId"]
    r = await client.post("/internal/workspace/offload", json=b, headers=headers())
    assert r.status_code == 400


async def test_stale_fence_is_rejected_and_nothing_is_written(client, maker):  # noqa: F811
    t, p, c = await world(maker)
    sid = f"{t}/{p}"
    ok = await client.post("/internal/workspace/offload", json=body(t, p, c, call="n"),
                           headers={**headers("n"), "x-engine-fence": "5", "x-engine-session": sid})
    assert ok.status_code == 200
    stale = await client.post("/internal/workspace/offload", json=body(t, p, c, call="o"),
                              headers={**headers("o"), "x-engine-fence": "4", "x-engine-session": sid})
    assert stale.status_code == 409
    assert await cw.read(str(t), str(c), "tool_results/o.txt") is None
