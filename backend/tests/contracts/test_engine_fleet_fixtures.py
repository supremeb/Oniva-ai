"""Engine-path Fleet fixtures (plan 2026-10-17-003, U9) and the guard that every legacy one has a counterpart.

``fixtures/fleet_engine_<scenario>.json`` holds what ``GET /background-jobs/{id}/stream`` puts on the wire for an
engine-run job (live steps from the engine watch, then the terminal event written from the ``on_finished`` hook)
plus the settled row, next to the legacy ``fleet_<scenario>.json``.

    ONIVA_RECORD_CONTRACTS=1 uv run pytest tests/contracts/test_engine_fleet_fixtures.py   # re-record
"""
import pytest

from tests.contracts.recorder import check_or_record
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)
from tests.test_fleet_cancel_engine import cancel, started
from tests.test_fleet_engine_start import wired  # noqa: F401
from tests.test_fleet_run_sync import push, ref_for, view_for
from tests.test_fleet_stream_engine import (  # noqa: F401
    Upstream, _no_delivery, engine, _wiring, db_for_stream, finish_patch, mid_view, snapshot_frame, stream)
from app.models.background_job import BackgroundJob
from app.services import fleet_run_sync as frs
from tests.contracts.test_engine_deep_work_fixtures import FIXTURES

SCENARIOS = ["completed", "failed", "cancelled"]


def legacy_fleet_fixtures() -> list[str]:
    return sorted(p.stem.removeprefix("fleet_") for p in FIXTURES.glob("fleet_*.json")
                  if not p.stem.startswith("fleet_engine_"))


def test_every_legacy_fleet_fixture_has_an_engine_counterpart():
    missing = [s for s in legacy_fleet_fixtures() if not (FIXTURES / f"fleet_engine_{s}.json").exists()]
    assert not missing, f"legacy fleet fixtures without a fleet_engine_* counterpart: {missing}"
    assert set(legacy_fleet_fixtures()) <= set(SCENARIOS)  # drive new scenarios here


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_fleet_engine_stream_contract(scenario, maker, factory, engine, wired, db_for_stream):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    if scenario == "completed":
        frames = [snapshot_frame(mid_view(job_id)), finish_patch(None)]

        async def settle():
            await frs.on_finished(ref, push(job_id, view=view_for(job_id)))
    elif scenario == "failed":
        failed = view_for(job_id, error={"kind": "max_rounds", "message": "max_rounds"})
        frames = [snapshot_frame(mid_view(job_id))]

        async def settle():
            await frs.on_finished(ref, push(job_id, "unanswered", "max_rounds", view=failed))
    else:
        frames = [snapshot_frame(mid_view(job_id))]

        async def settle():
            await cancel(maker, u, t, job_id, "stop")
            await frs.on_finished(ref, push(job_id, "unanswered", "aborted", view=view_for(job_id)))

    up = Upstream(frames, after=settle)

    async def watch_stream(*a, **k):
        return up

    engine.watch_stream = watch_stream
    events = await stream(maker, t, u, job_id)
    async with maker() as db:
        job = await db.get(BackgroundJob, job_id)
        final = {"status": job.status, "progress": job.progress, "result": job.result, "error": job.error,
                 "total_tokens": job.total_tokens}
    check_or_record(f"fleet_engine_{scenario}", {"events": events, "final_job": final})
