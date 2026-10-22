"""Context-size graders and the per-trial metrics Gate B reads (plan 2026-10-21-001, U17).

Everything is pure over the raw trace response. The long-session driver adds one key to it, `evalContext`:

    {"tiers": "full", "turns": 100,
     "noisy": false, "tokenSource": "estimate" | "auto",
     "samples": [{"n": 1, "requestId": "...", "clearedThroughEntryId": null, "clearedTokens": 0}, ...],
     "probes": [{"id", "kind", "turn", "requestId", "fact", "question", "expect": [...]}, ...]}

`samples` is the Tier 1 watermark (`view.context`) read from the watch snapshot when each turn settled. The trace
route has the entries but not the watermark, and entries are stored unchanged (clearing is a view), so the watermark
is what lets a grader rebuild the context the model saw.

What is and is not observable from a trace (the Gate B criteria):

| Criterion | Source |
|---|---|
| input tokens per turn after turn 30 | measured: the usage of the turn's last model request (live); in fake mode usage is declared by the script, so it is rebuilt from the entries and the watermark (`estimate`, chars / 4 as the engine does) |
| early-fact and early-ID probe accuracy | the answer to each probe turn, matched against the expected strings |
| compactions | counted (compaction entries). Blocking vs background is NOT in the trace (the cause lives on the compaction task input), so the count is a ceiling for "blocking" |
| `compaction_failed` | submissions that ended `unanswered(compaction_failed)` |
| overflow retries | NOT observable (a retried overflow leaves no entry). Only a run that ended in `context_overflow` is counted |
| cache-read share | measured from usage (live only; 0 in fake mode) |
| `reread_after_clear` | a tool call repeating an earlier (name, args) whose result was already cleared; the watermark is sampled per turn, so a clear that happens mid-turn is seen one turn late |
| no result above `offloadTokens` verbatim | stored `tool_result` entries (post-offload) above the threshold |
| regression pass^3, cost per passing trial | report-level (`evals.gate_b`) |
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

from . import GradeContext, Outcome, needs_raw, outcome

EVAL_CONTEXT_KEY = "evalContext"
CHARS_PER_TOKEN = 4
AFTER_TURN = 30
OFFLOAD_TOKENS = 8000
SUMMARY_PREFIX = "Summary of the earlier conversation:\n\n"
# Tools whose results are never cleared or offloaded; mirrors `CLEAR_EXCLUDE_TOOLS` in the backend's agent config.
EXCLUDE_TOOLS = ("dw_get_learnings", "dw_get_report", "ask_user", "update_todo", "present_plan", "complete_task")


def text_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return ""


def clear_stub(tool: str, tokens: int, details: Any) -> str:
    """The engine's `clearStub` text (engine/src/agent/context-clearing.ts)."""
    head = f"[cleared: {tool} result, ~{tokens} tokens. Re-run the tool"
    off = details.get("offloaded") if isinstance(details, dict) else None
    path = off.get("path") if isinstance(off, dict) else None
    return f"{head}.]" if not isinstance(path, str) or not path else f"{head}, or read {path} if it was saved.]"


def eval_context(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    ec = (raw or {}).get(EVAL_CONTEXT_KEY)
    return ec if isinstance(ec, dict) else None


def _payload(e: dict[str, Any]) -> dict[str, Any]:
    return e.get("payload") or {}


def _body_start(entries: Sequence[dict[str, Any]], head: int) -> int:
    kept = _payload(entries[head]).get("firstKeptEntryId")
    at = next((i for i, e in enumerate(entries) if e.get("id") == kept), -1) if kept else -1
    return at if 0 <= at < head else head + 1


def rebuild_context(
    entries: Sequence[dict[str, Any]], watermark: str | None, exclude_tools: Iterable[str] = EXCLUDE_TOOLS
) -> dict[str, Any]:
    """Estimate the context the engine builds from `entries` with the Tier 1 `watermark` applied.

    Mirrors `buildContext` in engine/src/agent/context-builder.ts: the newest compaction summary plus the entries from
    where it keeps; tool results at or before the watermark (not in `exclude_tools`) become stubs; a result whose call
    is not open is an orphan and is left out. System prompt and tool schemas are not counted (the same in every arm).
    Returns `{tokens, stubs, verbatim_max, verbatim_results}`.
    """
    excluded = set(exclude_tools)
    head = max((i for i, e in enumerate(entries) if e.get("kind") == "compaction"), default=-1)
    tokens = 0
    start = 0
    if head >= 0:
        tokens += text_tokens(SUMMARY_PREFIX + str(_payload(entries[head]).get("summary", "")))
        start = _body_start(entries, head)
    body = list(entries[start:])
    mark = next((i for i, e in enumerate(body) if e.get("id") == watermark), -1) if watermark else -1
    open_calls: set[str] = set()
    stubs = verbatim_max = verbatim = 0
    for i, e in enumerate(body):
        kind, p = e.get("kind"), _payload(e)
        if kind == "user":
            open_calls = set()
            tokens += text_tokens(content_text(p.get("content")))
        elif kind == "assistant":
            open_calls = set()
            blocks = (p.get("message") or {}).get("blocks") or []
            if p.get("partial"):
                blocks = [b for b in blocks if b.get("type") == "text"]
            for b in blocks:
                if b.get("type") == "text":
                    tokens += text_tokens(str(b.get("text", "")))
                elif b.get("type") == "tool_call":
                    open_calls.add(str(b.get("id")))
                    tokens += text_tokens(str(b.get("name", "")) + json.dumps(b.get("arguments") or {}, sort_keys=True))
        elif kind == "tool_result":
            call_id = str(p.get("toolCallId"))
            if call_id not in open_calls:
                continue
            open_calls.discard(call_id)
            tool = str(p.get("toolName", ""))
            original = text_tokens(content_text(p.get("content")))
            if i <= mark and tool not in excluded:
                tokens += text_tokens(clear_stub(tool, original, p.get("details")))
                stubs += 1
            else:
                tokens += original
                verbatim += 1
                verbatim_max = max(verbatim_max, original)
    return {"tokens": tokens, "stubs": stubs, "verbatim_max": verbatim_max, "verbatim_results": verbatim}


def _usage_prompt(entry: dict[str, Any]) -> tuple[int, int]:
    """(prompt tokens, cache-read tokens) of one assistant entry's request."""
    u = _payload(entry).get("usage") or {}
    cr = int(u.get("cacheReadTokens", 0) or 0)
    return int(u.get("inputTokens", 0) or 0) + cr + int(u.get("cacheWriteTokens", 0) or 0), cr


def _turn_bounds(entries: Sequence[dict[str, Any]], samples: Sequence[dict[str, Any]]) -> list[tuple[int, int]]:
    """(first, end) entry indexes of each sampled turn; a turn whose user entry is missing yields (-1, -1)."""
    user_at = {
        _payload(e).get("requestId"): i for i, e in enumerate(entries) if e.get("kind") == "user"
    }
    starts = [user_at.get(s.get("requestId"), -1) for s in samples]
    out = []
    for k, s0 in enumerate(starts):
        nxt = next((x for x in starts[k + 1:] if x >= 0), len(entries))
        out.append((s0, nxt) if s0 >= 0 else (-1, -1))
    return out


def turn_series(raw: dict[str, Any], *, source: str = "auto", exclude_tools: Iterable[str] = EXCLUDE_TOOLS) -> list[dict[str, Any]]:
    """Per sampled turn: the context size of its last model request, `{n, tokens, source, estimate, ...}`.

    `source`: `usage` (the request's measured prompt tokens), `estimate` (rebuilt from entries) or `auto` (usage when
    the request reported any, else the estimate).
    """
    ec = eval_context(raw) or {}
    samples = ec.get("samples") or []
    entries = raw.get("entries") or []
    out = []
    for sample, (first, end) in zip(samples, _turn_bounds(entries, samples), strict=True):
        if first < 0:
            continue
        last_asst = max((i for i in range(first, end) if entries[i].get("kind") == "assistant"), default=-1)
        if last_asst < 0:
            continue
        rebuilt = rebuild_context(entries[:last_asst], sample.get("clearedThroughEntryId"), exclude_tools)
        measured, cache_read = _usage_prompt(entries[last_asst])
        use_usage = source == "usage" or (source == "auto" and measured > 0)
        out.append({
            "n": int(sample["n"]),
            "tokens": measured if use_usage else rebuilt["tokens"],
            "source": "usage" if use_usage else "estimate",
            "estimate": rebuilt["tokens"],
            "usage": measured,
            "cache_read": cache_read,
            "stubs": rebuilt["stubs"],
            "verbatim_max": rebuilt["verbatim_max"],
        })
    return out


def _canonical_args(arguments: Any) -> str:
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"))


def reread_stats(raw: dict[str, Any], exclude_tools: Iterable[str] = EXCLUDE_TOOLS) -> dict[str, int]:
    """`clears` (results cleared by the final watermark) and `rereads` (calls repeating a cleared call)."""
    ec = eval_context(raw) or {}
    samples = ec.get("samples") or []
    entries = raw.get("entries") or []
    excluded = set(exclude_tools)
    index = {e.get("id"): i for i, e in enumerate(entries)}
    bounds = _turn_bounds(entries, samples)

    def watermark_index(k: int) -> int:
        return index.get(samples[k].get("clearedThroughEntryId"), -1) if samples[k].get("clearedThroughEntryId") else -1

    result_at: dict[str, int] = {}
    for i, e in enumerate(entries):
        if e.get("kind") == "tool_result":
            result_at.setdefault(str(_payload(e).get("toolCallId")), i)
    tool_of = {str(_payload(entries[i]).get("toolCallId")): str(_payload(entries[i]).get("toolName")) for i in result_at.values()}

    clears = 0
    if samples:
        final = watermark_index(len(samples) - 1)
        clears = sum(1 for i in result_at.values() if i <= final and tool_of.get(str(_payload(entries[i]).get("toolCallId"))) not in excluded)

    seen: dict[tuple[str, str], list[str]] = {}
    rereads = 0
    for k, (first, end) in enumerate(bounds):
        if first < 0:
            continue
        mark = watermark_index(k)
        for i in range(first, end):
            e = entries[i]
            if e.get("kind") != "assistant":
                continue
            for b in (_payload(e).get("message") or {}).get("blocks") or []:
                if b.get("type") != "tool_call" or b.get("name") in excluded:
                    continue
                key = (str(b.get("name")), _canonical_args(b.get("arguments")))
                if any(result_at.get(prior, 10**9) <= mark for prior in seen.get(key, ())):
                    rereads += 1
                seen.setdefault(key, []).append(str(b.get("id")))
    return {"clears": clears, "rereads": rereads}


_NORM_COMMA = re.compile(r"(?<=\d),(?=\d)")


def normalize_answer(text: str) -> str:
    return " ".join(_NORM_COMMA.sub("", text.lower()).split())


def answers_by_request(raw: dict[str, Any]) -> dict[str, str]:
    """All assistant text written after each user entry, by that entry's `requestId`."""
    out: dict[str, str] = {}
    current: str | None = None
    for e in raw.get("entries") or []:
        if e.get("kind") == "user":
            current = _payload(e).get("requestId")
        elif e.get("kind") == "assistant" and current is not None and not _payload(e).get("partial"):
            blocks = (_payload(e).get("message") or {}).get("blocks") or []
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            out[current] = (out.get(current, "") + " " + text).strip()
    return out


def probe_outcomes(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per probe turn: `{id, kind, turn, answer, hit}`."""
    ec = eval_context(raw) or {}
    answers = answers_by_request(raw)
    rows = []
    for p in ec.get("probes") or []:
        answer = answers.get(p.get("requestId"), "")
        norm = normalize_answer(answer)
        hit = any(normalize_answer(str(x)) in norm for x in p.get("expect") or [])
        rows.append({"id": p["id"], "kind": p.get("kind", "fact"), "turn": p.get("turn"), "answer": answer, "hit": hit})
    return rows


def _count(raw: dict[str, Any], kind: str) -> int:
    return sum(1 for e in raw.get("entries") or [] if e.get("kind") == kind)


def trial_metrics(raw: dict[str, Any] | None, *, after_turn: int = AFTER_TURN, source: str | None = None) -> dict[str, Any] | None:
    """Everything Gate B reads from one trial, JSON-able. None when the trace carries no `evalContext`.

    `source` defaults to the driver's `evalContext.tokenSource`: `estimate` in fake mode (usage there is declared by
    the script, not measured), `auto` in live mode.
    """
    ec = eval_context(raw)
    if raw is None or ec is None:
        return None
    source = source or str(ec.get("tokenSource") or "auto")
    series = turn_series(raw, source=source)
    late = [t for t in series if t["n"] > after_turn]
    entries = raw.get("entries") or []
    asst = []  # (turn number, prompt, cache read) of every request, to price the whole session
    samples = ec.get("samples") or []
    for sample, (first, end) in zip(samples, _turn_bounds(entries, samples), strict=True):
        if first >= 0:
            asst += [(int(sample["n"]), *_usage_prompt(entries[i])) for i in range(first, end) if entries[i].get("kind") == "assistant"]
    late_req = [(p, c) for n, p, c in asst if n > after_turn]
    prompt_total = sum(p for p, _ in late_req)
    probes = probe_outcomes(raw)
    by_kind: dict[str, list[int]] = {}
    for row in probes:
        hits_total = by_kind.setdefault(row["kind"], [0, 0])
        hits_total[0] += int(row["hit"])
        hits_total[1] += 1
    stored = [
        (str(_payload(e).get("toolName")), text_tokens(content_text(_payload(e).get("content"))))
        for e in entries
        if e.get("kind") == "tool_result" and _payload(e).get("toolName") not in EXCLUDE_TOOLS
    ]
    marks = [s.get("clearedThroughEntryId") for s in samples]
    failed = [
        s for group in [raw.get("submissions") or [], *[(c.get("submissions") or []) for c in (raw.get("children") or {}).values()]]
        for s in group
        if s.get("status") == "unanswered" and (s.get("unanswered") or {}).get("reason") == "compaction_failed"
    ]
    overflow = [
        e for e in entries
        if e.get("kind") == "run_error" and str((_payload(e).get("error") or {}).get("kind")) == "context_overflow"
    ]
    return {
        "tiers": ec.get("tiers"),
        "turns": ec.get("turns"),
        "noisy": bool(ec.get("noisy")),
        "after_turn": after_turn,
        "series": [[t["n"], t["tokens"]] for t in series],
        "token_source": late[0]["source"] if late else (series[0]["source"] if series else None),
        "mean_tokens_after": round(sum(t["tokens"] for t in late) / len(late), 1) if late else None,
        "mean_estimate_after": round(sum(t["estimate"] for t in late) / len(late), 1) if late else None,
        "prompt_tokens_after": prompt_total,
        "cache_read_share_after": round(sum(c for _, c in late_req) / prompt_total, 4) if prompt_total else None,
        "compactions": _count(raw, "compaction"),
        "compaction_failed": len(failed),
        "overflow_failures": len(overflow),
        **reread_stats(raw),
        "offloaded_results": sum(
            1 for e in entries
            if e.get("kind") == "tool_result" and isinstance((_payload(e).get("details") or {}).get("offloaded"), dict)
        ),
        "max_stored_result_tokens": max((t for _, t in stored), default=0),
        "stub_results_last_turn": series[-1]["stubs"] if series else 0,
        "watermark_advances": len({m for m in marks if m}),
        "final_watermark": next((m for m in reversed(marks) if m), None),
        "cleared_tokens": int((samples[-1].get("clearedTokens", 0)) if samples else 0),
        "probes": {k: v for k, v in sorted(by_kind.items())},
    }


_memo: tuple[Any, tuple, dict[str, Any] | None] | None = None


def _metrics(ctx: GradeContext, params: dict[str, Any]) -> dict[str, Any] | None:
    global _memo
    key = (int(params.get("after_turn", AFTER_TURN)), params.get("source"))
    if _memo is not None and _memo[0] is ctx.raw and _memo[1] == key:
        return _memo[2]
    m = trial_metrics(ctx.raw, after_turn=key[0], source=key[1])
    _memo = (ctx.raw, key, m)
    return m


def _missing(what: str) -> Outcome:
    return outcome(False, f"{what} needs the long-session driver's 'evalContext' in the raw trace; none found")


def context_tokens(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Mean context tokens per turn after `after_turn` (default 30). Gates only with `max_mean`."""
    if ctx.raw is None:
        return needs_raw("context_tokens")
    m = _metrics(ctx, params)
    if m is None:
        return _missing("context_tokens")
    mean = m["mean_tokens_after"]
    if mean is None:
        return outcome(False, f"no sampled turn after turn {m['after_turn']}")
    cap = params.get("max_mean")
    ok = cap is None or mean <= float(cap)
    return outcome(ok, f"mean {mean:.0f} tokens per turn after turn {m['after_turn']} ({m['token_source']})"
                       + ("" if cap is None else f", max {cap}"))


def probe_recall(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Share of probe answers (of `kind` `fact`, `id` or all) that state the planted value. Gates with `min_rate`."""
    if ctx.raw is None:
        return needs_raw("probe_recall")
    if eval_context(ctx.raw) is None:
        return _missing("probe_recall")
    kind = params.get("kind")
    rows = [r for r in probe_outcomes(ctx.raw) if kind in (None, "all", r["kind"])]
    if not rows:
        return outcome(False, f"no {kind or 'any'} probes")
    hits = sum(r["hit"] for r in rows)
    rate = hits / len(rows)
    floor = float(params.get("min_rate", 0.0))
    misses = [f"{r['id']}@{r['turn']}" for r in rows if not r["hit"]]
    return outcome(rate >= floor, f"{hits}/{len(rows)} {kind or 'all'} probes answered correctly"
                                  + (f"; missed {', '.join(misses)}" if misses else ""), rate)


def reread_after_clear(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Calls that repeat an already-cleared call, over results cleared. Gates with `max_rate` (default 0.10)."""
    if ctx.raw is None:
        return needs_raw("reread_after_clear")
    if eval_context(ctx.raw) is None:
        return _missing("reread_after_clear")
    s = reread_stats(ctx.raw)
    if s["clears"] == 0:
        return outcome(True, "nothing was cleared")
    rate = s["rereads"] / s["clears"]
    cap = float(params.get("max_rate", 0.10))
    return outcome(rate <= cap, f"{s['rereads']} rereads over {s['clears']} cleared results (rate {rate:.3f}, max {cap})", 1.0 - rate)


def no_verbatim_oversize(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """With offload on, no stored tool result is above `max_tokens` (default 8000 = `offloadTokens`)."""
    if ctx.raw is None:
        return needs_raw("no_verbatim_oversize")
    ec = eval_context(ctx.raw)
    if ec is None:
        return _missing("no_verbatim_oversize")
    if ec.get("tiers") == "off":
        return outcome(True, "tiers are off: offload does not apply")
    cap = int(params.get("max_tokens", OFFLOAD_TOKENS))
    big = [
        (str(_payload(e).get("toolName")), t)
        for e in ctx.raw.get("entries") or []
        if e.get("kind") == "tool_result"
        and _payload(e).get("toolName") not in EXCLUDE_TOOLS
        and (t := text_tokens(content_text(_payload(e).get("content")))) > cap
    ]
    if big:
        return outcome(False, f"{len(big)} tool result(s) above {cap} tokens stored verbatim, e.g. {big[0][0]} at {big[0][1]}")
    return outcome(True, f"no tool result above {cap} tokens")
