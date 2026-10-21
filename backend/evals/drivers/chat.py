"""Chat driver: create a conversation, submit each user turn, wait for the run to settle, fetch the trace.

Task input: `turns` (a list of strings, or `{text}` mappings; a single `query` is one turn) and `selections` (what
the composer would send: `skill_ids`, `web_search`, `knowledge_base_ids`, ...).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from evals.trace import fetch_trace

from . import AUTH, POLL_S, DriverContext, DriverError, DriverOutput, TrialTimeout

BASE = "/api/v1/engine/conversations"


def turns_of(task_input: dict[str, Any]) -> list[str]:
    raw = task_input.get("turns")
    if raw is None and task_input.get("query"):
        raw = [task_input["query"]]
    if not isinstance(raw, list) or not raw:
        raise DriverError("bad_task", "chat tasks need input.turns (or input.query)")
    out = []
    for t in raw:
        text = t.get("text") if isinstance(t, dict) else t
        if not isinstance(text, str) or not text.strip():
            raise DriverError("bad_task", "every turn needs text")
        out.append(text)
    return out


async def snapshot(ctx: DriverContext, conversation_id: str) -> dict[str, Any]:
    """The first snapshot frame of a fresh watch: what a browser loading the conversation sees."""
    url = f"{ctx.backend}{BASE}/{conversation_id}/watch"
    async with ctx.http.stream("GET", url, headers=AUTH, timeout=30) as r:
        if r.status_code != 200:
            raise DriverError("watch_failed", f"watch answered {r.status_code}")
        event, buf = None, []
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                buf.append(line[5:].lstrip())
            elif not line.strip():
                if event == "snapshot" and buf:
                    body = json.loads("\n".join(buf))
                    return body.get("snapshot", body)
                event, buf = None, []
    raise DriverError("watch_failed", "the watch stream ended without a snapshot")


def settled(snap: dict[str, Any], request_id: str) -> bool:
    """The submission's user entry is in and no run is active or queued."""
    placed = any(
        e.get("kind") == "user" and (e.get("payload") or {}).get("requestId") == request_id
        for e in snap.get("entries") or []
    )
    run_active = bool((snap.get("run") or {}).get("active"))
    queued = bool((snap.get("inbox") or {}).get("queue"))
    return placed and not run_active and not queued


async def wait_settled(ctx: DriverContext, conversation_id: str, request_id: str) -> None:
    while True:
        if ctx.remaining() <= 0:
            raise TrialTimeout("the chat run", ctx.timeout_s)
        if settled(await snapshot(ctx, conversation_id), request_id):
            return
        await asyncio.sleep(POLL_S)


async def create_conversation(ctx: DriverContext) -> str:
    selections = ctx.task.input.get("selections") or {}
    r = await ctx.http.post(
        f"{ctx.backend}{BASE}", headers=AUTH, json={"project_id": ctx.project_id, "selections": selections}
    )
    if r.status_code != 201:
        raise DriverError("create_failed", f"{r.status_code} {r.text[:300]}")
    return r.json()["conversationId"]


async def run(ctx: DriverContext) -> DriverOutput:
    turns = turns_of(ctx.task.input)
    started = time.monotonic()
    conversation_id = await create_conversation(ctx)
    ctx.lap("setup", started)

    timed_out = False
    started = time.monotonic()
    try:
        for i, text in enumerate(turns):
            request_id = f"{ctx.task.id}-t{ctx.trial_index}-{i}"
            r = await ctx.http.post(
                f"{ctx.backend}{BASE}/{conversation_id}/submissions",
                headers={**AUTH, "Idempotency-Key": request_id},
                json={"text": text, "requestId": request_id},
            )
            if r.status_code != 202:
                raise DriverError("submit_failed", f"{r.status_code} {r.text[:300]}")
            await wait_settled(ctx, conversation_id, request_id)
    except TrialTimeout:
        timed_out = True
        await ctx.http.post(f"{ctx.backend}{BASE}/{conversation_id}/abort", headers=AUTH, json={})
    ctx.lap("run", started)

    started = time.monotonic()
    raw = await fetch_trace(ctx.api, conversation_id, raw=True)
    ctx.lap("trace", started)
    return DriverOutput(conversation_id=conversation_id, raw=raw, phases=dict(ctx.phases), timed_out=timed_out)
