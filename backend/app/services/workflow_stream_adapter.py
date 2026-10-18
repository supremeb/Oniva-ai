"""Engine watch frames to the legacy workflow stream events (plan 003, U17).

``watch_events`` follows the engine watch of a workflow run's container conversation, diffs the run task's
``view.tasks`` entry (its ``nodes`` map and the ``workflow_node`` children) and yields the orchestrator-shaped
events the current UI and the agent-chat/compliance streams already consume:

* ``progress`` when a node starts, ``node_complete`` when it settles (the result comes from the stored trace),
* ``node_skipped`` for a node that never ran, and ``error`` (with the node id) after a failed node's ``node_complete``,
* ``complete`` once the run task is terminal.

It keeps no state outside the generator, so any pod can serve a stream: the engine watch sends a snapshot first
and the adapter derives everything from the view. A dropped watch reconnects with the last cursor
(``Last-Event-ID``); nodes already reported are remembered, so a resume has no duplicates and no gap. A watch that
cannot be reopened falls back to polling the run task and the stored trace. Closing the generator (client
disconnect) closes the engine stream.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import httpx

from app.models.workflow import WorkflowExecution
from app.services import workflow_engine_runs as runs
from app.services.deep_work import engine_view
from app.services.engine_client import EngineError, get_engine_client

logger = logging.getLogger(__name__)

RUN_KIND = "workflow_run"
NODE_KIND = "workflow_node"
#: Reopen a dropped watch this many times before polling instead.
MAX_RECONNECTS = 3
RECONNECT_DELAY_S = 0.2
_TERMINAL_TASK = ("done", "aborted", "faulted")
_LIVE_NODE = ("pending", "running", "waiting")
_SKIPPED = ("skipped", "blocked")


# ---- reading the view ---------------------------------------------------------------------------


def run_task(view: Dict[str, Any]) -> Optional[tuple]:
    """``(task_id, task)`` of the root ``workflow_run`` task in ``view.tasks``, if it is there yet."""
    for task_id, task in (view.get("tasks") or {}).items():
        if (task or {}).get("kind") == RUN_KIND and not (task or {}).get("parentId"):
            return task_id, task
    return None


def node_states(view: Dict[str, Any]) -> Dict[str, str]:
    """``{node_id: pending|running|success|failed|skipped|blocked}`` for the run's own nodes, in dispatch order."""
    found = run_task(view)
    if found is None:
        return {}
    run_id, run = found
    states: Dict[str, str] = {}
    children = sorted(
        ((tid, t) for tid, t in (view.get("tasks") or {}).items()
         if (t or {}).get("kind") == NODE_KIND and t.get("parentId") == run_id and t.get("label")),
        key=lambda item: (item[1].get("startedAt") or "", item[0]))
    for _tid, child in children:
        if child.get("status") in _LIVE_NODE:
            states[child["label"]] = "running"
    for node_id, status in (run.get("nodes") or {}).items():
        if status in ("success", "failed", "skipped", "blocked") or node_id not in states:
            states[node_id] = status
    return states


def run_ended(view: Dict[str, Any]) -> Optional[str]:
    """The run task's terminal status (``done``/``aborted``/``faulted``), else ``None``."""
    found = run_task(view)
    status = found[1].get("status") if found else None
    return status if status in _TERMINAL_TASK else None


# ---- the stream ---------------------------------------------------------------------------------


class _Seen:
    """What the stream has already reported."""

    def __init__(self) -> None:
        self.started: set = set()
        self.settled: set = set()


def _result(step: Optional[dict], status: str) -> dict:
    step = step or {}
    return {"status": step.get("status") or status, "output": step.get("output"), "error": step.get("error")}


async def _load(factory: Callable, execution_id: uuid.UUID) -> Optional[dict]:
    """Trace, totals and status of the execution, read after the view (every step is stored before a node settles)."""
    async with factory() as db:
        execution = await db.get(WorkflowExecution, execution_id)
        if execution is None:
            return None
        await db.refresh(execution)
        bound = await runs._binding(db, execution)
        return {"steps": [dict(s) for s in execution.execution_trace or []],
                "total": execution.total_nodes or 0, "status": execution.status,
                "tenant_id": execution.tenant_id, "conversation_id": execution.engine_conversation_id,
                "binding": bound}


def _last_step(steps: List[dict], node_id: str) -> Optional[dict]:
    found = None
    for step in steps:
        if step.get("node_id") == node_id:
            found = step
    return found


async def _node_events(states: Dict[str, str], seen: _Seen, factory: Callable, execution_id: uuid.UUID) -> List[dict]:
    """Events for the node transitions in ``states`` not reported yet; settled nodes in dispatch order."""
    fresh = [(n, s) for n, s in states.items()
             if (s == "running" and n not in seen.started) or (s not in _LIVE_NODE and n not in seen.settled)]
    if not fresh:
        return []
    fresh.sort(key=lambda item: item[1] == "running")  # settled nodes first (stable: dispatch order within each group)
    loaded = await _load(factory, execution_id)
    steps = (loaded or {}).get("steps", [])
    events: List[dict] = []
    for node_id, status in fresh:
        if status == "running":
            seen.started.add(node_id)
            events.append({"type": "progress", "node_id": node_id, "status": "running"})
        elif status in _SKIPPED:
            seen.started.add(node_id)
            seen.settled.add(node_id)
            events.append({"type": "node_skipped", "node_id": node_id, "status": "skipped"})
        else:
            if node_id not in seen.started:
                seen.started.add(node_id)
                events.append({"type": "progress", "node_id": node_id, "status": "running"})
            seen.settled.add(node_id)
            result = _result(_last_step(steps, node_id), status)
            if status == "failed":
                result["status"] = "failed"
            events.append({"type": "node_complete", "node_id": node_id, "result": result})
            if status == "failed":
                events.append({"type": "error", "node_id": node_id,
                               "error": result.get("error") or f"Node {node_id} failed"})
    return events


def _final_status(ended: str, row_status: Optional[str], states: Dict[str, str]) -> str:
    if row_status in runs.TERMINAL:
        return row_status
    if ended == "aborted":
        return "cancelled"
    if ended == "faulted" or any(s == "failed" for s in states.values()):
        return "failed"
    return "completed"


async def _complete_event(ended: str, states: Dict[str, str], factory: Callable, execution_id: uuid.UUID) -> dict:
    loaded = await _load(factory, execution_id) or {"steps": [], "total": 0, "status": None}
    results: Dict[str, Any] = {}
    for step in loaded["steps"]:
        results[step["node_id"]] = _result(step, "success")
    for node_id, status in states.items():
        if status in _SKIPPED:
            results.setdefault(node_id, {"status": "skipped", "output": None, "error": None})
    return {"type": "complete", "status": _final_status(ended, loaded["status"], states), "results": results,
            "executed_nodes": runs.executed_count(results), "total_nodes": loaded["total"],
            "execution_id": str(execution_id)}


async def _frames(upstream: Any, deadline: float) -> AsyncIterator[dict]:
    """Complete JSON frames from an open watch response; raises ``asyncio.TimeoutError`` past the deadline."""
    buffer = ""
    chunks = upstream.aiter_raw().__aiter__()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        try:
            raw = await asyncio.wait_for(chunks.__anext__(), remaining)
        except StopAsyncIteration:
            return
        buffer += raw.decode("utf-8", "replace").replace("\r\n", "\n")
        frames, buffer = engine_view.parse_frames(buffer)
        for frame in frames:
            yield frame


async def watch_events(
    factory: Callable, execution_id: Any, task_id: Optional[str] = None, *, engine: Any = None,
    timeout_s: float = 300.0, poll_s: Optional[float] = None, reconnect_delay_s: Optional[float] = None,
    announce: bool = False,
) -> AsyncIterator[dict]:
    """Orchestrator-shaped events for the engine run of ``execution_id``, from its container conversation's watch.

    ``announce`` makes the first event ``{"type": "started", "execution_id": ...}``, so a client can follow the run
    (waiting nodes, a late status read) before it completes.

    Raises ``asyncio.TimeoutError`` when the run is still going after ``timeout_s``.
    """
    execution_id = execution_id if isinstance(execution_id, uuid.UUID) else uuid.UUID(str(execution_id))
    if announce:
        yield {"type": "started", "execution_id": str(execution_id)}
    engine = engine or get_engine_client()
    deadline = time.monotonic() + timeout_s
    delay = RECONNECT_DELAY_S if reconnect_delay_s is None else reconnect_delay_s
    loaded = await _load(factory, execution_id)
    if loaded is None or loaded["binding"] is None:
        return
    session_id, project_id = loaded["binding"]
    tenant_id, conversation_id = loaded["tenant_id"], loaded["conversation_id"]
    seen = _Seen()
    view: Optional[dict] = None
    cursor: Optional[str] = None
    failures = 0
    while failures <= MAX_RECONNECTS:
        upstream = None
        try:
            upstream = await engine.watch_stream(session_id, tenant_id, project_id, conversation_id,
                                                 last_event_id=cursor)
            async for frame in _frames(upstream, deadline):
                if "cursor" in frame:
                    cursor = str(frame["cursor"])
                if "snapshot" in frame:
                    view = frame["snapshot"]
                elif view is not None and "patch" in frame:
                    view = engine_view.apply_patch(view, frame["patch"])
                else:
                    continue
                failures = 0
                states = node_states(view)
                for event in await _node_events(states, seen, factory, execution_id):
                    yield event
                ended = run_ended(view)
                if ended is not None:
                    yield await _complete_event(ended, states, factory, execution_id)
                    return
        except (EngineError, OSError, httpx.HTTPError) as exc:
            logger.warning("workflow %s: engine watch dropped (%s)", execution_id, exc)
        finally:
            if upstream is not None:
                await upstream.aclose()
        failures += 1
        if time.monotonic() >= deadline:
            raise asyncio.TimeoutError()
        await asyncio.sleep(delay)
    # The watch cannot be kept: follow the run task and the stored trace instead.
    async for event in _poll_events(factory, execution_id, task_id, seen, engine=engine,
                                    timeout_s=max(deadline - time.monotonic(), 0.0), poll_s=poll_s):
        yield event


async def _poll_events(factory: Callable, execution_id: uuid.UUID, task_id: Optional[str], seen: _Seen, *,
                       engine: Any, timeout_s: float, poll_s: Optional[float]) -> AsyncIterator[dict]:
    """Outage fallback: the run task's state and the stored trace, polled until the task is terminal."""
    deadline = time.monotonic() + timeout_s
    interval = runs.POLL_INTERVAL_S if poll_s is None else poll_s
    while True:
        async with factory() as db:
            execution = await db.get(WorkflowExecution, execution_id)
            if execution is None:
                return
            outcome = await runs.await_finished(db, execution, task_id, engine=engine, wait_s=0, poll_s=0)
        loaded = await _load(factory, execution_id) or {"steps": []}
        states: Dict[str, str] = {}
        for step in loaded["steps"]:
            states[step["node_id"]] = step.get("status") if step.get("status") in ("failed", "skipped") else "success"
        for event in await _node_events(states, seen, factory, execution_id):
            yield event
        if outcome is not None:
            ended = {"aborted": "aborted", "faulted": "faulted"}.get(outcome.get("type"), "done")
            event = await _complete_event(ended, states, factory, execution_id)
            event["status"] = runs.outcome_status(outcome)
            yield event
            return
        if time.monotonic() >= deadline:
            raise asyncio.TimeoutError()
        await asyncio.sleep(interval)
