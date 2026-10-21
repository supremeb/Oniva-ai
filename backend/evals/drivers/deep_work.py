"""Deep Work driver: intake, plan, execute through `/api/v1/deep-work`, as `test_deep_work_engine_recovery.py` does.

The session is opened by the intake call (`/engine/clarify`), which is the first submission of the run's
conversation. The plan comes from `input.plan` when the task has one (no planning model call, in either mode);
otherwise `/engine/plan` asks the model. The run is then started and polled until its row is terminal.

Files for graders: `report.md` (the final report, downloaded from the run's workspace), `learnings` (saved
learnings in the order production numbers sources) and `fixture_urls` (URLs present in the replay fixtures).
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

from app.services.deep_work.engine_tools import _all_learnings
from evals.replay import fixture_urls
from evals.trace import fetch_trace
from tests.e2e.harness import sql

from . import AUTH, POLL_S, DriverContext, DriverError, DriverOutput, TrialTimeout

BASE = "/api/v1/deep-work"
TERMINAL = ("completed", "failed", "cancelled")


async def call(ctx: DriverContext, method: str, path: str, **kw: Any) -> Any:
    r = await ctx.http.request(method, f"{ctx.backend}{BASE}{path}", headers=AUTH, timeout=120, **kw)
    return r


async def open_session(ctx: DriverContext, query: str) -> tuple[str, str]:
    """Intake. Returns (session_id, conversation_id); a clarification request is a driver error."""
    r = await call(ctx, "POST", "/engine/clarify", json={"query": query, "project_id": ctx.project_id})
    if r.status_code != 200:
        raise DriverError("clarify_failed", f"{r.status_code} {r.text[:300]}")
    body = r.json()
    if body.get("action") != "proceed":
        raise DriverError("needs_clarification", "the intake asked a question; make the task query self-contained")
    return body["session_id"], body["conversation_id"]


async def make_plan(ctx: DriverContext, query: str, session_id: str) -> dict[str, Any]:
    plan = ctx.task.input.get("plan")
    if isinstance(plan, dict):
        return plan
    r = await call(
        ctx,
        "POST",
        "/engine/plan",
        json={"query": query, "project_id": ctx.project_id, "session_id": session_id, "enriched_query": query},
    )
    if r.status_code != 200:
        raise DriverError("plan_failed", f"{r.status_code} {r.text[:300]}")
    return r.json()["raw_plan"]


async def start(ctx: DriverContext, query: str, session_id: str, plan: dict[str, Any]) -> None:
    r = await call(
        ctx,
        "POST",
        "/engine",
        json={"query": query, "project_id": ctx.project_id, "session_id": session_id, "plan": plan, "enriched_query": query},
    )
    if r.status_code != 200 or r.json().get("status") != "started":
        raise DriverError("start_failed", f"{r.status_code} {r.text[:300]}")


async def wait_terminal(ctx: DriverContext, conversation_id: str) -> str:
    while True:
        if ctx.remaining() <= 0:
            raise TrialTimeout("the Deep Work run", ctx.timeout_s)
        r = await call(ctx, "GET", f"/by-conversation/{conversation_id}/full-state")
        if r.status_code == 200 and r.json().get("status") in TERMINAL:
            return r.json()["status"]
        await asyncio.sleep(POLL_S)


def saved_learnings(ctx: DriverContext, session_id: str) -> list[dict[str, Any]]:
    """The run's learnings, numbered as `_all_learnings` (and so the Sources section) orders them."""
    rows = sql(ctx.ew.world, "SELECT rounds FROM deep_work_runs WHERE id = ?", session_id.replace("-", ""))
    rounds = rows[0][0] if rows else []
    if isinstance(rounds, str):
        rounds = json.loads(rounds)
    return [l.model_dump(exclude_none=True) for l in _all_learnings(SimpleNamespace(rounds=rounds or []))]


async def read_report(ctx: DriverContext, session_id: str) -> str | None:
    r = await call(ctx, "GET", f"/{session_id}/workspace/report.md")
    return r.text if r.status_code == 200 else None


async def run(ctx: DriverContext) -> DriverOutput:
    query = ctx.task.input.get("query")
    if not isinstance(query, str) or len(query) < 5:
        raise DriverError("bad_task", "deep_work tasks need input.query")
    started = time.monotonic()
    session_id, conversation_id = await open_session(ctx, query)
    plan = await make_plan(ctx, query, session_id)
    ctx.lap("setup", started)

    started = time.monotonic()
    await start(ctx, query, session_id, plan)
    timed_out, status = False, None
    try:
        status = await wait_terminal(ctx, conversation_id)
    except TrialTimeout:
        timed_out = True
        await call(ctx, "POST", f"/engine/{session_id}/cancel")
    ctx.lap("run", started)

    started = time.monotonic()
    raw = await fetch_trace(ctx.api, conversation_id, raw=True)
    files: dict[str, Any] = {"fixture_urls": fixture_urls(ctx.ew.replay_dir), "learnings": saved_learnings(ctx, session_id)}
    if (report := await read_report(ctx, session_id)) is not None:
        files["report.md"] = report
    ctx.lap("trace", started)
    return DriverOutput(
        conversation_id=conversation_id,
        raw=raw,
        files=files,
        phases=dict(ctx.phases),
        timed_out=timed_out,
        note=f"deep_work status {status}" if status else None,
    )
