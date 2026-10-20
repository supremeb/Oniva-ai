"""Plan 003 U5: the run reconciler (SQLite tier; the advisory lock is in tests/postgres)."""
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.services import run_handlers as rh
from app.services import run_reconciler as rr
from app.services import automation_scope as scope
from app.services.engine_client import EngineError
from tests.internal.test_automation_scope import seed
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)

SETTINGS = SimpleNamespace(RUN_RECONCILE_AFTER_S=120, RUN_RECONCILE_INTERVAL_S=60.0,
                           RUN_START_RETRY_MAX=5, RUN_START_RETRY_BASE_S=30.0)

IDLE_VIEW = {"run": {"active": False}, "inbox": {"queue": []}, "tasks": {}, "entries": []}


class FakeEngine:
    def __init__(self):
        self.directory = SimpleNamespace(default_session_id="s1")
        self.tasks = {}
        self.task_calls = 0

    async def get_task(self, session_id, tenant, project, conversation, task_id):
        self.task_calls += 1
        if task_id not in self.tasks:
            raise EngineError(404, "not_found")
        return self.tasks[task_id]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _registry():
    rh.reset()
    yield
    rh.reset()


@pytest.fixture
def engine(monkeypatch):
    eng = FakeEngine()
    views = {}  # conversation id -> view dict, or an exception to raise
    eng.views = views

    async def read_snapshot(_engine, session_id, tenant, project, conversation):
        v = views.get(conversation)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise EngineError(404, "not_found")
        return v

    monkeypatch.setattr(rr, "read_snapshot", read_snapshot)
    return eng


def reconciler(factory, engine, clock=None, **settings):  # noqa: F811
    s = SimpleNamespace(**{**SETTINGS.__dict__, **settings})
    return rr.RunReconciler(factory, s, engine_factory=lambda: engine, clock=clock or Clock())


class Recorder:
    """A fake handler for one kind that records calls and serves a scripted stuck list."""

    def __init__(self, refs, start_results=None):
        self.refs = refs
        self.older_than = []
        self.finished = []
        self.started = []
        self.failed = []
        self.start_results = list(start_results or [])

    async def find_stuck(self, session, older_than):
        self.older_than.append(older_than)
        return list(self.refs)

    async def on_finished(self, ref, snapshot):
        self.finished.append((ref, snapshot))

    async def start_missing(self, ref):
        self.started.append(ref)
        return self.start_results.pop(0) if self.start_results else rh.StartResult.failed("down")

    async def fail(self, ref, code):
        self.failed.append((ref, code))

    def register(self, kind="fleet", *, start=True):
        rh.register(kind, find_stuck=self.find_stuck, on_finished=self.on_finished, fail=self.fail,
                    **({"start_missing": self.start_missing} if start else {}))


async def bind(maker, factory, kind="fleet"):  # noqa: F811
    (t,), (u,) = await seed(maker)
    ref_id = uuid.uuid4()
    cid, project, _ = await scope.bind_run_conversation(factory, kind, ref_id, t, u)
    return rh.RunRef(kind=kind, ref_id=ref_id), cid


async def test_settles_a_row_whose_finished_post_was_lost(maker, factory, engine):
    ref, cid = await bind(maker, factory)
    rec = Recorder([ref])
    rec.register()
    engine.views[cid] = IDLE_VIEW
    counts = await reconciler(factory, engine).run_once()
    assert counts["finished"] == 1
    [(got, snap)] = rec.finished
    assert got.ref_id == ref.ref_id and got.conversation_id == cid and got.tenant_id is not None
    assert snap["source"] == "reconcile" and snap["view"] is IDLE_VIEW
    assert rec.older_than == [timedelta(seconds=120)]


async def test_a_run_the_engine_is_still_working_on_is_left_alone(maker, factory, engine):
    ref, cid = await bind(maker, factory)
    rec = Recorder([ref])
    rec.register()
    for active in ({"run": {"active": True}}, {"inbox": {"queue": [{}]}},
                   {"tasks": {"t": {"status": "waiting"}}}):
        engine.views[cid] = {**IDLE_VIEW, **active}
        await reconciler(factory, engine).run_once()
    assert rec.finished == [] and rec.failed == [] and rec.started == []


async def test_reads_the_root_task_when_the_row_recorded_it(maker, factory, engine):
    ref, cid = await bind(maker, factory)
    rec = Recorder([ref.with_binding(task_id="task-1")])
    rec.register()
    engine.views[cid] = IDLE_VIEW
    engine.tasks["task-1"] = {"id": "task-1", "status": "terminal", "outcome": {"type": "faulted"}}
    await reconciler(factory, engine).run_once()
    [(_, snap)] = rec.finished
    assert snap["runStatus"] == "failed" and snap["task"]["id"] == "task-1"


async def test_fails_a_row_with_no_engine_conversation_and_no_start_hook(maker, factory, engine):
    ref, _ = await bind(maker, factory)
    rec = Recorder([ref])
    rec.register(start=False)
    # No view registered for the conversation: the engine answers 404.
    await reconciler(factory, engine).run_once()
    assert [(r.ref_id, c) for r, c in rec.failed] == [(ref.ref_id, "engine_run_missing")]


async def test_fails_a_row_with_no_binding_at_all(maker, factory, engine):
    rec = Recorder([rh.RunRef(kind="fleet", ref_id=uuid.uuid4())])
    rec.register(start=False)
    await reconciler(factory, engine).run_once()
    assert [c for _, c in rec.failed] == ["engine_run_missing"]


async def test_an_unreachable_engine_changes_nothing(maker, factory, engine):
    ref, cid = await bind(maker, factory)
    rec = Recorder([ref])
    rec.register()
    engine.views[cid] = EngineError(502, "engine_unreachable")
    await reconciler(factory, engine).run_once()
    assert rec.finished == rec.failed == rec.started == []


async def test_start_missing_is_called_for_a_row_with_no_engine_run_and_started_stops_it(maker, factory, engine):
    ref, _ = await bind(maker, factory)
    rec = Recorder([ref], [rh.StartResult.started("task-9")])
    rec.register()
    rc = reconciler(factory, engine)
    await rc.run_once()
    assert len(rec.started) == 1 and rec.failed == []
    # The engine run now exists: the next pass reads it instead of starting again.
    engine.views[rec.started[0].conversation_id] = {**IDLE_VIEW, "run": {"active": True}}
    await rc.run_once()
    assert len(rec.started) == 1


async def test_already_started_also_stops_further_start_calls(maker, factory, engine):
    ref, cid = await bind(maker, factory)
    rec = Recorder([ref], [rh.StartResult.already_started("t")])
    rec.register()
    rc = reconciler(factory, engine)
    await rc.run_once()
    engine.views[cid] = {**IDLE_VIEW, "run": {"active": True}}
    await rc.run_once()
    assert len(rec.started) == 1


async def test_failed_start_retries_with_backoff_and_stops_at_the_bound(maker, factory, engine):
    ref, _ = await bind(maker, factory)
    rec = Recorder([ref], [rh.StartResult.failed("down")] * 10)
    rec.register()
    clock = Clock()
    rc = reconciler(factory, engine, clock)
    await rc.run_once()
    assert len(rec.started) == 1
    # Inside the 30 s backoff nothing is retried.
    clock.t += 29
    await rc.run_once()
    assert len(rec.started) == 1
    # Delays double: 30, 60, 120, then the fifth failure hits the bound.
    waits = []
    for expected in (2, 3, 4, 5):
        for _ in range(400):
            clock.t += 15
            waits.append(15)
            await rc.run_once()
            if len(rec.started) == expected:
                break
        else:
            pytest.fail(f"attempt {expected} never ran")
    assert len(rec.started) == 5
    assert [c for _, c in rec.failed] == ["engine_start_failed"]
    # After fail() the reconciler does not call fail again for the same row on later passes only if
    # the handler moved the row; here the row is still listed, so the counter restarts cleanly.
    clock.t += 10_000
    await rc.run_once()
    assert len(rec.started) == 6 and len(rec.failed) == 1


async def test_the_handlers_persisted_attempt_counter_counts_toward_the_bound(maker, factory, engine):
    ref, _ = await bind(maker, factory)
    rec = Recorder([ref], [rh.StartResult.failed("down", attempts=5)])
    rec.register()
    await reconciler(factory, engine).run_once()
    assert [c for _, c in rec.failed] == ["engine_start_failed"]


async def test_a_hook_that_raises_does_not_stop_the_pass(maker, factory, engine):
    ref, cid = await bind(maker, factory)
    other, cid2 = await bind(maker, factory)
    rec = Recorder([ref, other])
    rec.register()
    engine.views[cid] = IDLE_VIEW
    engine.views[cid2] = IDLE_VIEW
    calls = []

    async def boom(r, snap):
        calls.append(r.ref_id)
        if len(calls) == 1:
            raise RuntimeError("boom")

    rh._registry["fleet"]["on_finished"] = boom
    counts = await reconciler(factory, engine).run_once()
    assert len(calls) == 2 and counts["finished"] == 1


async def test_a_kind_with_no_find_stuck_is_skipped(maker, factory, engine):
    rh.register("batch", on_finished=Recorder([]).on_finished)
    assert (await reconciler(factory, engine).run_once())["checked"] == 0


async def test_a_find_stuck_that_raises_does_not_stop_other_kinds(maker, factory, engine):
    async def bad(session, older_than):
        raise RuntimeError("db")

    rh.register("batch", find_stuck=bad, on_finished=Recorder([]).on_finished)
    ref, cid = await bind(maker, factory)
    rec = Recorder([ref])
    rec.register("fleet")
    engine.views[cid] = IDLE_VIEW
    assert (await reconciler(factory, engine).run_once())["finished"] == 1


async def test_start_and_stop_run_the_loop(maker, factory, engine):
    ref, cid = await bind(maker, factory)
    rec = Recorder([ref])
    rec.register()
    engine.views[cid] = IDLE_VIEW
    rc = reconciler(factory, engine, RUN_RECONCILE_INTERVAL_S=0.01)
    rc.start()
    for _ in range(200):
        if rec.finished:
            break
        import asyncio
        await asyncio.sleep(0.01)
    await rc.stop()
    assert rec.finished
