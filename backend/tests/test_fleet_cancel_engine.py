"""Plan 003 U8: Fleet intervene is an engine steer, cancel is an engine abort (SQLite tier, fake engine)."""
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1 import background_jobs as bj
from app.api.v1 import fleet as fleet_api
from app.models.background_job import BackgroundJob
from app.schemas.background_job import CancelJobRequest
from app.services import fleet_engine as fe
from app.services.engine_client import EngineError
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)
from tests.test_fleet_engine_start import FakeEngine, make_job, reload, wired, _wiring  # noqa: F401


class Engine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.aborts = []
        self.abort_down = False

    async def abort(self, session, tenant, project, conversation, reason=None):
        if self.abort_down:
            raise EngineError(502, "engine_unreachable")
        self.aborts.append((conversation, reason))
        return {"aborted": True}


@pytest.fixture
def engine(monkeypatch):
    eng = Engine()
    monkeypatch.setattr(fe, "get_engine_client", lambda: eng)
    return eng


def user_for(u, t):
    return SimpleNamespace(id=str(u), email="me@example.com", tenant_id=str(t))


async def started(maker, factory, engine, wired, **kw):  # noqa: F811
    t, u, a, job_id = await make_job(maker, **kw)
    await fe.start_fleet_run(factory, job_id)
    return t, u, job_id


async def intervene(maker, t, u, job_id, message="focus on X"):  # noqa: F811
    async with maker() as db:
        return await fleet_api.intervene_in_agent(
            job_id=job_id, body=fleet_api.InterventionRequest(message=message),
            user=user_for(u, t), tenant_id=t, db=db)


async def cancel(maker, u, t, job_id, reason=None):  # noqa: F811
    async with maker() as db:
        return await bj.cancel_job(job_id, CancelJobRequest(reason=reason) if reason else None, db=db,
                                   user=user_for(u, t))


async def test_intervene_submits_one_steer_with_a_stable_request_id(maker, factory, engine, wired):
    t, u, job_id = await started(maker, factory, engine, wired)
    resp = await intervene(maker, t, u, job_id)
    assert resp.message == "focus on X" and resp.sender == "me@example.com" and resp.timestamp
    await intervene(maker, t, u, job_id)  # repeat: same key, deduplicated by the engine
    steer_keys = [k for k in engine.submissions if k.startswith("steer:")]
    assert len(steer_keys) == 1 and steer_keys[0].startswith(f"steer:{job_id}:")
    body = engine.submissions[steer_keys[0]]["body"]
    assert body == {"text": "focus on X", "whenBusy": "steer", "requestId": steer_keys[0]}
    await intervene(maker, t, u, job_id, "another")
    assert len([k for k in engine.submissions if k.startswith("steer:")]) == 2


async def test_intervene_on_pending_finished_or_foreign_tenant_job_is_404(maker, factory, engine, wired):
    t, u, _, pending = await make_job(maker)
    with pytest.raises(HTTPException) as e:
        await intervene(maker, t, u, pending)
    assert e.value.status_code == 404
    t2, u2, done = await started(maker, factory, engine, wired)
    async with maker() as db:
        (await db.get(BackgroundJob, done)).status = "completed"
        await db.commit()
    with pytest.raises(HTTPException) as e:
        await intervene(maker, t2, u2, done)
    assert e.value.status_code == 404
    t3, u3, live = await started(maker, factory, engine, wired)
    with pytest.raises(HTTPException) as e:
        await intervene(maker, uuid.uuid4(), u3, live)
    assert e.value.status_code == 404
    assert not [k for k in engine.submissions if k.startswith("steer:")]


async def test_intervene_engine_outage_is_503(maker, factory, engine, wired):
    t, u, job_id = await started(maker, factory, engine, wired)
    engine.down = True
    with pytest.raises(HTTPException) as e:
        await intervene(maker, t, u, job_id)
    assert e.value.status_code == 503


async def test_cancel_aborts_once_and_a_second_cancel_is_400(maker, factory, engine, wired):
    t, u, job_id = await started(maker, factory, engine, wired)
    resp = await cancel(maker, u, t, job_id, "no longer needed")
    assert resp.success and resp.status == "cancelled"
    assert len(engine.aborts) == 1 and engine.aborts[0][1] == "no longer needed"
    job = await reload(maker, job_id)
    assert job.status == "cancelled" and job.error == "Cancelled: no longer needed"
    assert "abort_pending" not in job.input_data
    with pytest.raises(HTTPException) as e:
        await cancel(maker, u, t, job_id)
    assert e.value.status_code == 400 and "already completed" in e.value.detail
    assert len(engine.aborts) == 1


async def test_a_late_settle_leaves_the_job_cancelled(maker, factory, engine, wired):
    t, u, job_id = await started(maker, factory, engine, wired)
    await cancel(maker, u, t, job_id)
    await fe.fail(SimpleNamespace(tenant_id=t, ref_id=job_id), "engine_conversation_missing")
    res = await fe.start_fleet_run(factory, job_id)
    assert res.status == "already_started"
    assert (await reload(maker, job_id)).status == "cancelled"


async def test_engine_outage_on_cancel_still_cancels_and_the_reconciler_retries_the_abort(
        maker, factory, engine, wired):
    t, u, job_id = await started(maker, factory, engine, wired)
    engine.abort_down = True
    resp = await cancel(maker, u, t, job_id)
    assert resp.status == "cancelled"
    job = await reload(maker, job_id)
    assert job.status == "cancelled" and job.input_data["abort_pending"] is True

    async with maker() as db:
        refs = await fe.find_stuck(db, timedelta(seconds=-1))
    assert [r.ref_id for r in refs] == [job_id]
    still = await fe.start_missing(refs[0])
    assert still.status == "failed" and (await reload(maker, job_id)).input_data["abort_pending"] is True

    engine.abort_down = False
    done = await fe.start_missing(refs[0])
    assert done.status == "already_started" and len(engine.aborts) == 1
    job = await reload(maker, job_id)
    assert job.status == "cancelled" and "abort_pending" not in job.input_data
    async with maker() as db:
        assert await fe.find_stuck(db, timedelta(seconds=-1)) == []


async def test_cancel_of_a_never_started_engine_job_makes_no_engine_call(maker, factory, engine, wired):
    t, u, _, job_id = await make_job(maker)
    resp = await cancel(maker, u, t, job_id)
    assert resp.status == "cancelled" and engine.aborts == []
    assert "abort_pending" not in (await reload(maker, job_id)).input_data


async def test_cancel_treats_an_engine_404_as_settled(maker, factory, engine, wired, monkeypatch):
    t, u, job_id = await started(maker, factory, engine, wired)

    async def gone(*a, **k):
        raise EngineError(404, "not_found")

    monkeypatch.setattr(engine, "abort", gone)
    await cancel(maker, u, t, job_id)
    assert "abort_pending" not in (await reload(maker, job_id)).input_data
