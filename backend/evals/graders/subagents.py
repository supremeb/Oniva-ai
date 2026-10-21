"""Subagent-contract graders (U24, Gate C), computed from the merged trace route response (`GradeContext.raw`).

Each grader states its measurement twice: a sentence for people and, after `DATA_SEPARATOR`, a JSON object that
`evals.gate_c` sums across trials (`parse_data`). Gate C compares two arms (`baseline`: contract v2 off, `v2`: on) on
the same tasks:

- `worker_failure_rate`: share of `research_worker` calls that did not end ok with learnings saved.
- `malformed_worker_answers`: worker/reflector/verifier answers that reached the orchestrator as an `ok` result but are
  not the object the role promises. With contract v2 the engine validates the object (`details.result`) and a worker
  that never calls `complete_task` becomes an error result instead, so this count is the one that must reach 0.
- `cross_worker_url_overlap`: source URLs saved by more than one worker of a round (and, optionally, URLs a later
  round saves again).
- `broken_citations`: citations in the final report that map to no source, and `dw_finalize` calls the citation check
  refused.
- `uncited_sections`: factual report sections with no citation (production's own lint).
- `steer_applied`: the steered worker received the orchestrator's message and its later actions followed it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from app.services.deep_work.engine_tools import UNCITED_MIN_CHARS, _parse_sections, lint_citations

from . import GradeContext, Outcome, needs_raw, outcome
from .deep_work import SAVE_TOOLS, WORKER_ROLE, source_count

DATA_SEPARATOR = " || "
STEER_MARKER = "Message from the orchestrator"
SOURCES_HEADING = "sources"
ROLE_KEYS: dict[str, tuple[str, ...]] = {
    "research_worker": ("summary", "learning_count", "source_count", "top_findings", "sources"),
    "reflector": ("score", "gaps", "round", "continuing"),
    "verifier": ("unsupported_claims", "missing_citations", "broken_citations"),
}


def with_data(text: str, data: dict[str, Any]) -> str:
    return f"{text}{DATA_SEPARATOR}{json.dumps(data, sort_keys=True)}"


def parse_data(detail: str) -> dict[str, Any] | None:
    """The JSON measurement after the separator in a grader's detail, else None."""
    head, sep, tail = detail.rpartition(DATA_SEPARATOR)
    if not sep:
        return None
    try:
        data = json.loads(tail)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# --- reading the raw trace -----------------------------------------------------------------------------------------


def _root(ctx: GradeContext) -> list[dict[str, Any]]:
    return list((ctx.raw or {}).get("entries") or [])


def _child(ctx: GradeContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    details = payload.get("details")
    cid = details.get("conversationId") if isinstance(details, dict) else None
    return ((ctx.raw or {}).get("children") or {}).get(cid) if isinstance(cid, str) else None


def _results(ctx: GradeContext, *tools: str) -> list[dict[str, Any]]:
    """Payloads of the root conversation's tool results for `tools`, in order."""
    return [
        e.get("payload") or {}
        for e in _root(ctx)
        if e.get("kind") == "tool_result" and (e.get("payload") or {}).get("toolName") in tools
    ]


def _tool_calls(entries: list[dict[str, Any]]) -> Iterator[tuple[int, dict[str, Any]]]:
    """(entry index, tool_call block) for every call in the assistant entries."""
    for i, e in enumerate(entries):
        if e.get("kind") != "assistant":
            continue
        for b in ((e.get("payload") or {}).get("message") or {}).get("blocks") or []:
            if b.get("type") == "tool_call":
                yield i, b


def _is_ok(payload: dict[str, Any]) -> bool:
    return payload.get("status", "ok") == "ok" and not payload.get("isError")


def _norm(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _saved_urls(child: dict[str, Any] | None) -> list[str]:
    """Distinct source URLs a worker saved (and pages it browsed), in first-seen order."""
    seen: dict[str, None] = {}
    for _, b in _tool_calls(list((child or {}).get("entries") or [])):
        args = b.get("arguments") or {}
        if b.get("name") in SAVE_TOOLS:
            items = args.get("learnings") if "learnings" in args else [args]
            for l in items or []:
                if isinstance(l, dict) and l.get("source_url"):
                    seen[_norm(str(l["source_url"]))] = None
        elif b.get("name") == "browse_url" and args.get("url"):
            seen[_norm(str(args["url"]))] = None
    return list(seen)


def _saved_ok(child: dict[str, Any] | None) -> bool:
    return any(
        (e.get("payload") or {}).get("toolName") in SAVE_TOOLS and _is_ok(e.get("payload") or {})
        for e in (child or {}).get("entries") or []
        if e.get("kind") == "tool_result"
    )


# --- failed workers -------------------------------------------------------------------------------------------------


def worker_failure_rate(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Failed `research_worker` calls over all calls: a non-ok result, or an ok one whose worker saved nothing."""
    if ctx.raw is None:
        return needs_raw("worker_failure_rate")
    role = str(params.get("worker_role", WORKER_ROLE))
    calls = _results(ctx, role)
    if not calls:
        return outcome(False, with_data(f"no {role} call in the trace", {"workers": 0, "failed": 0}))
    reasons: list[str] = []
    for n, p in enumerate(calls, 1):
        if not _is_ok(p):
            details = p.get("details") if isinstance(p.get("details"), dict) else {}
            reasons.append(f"worker {n}: {details.get('reason') or p.get('status')}")
        elif not _saved_ok(_child(ctx, p)):
            reasons.append(f"worker {n}: saved no learnings")
    rate = len(reasons) / len(calls)
    limit = float(params.get("max_rate", 0.0))
    text = f"{len(reasons)} of {len(calls)} workers failed (rate {rate:.2f}, max {limit:.2f})"
    if reasons:
        text += ": " + "; ".join(reasons)
    data: dict[str, Any] = {"workers": len(calls), "failed": len(reasons)}
    if params.get("gate") is False:  # a task that injects a failure on purpose stays out of Gate C's failed-worker rate
        data["gate"] = False
    return outcome(rate <= limit, with_data(text, data), score=1 - rate)


# --- malformed answers ----------------------------------------------------------------------------------------------


def _valid_object(value: Any, keys: tuple[str, ...]) -> bool:
    return isinstance(value, dict) and all(k in value for k in keys)


def classify_answer(role: str, payload: dict[str, Any]) -> str:
    """`typed` (engine-validated object), `valid` (untyped text that parses to the right object), `malformed`,
    or `unanswered` (an error result: the orchestrator was told there is no answer)."""
    if not _is_ok(payload):
        return "unanswered"
    keys = ROLE_KEYS.get(role, ())
    details = payload.get("details")
    typed = details.get("result") if isinstance(details, dict) else None
    if typed is not None:
        return "typed" if _valid_object(typed, keys) else "malformed"
    try:
        parsed = json.loads(payload.get("content") or "")
    except ValueError:
        return "malformed"
    return "valid" if _valid_object(parsed, keys) else "malformed"


def malformed_worker_answers(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Answers that reached the orchestrator as `ok` but are not the promised object. Passes at `max` (default 0)."""
    if ctx.raw is None:
        return needs_raw("malformed_worker_answers")
    roles = tuple(params.get("roles") or ROLE_KEYS)
    counts = {"typed": 0, "valid": 0, "malformed": 0, "unanswered": 0}
    nudged = 0
    for p in _results(ctx, *roles):
        counts[classify_answer(str(p["toolName"]), p)] += 1
        details = p.get("details")
        nudged += bool(isinstance(details, dict) and details.get("nudged"))
    answers = sum(counts.values())
    if answers == 0:
        return outcome(False, with_data("no worker answer in the trace", {"answers": 0, **counts, "nudged": 0}))
    limit = int(params.get("max", 0))
    text = (
        f"{counts['malformed']} malformed of {answers} answers (max {limit}); {counts['unanswered']} unanswered, "
        f"{nudged} nudged to complete_task"
    )
    return outcome(counts["malformed"] <= limit, with_data(text, {"answers": answers, **counts, "nudged": nudged}))


# --- URL overlap ----------------------------------------------------------------------------------------------------


def _worker_urls_by_round(ctx: GradeContext, role: str) -> dict[int, list[list[str]]]:
    """{round: [urls of each worker]}: a worker belongs to the latest round started before its result."""
    rounds: dict[int, list[list[str]]] = {}
    current = 0
    started = {b.get("id"): (b.get("arguments") or {}).get("round") for _, b in _tool_calls(_root(ctx)) if b.get("name") == "dw_start_round"}
    for e in _root(ctx):
        if e.get("kind") != "tool_result":
            continue
        p = e.get("payload") or {}
        if p.get("toolName") == "dw_start_round" and _is_ok(p) and isinstance(started.get(p.get("toolCallId")), int):
            current = int(started[p["toolCallId"]])
        elif p.get("toolName") == role and current:
            rounds.setdefault(current, []).append(_saved_urls(_child(ctx, p)))
    return rounds


def cross_worker_url_overlap(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Share of a round's saved source URLs that more than one worker saved; with `max_overlap` / `max_repeat` it gates.

    overlap = 1 - distinct / total over rounds with at least two workers (total = URLs summed per worker). repeat = share
    of the URLs a later round saves that an earlier round already had.
    """
    if ctx.raw is None:
        return needs_raw("cross_worker_url_overlap")
    rounds = _worker_urls_by_round(ctx, str(params.get("worker_role", WORKER_ROLE)))
    per_round, total, distinct, later, repeated, seen = [], 0, 0, 0, 0, set()
    for r in sorted(rounds):
        workers = rounds[r]
        union = {u for urls in workers for u in urls}
        n = sum(len(urls) for urls in workers)
        per_round.append({"round": r, "workers": len(workers), "urls": n, "distinct": len(union)})
        if len(workers) >= 2:
            total, distinct = total + n, distinct + len(union)
        if r > 1:
            later, repeated = later + len(union), repeated + len(union & seen)
        seen |= union
    if not any(row["urls"] for row in per_round):
        return outcome(False, with_data("no worker saved a source URL", {"rounds": per_round, "urls": 0, "distinct": 0}))
    overlap = 1 - distinct / total if total else 0.0
    repeat = repeated / later if later else 0.0
    max_overlap, max_repeat = params.get("max_overlap"), params.get("max_repeat")
    ok = (max_overlap is None or overlap <= float(max_overlap)) and (max_repeat is None or repeat <= float(max_repeat))
    text = f"overlap {overlap:.2f} ({total - distinct} of {total} URLs shared), repeat across rounds {repeat:.2f}"
    data = {"rounds": per_round, "urls": total, "distinct": distinct, "later_urls": later, "repeated": repeated}
    return outcome(ok, with_data(text, data), score=1 - overlap)


# --- citations ------------------------------------------------------------------------------------------------------


def _report_sections(report: str) -> list[tuple[str, str]]:
    return [(h, b) for h, b in _parse_sections(report) if h.lower() != SOURCES_HEADING]


def broken_citations(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Citations in report.md that map to no source, and (`max_blocked`, default 0) `dw_finalize` calls refused for them."""
    report, learnings = ctx.files.get("report.md"), ctx.files.get("learnings")
    if not isinstance(report, str) or not isinstance(learnings, list):
        return outcome(False, "broken_citations needs files 'report.md' and 'learnings'")
    k = source_count(learnings)
    invalid, _ = lint_citations(_report_sections(report), k)
    blocked = sum(
        1
        for p in _results(ctx, "dw_finalize")
        if isinstance(p.get("details"), dict) and p["details"].get("error") == "invalid_citations"
    )
    limit = int(params.get("max_blocked", 0))
    broken = sum(len(v) for v in invalid.values())
    text = f"{broken} broken citation(s) in the report ({k} sources); {blocked} finalize call(s) refused (max {limit})"
    return outcome(broken == 0 and blocked <= limit, with_data(text, {"broken": broken, "blocked": blocked, "sources": k}))


def uncited_sections(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Report sections of at least `UNCITED_MIN_CHARS` characters with no citation, by production's lint."""
    report, learnings = ctx.files.get("report.md"), ctx.files.get("learnings")
    if not isinstance(report, str) or not isinstance(learnings, list):
        return outcome(False, "uncited_sections needs files 'report.md' and 'learnings'")
    _, uncited = lint_citations(_report_sections(report), source_count(learnings))
    text = f"uncited sections: {', '.join(uncited) if uncited else 'none'} (sections of {UNCITED_MIN_CHARS}+ characters)"
    return outcome(not uncited, with_data(text, {"uncited": len(uncited)}))


# --- steering -------------------------------------------------------------------------------------------------------


def _steered_child(ctx: GradeContext, worker: str, role: str) -> dict[str, Any] | None:
    """The child conversation of `worker` (its first user message names the worker id)."""
    for child in ((ctx.raw or {}).get("children") or {}).values():
        if child.get("role") != role:
            continue
        for e in child.get("entries") or []:
            if e.get("kind") == "user":
                if f"WORKER {worker}:" in str((e.get("payload") or {}).get("content", "")):
                    return child
                break
    return None


def steer_applied(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """The steered worker saw the orchestrator's message and then acted on it.

    `worker` is the worker id, `keywords` words (any, case-insensitive) that the worker's later searches, pages or saved
    learnings must contain. Delivery is read from the worker's own conversation (a user entry carrying the
    orchestrator's steer prefix), so a `steer_worker` call that errored, or never happened, does not count.
    """
    if ctx.raw is None:
        return needs_raw("steer_applied")
    worker = str(params.get("worker") or "")
    keywords = [str(k).lower() for k in params.get("keywords") or []]
    if not worker or not keywords:
        raise ValueError("steer_applied needs 'worker' and 'keywords'")
    calls = _results(ctx, "steer_worker")
    ok_calls = sum(1 for p in calls if _is_ok(p))
    child = _steered_child(ctx, worker, str(params.get("worker_role", WORKER_ROLE)))
    entries = list((child or {}).get("entries") or [])
    delivered_at = next(
        (i for i, e in enumerate(entries) if e.get("kind") == "user" and STEER_MARKER in json.dumps(e.get("payload") or {})),
        None,
    )
    changed = False
    if delivered_at is not None:
        for i, b in _tool_calls(entries):
            if i > delivered_at and any(k in json.dumps(b.get("arguments") or {}).lower() for k in keywords):
                changed = True
                break
    data = {"steer_calls": len(calls), "steer_ok": ok_calls, "delivered": delivered_at is not None, "changed": changed}
    if child is None:
        why = f"no conversation for worker {worker}"
    elif not calls:
        why = "the orchestrator never called steer_worker"
    elif delivered_at is None:
        why = f"steer_worker called ({ok_calls} ok) but the message never reached {worker}"
    elif not changed:
        why = f"{worker} received the steer but its later actions do not mention {', '.join(keywords)}"
    else:
        why = f"{worker} received the steer and then worked on {', '.join(keywords)}"
    return outcome(delivered_at is not None and changed, with_data(why, data))
