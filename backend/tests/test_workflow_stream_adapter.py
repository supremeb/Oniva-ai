"""Plan 003 U17: engine watch frames become the legacy workflow stream events (SQLite tier, scripted watch)."""
import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.models.workflow import WorkflowExecution
from app.services import workflow_stream_adapter as wsa
from tests.contracts.streams import ScriptedEngine, ScriptedWatch, run_view, snapshot_frames
from tests.internal.test_invoke import factory, maker  # noqa: F401  (fixtures)
from tests.test_workflow_engine_start import EXECUTE, client_for, make_app, w, write_trace  # noqa: F401


async def started(w, steps):  # noqa: F811
    """A running engine execution with ``steps`` stored as its trace; returns its id."""
    async with client_for(make_app(w)) as c:
        w.engine.task = {"status": "running"}
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
    execution_id = uuid.UUID(r.json()["execution_id"])
    await write_trace(w.maker, execution_id, [
        {"node_id": n, "status": st, "output": out, "error": err} for n, st, out, err in steps])
    return execution_id


async def collect(w, execution_id, engine, **kw):  # noqa: F811
    return [e async for e in wsa.watch_events(w.factory, execution_id, "task-1", engine=engine,
                                              reconnect_delay_s=0.0, poll_s=0.01, **kw)]


OK = [("s", "success", {"q": 1}, None), ("t", "success", {"output": "hi"}, None)]


async def test_a_scripted_view_sequence_gives_progress_node_complete_then_complete_in_order(w):  # noqa: F811
    eid = await started(w, OK)
    views = [run_view({"s": "pending", "t": "pending"}, running=("s",)),
             run_view({"s": "success", "t": "pending"}, running=("t",)),
             run_view({"s": "success", "t": "success"}),
             run_view({"s": "success", "t": "success"}, status="done")]
    events = await collect(w, eid, ScriptedEngine(ScriptedWatch(snapshot_frames(views))))
    assert [(e["type"], e.get("node_id")) for e in events] == [
        ("progress", "s"), ("node_complete", "s"), ("progress", "t"), ("node_complete", "t"), ("complete", None)]
    assert events[1]["result"] == {"status": "success", "output": {"q": 1}, "error": None}
    done = events[-1]
    assert done["status"] == "completed" and done["executed_nodes"] == 2 and done["execution_id"] == str(eid)
    assert done["results"]["t"]["output"] == {"output": "hi"}


async def test_patches_apply_to_the_snapshot(w):  # noqa: F811
    eid = await started(w, OK)
    frames = [{"cursor": 1, "snapshot": run_view({"s": "pending", "t": "pending"}, running=("s",))},
              {"cursor": 2, "patch": [{"op": "replace", "path": "/tasks/run-task-1/nodes/s", "value": "success"},
                                      {"op": "replace", "path": "/tasks/run-task-1/nodes/t", "value": "success"}]},
              {"cursor": 3, "patch": [{"op": "replace", "path": "/tasks/run-task-1/status", "value": "done"}]}]
    events = await collect(w, eid, ScriptedEngine(ScriptedWatch(frames)))
    assert [e["type"] for e in events] == ["progress", "node_complete", "progress", "node_complete", "complete"]


async def test_a_skipped_node_emits_node_skipped_and_a_failed_node_emits_error_with_its_id(w):  # noqa: F811
    eid = await started(w, [("s", "success", {}, None), ("t", "failed", None, "boom")])
    views = [run_view({"s": "success", "t": "failed", "o": "skipped"}),
             run_view({"s": "success", "t": "failed", "o": "skipped"}, status="done")]
    events = await collect(w, eid, ScriptedEngine(ScriptedWatch(snapshot_frames(views))))
    kinds = [(e["type"], e.get("node_id")) for e in events]
    assert ("node_skipped", "o") in kinds
    failed = kinds.index(("node_complete", "t"))
    assert kinds[failed + 1] == ("error", "t") and events[failed + 1]["error"] == "boom"
    assert events[failed]["result"]["status"] == "failed"
    assert events[-1]["type"] == "complete" and events[-1]["status"] == "failed"
    assert events[-1]["results"]["o"]["status"] == "skipped" and events[-1]["executed_nodes"] == 1


async def test_a_reconnect_resumes_with_the_last_cursor_and_no_duplicates_or_gap(w):  # noqa: F811
    eid = await started(w, OK)
    first = ScriptedWatch(snapshot_frames([run_view({"s": "success", "t": "pending"}, running=("t",))], 7),
                          fail=httpx.ReadError("dropped"))
    second = ScriptedWatch(snapshot_frames([run_view({"s": "success", "t": "success"}, status="done")], 8))
    engine = ScriptedEngine(first, second)
    events = await collect(w, eid, engine)
    assert engine.resumes == [None, "7"]
    assert [(e["type"], e.get("node_id")) for e in events] == [
        ("progress", "s"), ("node_complete", "s"), ("progress", "t"), ("node_complete", "t"), ("complete", None)]
    assert first.closed and second.closed


async def test_a_client_disconnect_closes_the_engine_stream(w):  # noqa: F811
    eid = await started(w, OK)
    watch = ScriptedWatch(snapshot_frames([run_view({"s": "pending"}, running=("s",))]))
    gen = wsa.watch_events(w.factory, eid, "task-1", engine=ScriptedEngine(watch))
    assert (await gen.__anext__())["type"] == "progress"
    await gen.aclose()
    assert watch.closed


async def test_an_unreachable_watch_falls_back_to_the_run_task_and_the_stored_trace(w):  # noqa: F811
    eid = await started(w, OK)
    w.engine.task = {"status": "terminal", "outcome": {"type": "done", "value": {"status": "completed"}}}
    w.engine.down = True  # the watch cannot be opened; the run task is still readable
    events = await collect(w, eid, w.engine)
    assert [e["type"] for e in events] == ["progress", "node_complete", "progress", "node_complete", "complete"]
    assert events[-1]["status"] == "completed"


async def test_a_run_still_going_after_the_timeout_raises(w):  # noqa: F811
    eid = await started(w, OK)
    watch = ScriptedWatch(snapshot_frames([run_view({"s": "pending"}, running=("s",))]))
    with pytest.raises(asyncio.TimeoutError):
        await collect(w, eid, ScriptedEngine(watch), timeout_s=0.1)


async def test_execute_stream_on_the_engine_starts_one_run_and_streams_legacy_events(w):  # noqa: F811
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute-stream", json={**EXECUTE, "workflow_id": str(w.wf)})
    assert r.status_code == 200
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    async with w.maker() as db:
        [row] = (await db.execute(select(WorkflowExecution))).scalars().all()
    # The first event names the execution, so a client can follow the run before it completes.
    assert events[0] == {"type": "started", "execution_id": str(row.id)}
    assert [e["type"] for e in events][1] == "progress" and events[-1]["type"] == "complete"
    assert [e["node_id"] for e in events if e["type"] == "node_complete"] == ["s", "t", "o"]
    assert len(w.engine.starts) == 1
    async with w.maker() as db:
        [row] = (await db.execute(select(WorkflowExecution))).scalars().all()
    assert row.trigger == "manual"


async def test_execute_stream_engine_outage_is_a_502_before_the_stream_opens(w):  # noqa: F811
    w.engine.down = True
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute-stream", json={**EXECUTE, "workflow_id": str(w.wf)})
    assert r.status_code == 502


async def test_logs_stream_serves_a_running_engine_execution(w):  # noqa: F811
    from app.api.v1 import workflows as wf_api
    from types import SimpleNamespace

    eid = await started(w, OK)
    async with w.maker() as db:
        resp = await wf_api.stream_workflow_logs(
            w.wf, db=db, user=SimpleNamespace(id=str(w.user), role="member"), tenant_id=w.tenant)
        first = await resp.body_iterator.__anext__()
        await resp.body_iterator.aclose()
    data = json.loads(first.split("data: ", 1)[1])
    assert data["id"] == str(eid) and data["status"] == "running"
