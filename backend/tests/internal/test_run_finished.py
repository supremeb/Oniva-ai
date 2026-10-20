"""Plan 003 U5: POST /internal/runs/finished."""
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.core.database import get_system_db
from app.internal_api import runs as runs_mod
from app.internal_api.router import mount_internal_router
from app.services import automation_scope as scope
from app.services import run_handlers
from tests.internal.test_automation_scope import seed
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
AUTH = {"Authorization": f"Bearer {GATEWAY}"}


@pytest.fixture(autouse=True)
def _clean():
    run_handlers.reset()
    runs_mod.reset_dedupe()
    yield
    run_handlers.reset()
    runs_mod.reset_dedupe()


@pytest.fixture
async def client(maker):  # noqa: F811
    app = FastAPI()
    mount_internal_router(app, Settings(
        _env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC))

    async def _db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_system_db] = _db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


def payload(conversation_id, submission="sub-1", status="done", **kw):
    return {"kind": "run_finished", "conversationId": str(conversation_id), "submissionId": submission,
            "status": status, "usage": {"requests": 1}, **kw}


async def bound(maker, factory, kind="fleet"):  # noqa: F811
    (t,), (u,) = await seed(maker)
    ref = uuid.uuid4()
    cid, project_id, _ = await scope.bind_run_conversation(factory, kind, ref, t, u)
    return ref, cid, t, project_id


async def test_requires_the_service_token(client):
    r = await client.post("/internal/runs/finished", json=payload(uuid.uuid4()))
    assert r.status_code == 401


async def test_dispatches_to_the_registered_on_finished_with_the_run_ref(client, maker, factory):
    calls = []

    async def on_finished(ref, snapshot):
        calls.append((ref, snapshot))

    run_handlers.register("fleet", on_finished=on_finished)
    ref_id, cid, tenant, project = await bound(maker, factory)
    r = await client.post("/internal/runs/finished", json=payload(cid, reason=None), headers=AUTH)
    assert r.status_code == 200 and r.json()["handled"] is True
    [(ref, snap)] = calls
    assert (ref.kind, ref.ref_id, ref.tenant_id, ref.project_id, ref.conversation_id) == (
        "fleet", ref_id, tenant, project, cid)
    assert snap["source"] == "push" and snap["status"] == "done" and snap["submissionId"] == "sub-1"
    assert snap["usage"] == {"requests": 1}


async def test_the_same_post_twice_applies_once_and_both_answer_200(client, maker, factory):
    calls = []

    async def on_finished(ref, snapshot):
        calls.append(ref)

    run_handlers.register("fleet", on_finished=on_finished)
    _, cid, *_ = await bound(maker, factory)
    first = await client.post("/internal/runs/finished", json=payload(cid), headers=AUTH)
    second = await client.post("/internal/runs/finished", json=payload(cid), headers=AUTH)
    assert first.status_code == second.status_code == 200
    assert len(calls) == 1 and second.json()["duplicate"] is True


async def test_a_different_submission_of_the_same_run_is_dispatched(client, maker, factory):
    calls = []

    async def on_finished(ref, snapshot):
        calls.append(snapshot["submissionId"])

    run_handlers.register("fleet", on_finished=on_finished)
    _, cid, *_ = await bound(maker, factory)
    for sub in ("a", "b"):
        await client.post("/internal/runs/finished", json=payload(cid, submission=sub), headers=AUTH)
    assert calls == ["a", "b"]


async def test_a_handler_that_raises_is_not_deduplicated_so_the_engine_retry_works(client, maker, factory):
    attempts = []

    async def on_finished(ref, snapshot):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("db blip")

    run_handlers.register("fleet", on_finished=on_finished)
    _, cid, *_ = await bound(maker, factory)
    app_client = client
    with pytest.raises(RuntimeError):
        await app_client.post("/internal/runs/finished", json=payload(cid), headers=AUTH)
    ok = await app_client.post("/internal/runs/finished", json=payload(cid), headers=AUTH)
    assert ok.status_code == 200 and len(attempts) == 2


async def test_an_unknown_conversation_is_404(client):
    r = await client.post("/internal/runs/finished", json=payload(uuid.uuid4()), headers=AUTH)
    assert r.status_code == 404 and r.json() == {"error": "unknown_run"}


async def test_a_kind_with_no_handler_is_skipped_with_200(client, maker, factory):
    _, cid, *_ = await bound(maker, factory)
    r = await client.post("/internal/runs/finished", json=payload(cid), headers=AUTH)
    assert r.status_code == 200 and r.json()["handled"] is False


async def test_a_malformed_conversation_id_is_422(client):
    r = await client.post("/internal/runs/finished", json=payload("not-a-uuid"), headers=AUTH)
    assert r.status_code == 422
