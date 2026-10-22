"""Long-session driver: scripted user turns, then the watermark and probe bookkeeping Gate B needs.

Turns come from `evals.long_session.plan` (facts planted first, work turns that open generated web pages, chat and
recap turns, probe rounds). Each turn is submitted and awaited like the chat driver does. When a turn settles the
driver reads the watch snapshot's `context` (the Tier 1 watermark, `view.context`), which the trace route does not
carry. After the last turn it fetches the trace and adds `evalContext` to it (see `evals.graders.context`).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from evals import arms, long_session
from evals.graders.context import EVAL_CONTEXT_KEY
from evals.trace import fetch_trace

from . import AUTH, POLL_S, DriverContext, DriverError, DriverOutput, TrialTimeout
from .chat import BASE, create_conversation, settled, snapshot

LIVE_POLL_S = 1.5


async def wait_settled(ctx: DriverContext, conversation_id: str, request_id: str) -> dict[str, Any]:
    """The first snapshot in which the submission is placed and no run is active or queued."""
    poll = POLL_S if ctx.ew.mode == "fake" else LIVE_POLL_S
    while True:
        if ctx.remaining() <= 0:
            raise TrialTimeout("the long session", ctx.timeout_s)
        snap = await snapshot(ctx, conversation_id)
        if settled(snap, request_id):
            return snap
        await asyncio.sleep(poll)


def request_id(ctx: DriverContext, turn: long_session.Turn) -> str:
    return f"{ctx.task.id}-t{ctx.trial_index}-{turn.n - 1}"


async def run(ctx: DriverContext) -> DriverOutput:
    try:
        turns, probes = long_session.plan(ctx.task)
    except long_session.SpecError as e:
        raise DriverError("bad_task", str(e)) from None
    started = time.monotonic()
    conversation_id = await create_conversation(ctx)
    ctx.lap("setup", started)

    samples: list[dict[str, Any]] = []
    timed_out = False
    started = time.monotonic()
    try:
        for turn in turns:
            rid = request_id(ctx, turn)
            r = await ctx.http.post(
                f"{ctx.backend}{BASE}/{conversation_id}/submissions",
                headers={**AUTH, "Idempotency-Key": rid},
                json={"text": turn.text, "requestId": rid},
            )
            if r.status_code != 202:
                raise DriverError("submit_failed", f"turn {turn.n}: {r.status_code} {r.text[:300]}")
            snap = await wait_settled(ctx, conversation_id, rid)
            state = snap.get("context") or {}
            samples.append({
                "n": turn.n,
                "requestId": rid,
                "clearedThroughEntryId": state.get("clearedThroughEntryId"),
                "clearedTokens": int(state.get("clearedTokens") or 0),
            })
    except TrialTimeout:
        timed_out = True
        await ctx.http.post(f"{ctx.backend}{BASE}/{conversation_id}/abort", headers=AUTH, json={})
    ctx.lap("run", started)

    started = time.monotonic()
    raw = await fetch_trace(ctx.api, conversation_id, raw=True)
    by_turn = {t.n: t for t in turns}
    raw[EVAL_CONTEXT_KEY] = {
        "tiers": arms.tiers_of(ctx.task),
        "turns": len(turns),
        "noisy": bool(ctx.task.input.get("noisy")),
        # fake-mode usage is declared by the script, so context size is rebuilt from the entries instead
        "tokenSource": "estimate" if ctx.ew.mode == "fake" else "auto",
        "samples": samples,
        "probes": [
            {
                "id": p.id,
                "kind": p.kind,
                "turn": p.turn,
                "requestId": request_id(ctx, by_turn[p.turn]),
                "fact": p.fact,
                "question": p.question,
                "expect": list(p.expect),
            }
            for p in probes
            if p.turn <= len(samples)
        ],
    }
    ctx.lap("trace", started)
    note = None if len(samples) == len(turns) else f"stopped after {len(samples)} of {len(turns)} turns"
    return DriverOutput(
        conversation_id=conversation_id, raw=raw, phases=dict(ctx.phases), timed_out=timed_out, note=note
    )
