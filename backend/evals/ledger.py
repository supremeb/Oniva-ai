"""Dollar cost of a trial, from the backend's usage ledger.

The engine's usage documents carry tokens but no dollars: the worker has no pricing table (`costUsd` is null), and
cost is computed by the backend when the worker reports each request (`/internal` usage, `app/config/llm_pricing`).
`GET /api/v1/engine/conversations/:id/cost` returns that ledger total for a conversation, subagents included (their
rows are stored under the root conversation id). Rows arrive asynchronously after a request, so `settle_cost` waits
until the ledger's token totals equal the trace's before reading the price.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any

import httpx

from .schema import Trace, Usage
from .trace import ROOT_ROLE, total_usage

LEDGER_WAIT_S = 20.0
POLL_S = 0.4


async def settle_cost(
    http: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    conversation_id: str,
    trace: Trace,
    *,
    wait_s: float = LEDGER_WAIT_S,
) -> dict[str, Any]:
    """The conversation's ledger summary once it accounts for every request in `trace`.

    Returns `{"totalUsd", "inputTokens", "outputTokens", "unpriced", "complete"}`. `complete` is False when the ledger
    never caught up with the usage documents within `wait_s` (the latest reading is returned anyway).
    """
    used = total_usage(trace)
    deadline = time.monotonic() + wait_s
    body: dict[str, Any] = {}
    while True:
        r = await http.get(f"{base_url}/api/v1/engine/conversations/{conversation_id}/cost", headers=headers)
        if r.status_code == 200:
            body = r.json()
            if body.get("inputTokens") == used.input_tokens and body.get("outputTokens") == used.output_tokens:
                return {**body, "complete": True}
        if time.monotonic() >= deadline:
            return {**body, "totalUsd": float(body.get("totalUsd") or 0.0), "complete": False}
        await asyncio.sleep(POLL_S)


def with_ledger_cost(trace: Trace, total_usd: float) -> Trace:
    """The trace with dollars filled in: the ledger total goes on the root role, other roles stay at zero cost.

    Per-role dollar splits are not available from the ledger summary; whole-run cost (what the budget cap, the
    `budget` grader and reports use) is exact.
    """
    roles = dict(trace.usage_by_role)
    roles = {name: replace(u, cost_usd=0.0) for name, u in roles.items()}
    roles[ROOT_ROLE] = replace(roles.get(ROOT_ROLE, Usage()), cost_usd=float(total_usd))
    return replace(trace, usage_by_role=roles)
