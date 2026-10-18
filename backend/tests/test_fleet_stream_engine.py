"""Plan 003 U9: the job stream proxies the engine watch for engine jobs; the Fleet list hides engine bookkeeping."""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1 import background_jobs as bj
from app.api.v1 import fleet as fleet_api
from app.models.background_job import BackgroundJob, JobType
from app.models.engine import EngineConversation
from app.services import fleet_engine as fe
from app.services import fleet_run_sync as frs
from app.services import run_handlers as rh
from app.services.engine_client import EngineError
from tests.contracts.recorder import parse_sse_text
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)
from tests.test_fleet_cancel_engine import Engine, cancel, started, user_for  # noqa: F401
from tests.test_fleet_engine_start import make_job, wired  # noqa: F401
from tests.test_fleet_run_sync import USAGE, push, ref_for, view_for


@pytest.fixture(autouse=True)
def _wiring():
    rh.reset()
    fe.register_hooks()
    frs.register_hooks()
    yield
    rh.reset()


@pytest.fixture
def engine(monkeypatch):
    eng = Engine()
    monkeypatch.setattr(fe, "get_engine_client", lambda: eng)
    import app.services.engine_client as ec
    monkeypatch.setattr(ec, "get_engine_client", lambda: eng)
    return eng


@pytest.fixture(autouse=True)
def _no_delivery(monkeypatch):
    monkeypatch.setattr(frs, "deliver", AsyncMock())


@pytest.fixture
def db_for_stream(monkeypatch, maker):  # noqa: F811
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: maker())
    monkeypatch.setattr("app.core.database.set_tenant_context", AsyncMock())


class Upstream:
    """An open watch response: yields the scripted frames, runs ``after`` just before the last one."""

    def __init__(self, frames, after=None):
        self.frames, self.after, self.closed = frames, after, False

    async def aiter_raw(self):
        for i, f in enumerate(self.frames):
            if self.after is not None and i == len(self.frames) - 1:
                await self.after()  # the engine's push lands as the last frame goes out
            yield f"data: {json.dumps(f)}\n\n".encode()

    async def aclose(self):
        self.closed = True


def snapshot_frame(view):
    return {"cursor": 1, "snapshot": view}


def finish_patch(view, final_text="All done."):
    ops = [{"op": "add", "path": "/entries/-", "value": {
        "kind": "assistant", "payload": {"message": {"blocks": [{"type": "text", "text": final_text}]}}}},
           {"op": "replace", "path": "/run/active", "value": False}]
    return {"cursor": 2, "patch": ops}


def mid_view(job_id):
    view = view_for(job_id, active=True)
    view["entries"] = view["entries"][:-1]  # no final answer yet
    return view


async def stream(maker, t, u, job_id):  # noqa: F811
    async with maker() as db:
        resp = await bj.stream_job_progress(job_id, db=db, user=user_for(u, t), tenant_id=t)
        text = ""
        async for chunk in resp.body_iterator:
            text += chunk if isinstance(chunk, str) else chunk.decode()
    return parse_sse_text(text)


async def test_a_live_engine_job_streams_steps_from_the_watch_then_the_terminal_event(
        maker, factory, engine, wired, db_for_stream):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    final = view_for(job_id)

    async def settle():
        await frs.on_finished(ref, push(job_id, view=final))

    up = Upstream([snapshot_frame(mid_view(job_id)), finish_patch(None)], after=settle)

    async def watch_stream(*a, **k):
        return up

    engine.watch_stream = watch_stream
    events = await stream(maker, t, u, job_id)
    names = [e["event"] for e in events]
    assert names[0] == "progress" and names[-1] == "completed"
    steps = [e["data"] for e in events if e["event"] == "step"]
    assert [s["node_type"] for s in steps] == ["text_response", "tool_call", "text_response", "usage"]  # no repeats
    assert steps[1]["tool_name"] == "web_search"
    assert events[-1]["data"]["result"]["output"] == "All done." and up.closed


async def test_an_engine_outage_falls_back_to_polling_the_row(maker, factory, engine, wired, db_for_stream):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)

    async def watch_stream(*a, **k):
        await frs.on_finished(ref, push(job_id, view=view_for(job_id)))
        raise EngineError(502, "engine_unreachable")

    engine.watch_stream = watch_stream
    events = await stream(maker, t, u, job_id)
    assert events[-1]["event"] == "completed"
    assert [e["data"]["node_type"] for e in events if e["event"] == "step"] == [
        "text_response", "tool_call", "text_response", "usage"]


async def test_a_cancelled_engine_job_streams_cancelled_and_stays_cancelled(
        maker, factory, engine, wired, db_for_stream):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)

    async def cancel_then_settle():
        await cancel(maker, u, t, job_id, "stop")
        await frs.on_finished(ref, push(job_id, "unanswered", "aborted", view=view_for(job_id)))

    up = Upstream([snapshot_frame(mid_view(job_id))], after=cancel_then_settle)

    async def watch_stream(*a, **k):
        return up

    engine.watch_stream = watch_stream
    events = await stream(maker, t, u, job_id)
    assert events[-1]["event"] == "cancelled" and events[-1]["data"]["error"] == "Cancelled: stop"
    assert len(engine.aborts) == 1


async def test_the_stream_is_scoped_to_the_owner_and_tenant(maker, factory, engine, wired, db_for_stream):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    for tenant, owner in ((uuid.uuid4(), u), (t, uuid.uuid4())):
        with pytest.raises(HTTPException) as e:
            await stream(maker, tenant, owner, job_id)
        assert e.value.status_code == 404


async def test_a_job_that_never_started_streams_by_polling(maker, factory, engine, wired, db_for_stream):  # noqa: F811
    t, u, _, job_id = await make_job(maker, status="completed")
    async with maker() as db:
        (await db.get(BackgroundJob, job_id)).result = {"output": "x"}
        await db.commit()
    events = await stream(maker, t, u, job_id)
    assert [e["event"] for e in events] == ["progress", "completed"]


async def list_runs(maker, t, u):  # noqa: F811
    async with maker() as db:
        return await fleet_api.list_fleet_runs(
            user=user_for(u, t), tenant_id=t, db=db, status=None, agent_id=None, page=1, page_size=20)


async def test_the_fleet_list_hides_engine_bookkeeping_and_exposes_the_conversation(
        maker, factory, engine, wired):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    async with maker() as db:
        job = await db.get(BackgroundJob, job_id)
        job.input_data = {**job.input_data, "start_attempts": 1, "abort_pending": True}
        conversation = job.conversation_id
        await db.commit()
    item = (await list_runs(maker, t, u)).items[0]
    assert item.input_data == {"task": "do it"}
    assert item.conversation_id == str(conversation)
    async with maker() as db:  # the stored row keeps its marks
        assert fe.is_engine_job(await db.get(BackgroundJob, job_id))


async def test_a_settled_engine_job_lists_with_status_result_and_tokens(maker, factory, engine, wired):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    await frs.on_finished(await ref_for(maker, job_id), push(job_id, view=view_for(job_id)))
    item = (await list_runs(maker, t, u)).items[0]
    assert item.status == "completed" and item.result["output"] == "All done."
    assert item.total_tokens == 42 and item.progress == 100 and item.duration_ms is not None


async def test_a_legacy_job_lists_unchanged(maker):  # noqa: F811
    t, u, _, job_id = await make_job(maker)
    async with maker() as db:
        job = await db.get(BackgroundJob, job_id)
        job.input_data = {"task": "legacy", "engine": "other"}
        await db.commit()
    item = (await list_runs(maker, t, u)).items[0]
    assert item.input_data == {"task": "legacy", "engine": "other"} and item.conversation_id is None
