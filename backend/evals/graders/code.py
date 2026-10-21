"""Budget, run health, cache share and tool error rate."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from evals.schema import Span
from evals.trace import cache_read_share, total_usage

from . import GradeContext, Outcome, needs_raw, outcome

CONFIG_KEYS = ("max_usd", "max_input_tokens", "max_rounds")


def root_rounds(ctx: GradeContext) -> int:
    """Model rounds of the root conversation (its direct `model` spans)."""
    return sum(1 for s in ctx.trace.root.children if s.kind == "model")


def budget(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Cost and size ceilings.

    Cost and tokens come from `usage_by_role` (via `total_usage`), not from model
    spans, because spans exclude compaction requests and those cost real money.
    `max_input_tokens` counts all prompt tokens (uncached + cache read + cache write).
    """
    if not any(params.get(k) is not None for k in CONFIG_KEYS):
        raise ValueError(f"budget grader needs at least one of {', '.join(CONFIG_KEYS)}")
    usage = total_usage(ctx.trace)
    prompt = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    problems: list[str] = []
    if (cap := params.get("max_usd")) is not None and usage.cost_usd > cap:
        problems.append(f"cost ${usage.cost_usd:.6g} > ${cap}")
    if (cap := params.get("max_input_tokens")) is not None and prompt > cap:
        problems.append(f"input tokens {prompt} > {cap}")
    if (cap := params.get("max_rounds")) is not None and (n := root_rounds(ctx)) > cap:
        problems.append(f"rounds {n} > {cap}")
    if problems:
        return outcome(False, "; ".join(problems))
    return outcome(True, f"cost ${usage.cost_usd:.6g}, input tokens {prompt}, rounds {root_rounds(ctx)} within budget")


def rounds(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    cap = params.get("max")
    if cap is None:
        raise ValueError("rounds grader needs 'max'")
    n = root_rounds(ctx)
    return outcome(n <= cap, f"{n} root model rounds (max {cap})")


def cache_hit_ratio(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    floor = float(params.get("min_share", 0.0))
    share = cache_read_share(ctx.trace)  # 0.0 when there are no prompt tokens
    score = 1.0 if floor <= 0 else min(1.0, share / floor)
    return outcome(share >= floor, f"cache-read share {share:.3f} (floor {floor})", score)


# --- raw-trace checks -------------------------------------------------------
#
# How these look in a real trace (recorded from fake-mode runs; tests/evals/fixtures/trace_fake_*.json):
# - a tool call the engine refused for exceeding the per-round cap is a `tool_result` entry whose payload
#   `status` is "limit_exceeded" (`isError` true, `details` null); there is no `reason`/`error`/`code` key;
# - a run that fails before answering leaves a `run_error` entry whose `error` is the provider's ModelError;
#   `compaction_failed` is in no entry. It is the reason the run's submission ended `unanswered`, which the
#   trace route reports under `submissions` (`{status: "unanswered", unanswered: {reason, message}}`).

LIMIT_EXCEEDED = "limit_exceeded"
COMPACTION_FAILED = "compaction_failed"


def unanswered_submissions(ctx: GradeContext, reason: str | None = None) -> Iterator[dict[str, Any]]:
    """Submissions of the root and each child that ended unanswered (optionally with one `reason`)."""
    raw = ctx.raw or {}
    groups = [raw.get("submissions") or []]
    groups += [c.get("submissions") or [] for c in (raw.get("children") or {}).values()]
    for group in groups:
        for sub in group:
            why = sub.get("unanswered") or {}
            if sub.get("status") == "unanswered" and (reason is None or why.get("reason") == reason):
                yield sub


def no_run_error(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    if ctx.raw is None:
        return needs_raw("no_run_error")
    errors = [e for e in ctx.entries() if e.get("kind") == "run_error"]
    if not errors:
        return outcome(True, "no run_error entries")
    msgs = []
    for e in errors:
        err = (e.get("payload") or {}).get("error")
        msgs.append(str(err.get("message") if isinstance(err, dict) else err))
    return outcome(False, f"{len(errors)} run_error: " + "; ".join(msgs))


def no_compaction_failure(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Fail when an input ended `unanswered(compaction_failed)`, unless `allow` (expected-failure tasks).

    Reads the trace's `submissions`, not the entries (see the note above). A raw trace without `submissions`
    (recorded before the route reported them) cannot answer the question, so the check fails closed.
    """
    if ctx.raw is None:
        return needs_raw("no_compaction_failure")
    if ctx.raw.get("submissions") is None:
        return outcome(False, "no_compaction_failure needs the trace's 'submissions'; this raw trace has none")
    hits = [s.get("requestId") or s.get("id") for s in unanswered_submissions(ctx, COMPACTION_FAILED)]
    if hits and params.get("allow"):
        return outcome(True, f"{len(hits)} compaction_failed (allowed)")
    return outcome(not hits, f"compaction_failed ended submissions {hits}" if hits else "no compaction failures")


def no_limit_exceeded(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Fail when a tool call got a `limit_exceeded` result (too many calls in a round, subagent depth)."""
    if ctx.raw is None:
        return needs_raw("no_limit_exceeded")
    hits = [
        f"{(e.get('payload') or {}).get('toolName')}:{e.get('id')}"
        for e in ctx.entries()
        if e.get("kind") == "tool_result" and (e.get("payload") or {}).get("status") == LIMIT_EXCEEDED
    ]
    return outcome(not hits, f"limit_exceeded results: {', '.join(hits)}" if hits else "no limit_exceeded results")


def _is_call(s: Span) -> bool:
    return s.kind in ("tool", "subagent")


def tool_error_rate(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Share of tool and subagent calls (whole tree) whose status is not `ok`."""
    cap = float(params.get("max_rate", 0.0))
    calls = [s for s in ctx.trace.walk() if s is not ctx.trace.root and _is_call(s)]
    if not calls:
        return outcome(True, "no tool calls", 1.0)
    bad = sum(1 for s in calls if s.status != "ok")
    rate = bad / len(calls)
    return outcome(rate <= cap, f"{bad}/{len(calls)} tool calls failed (rate {rate:.3f}, max {cap})", 1.0 - rate)
