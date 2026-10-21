"""Deep Work checks over the trace and the run's workspace artifacts.

Artifacts come from `GradeContext.files`: `report.md` (str), `learnings`
(list of dicts with `source_url` / `source_type`; `[N]` is numbered like the
production Sources section: distinct source URLs in first-seen order) and `fixture_urls` (URLs present in the replay fixtures).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from app.services.deep_work.models import Learning
from app.services.deep_work.synthesis import build_sources_section
from evals.schema import Span

from . import GradeContext, Outcome, needs_raw, outcome

ORDER_ERRORS = ("round_order", "round_open", "no_sections")
SAVE_TOOLS = ("dw_save_learnings", "dw_save_learning")
WORKER_ROLE = "research_worker"
MODEL_KNOWLEDGE = "model_knowledge"
# Same shape as production's citation lint: `[3]`, `[1, 2]`, `[2-4]`, not `[3](url)`.
_CITATION = re.compile(r"\[(\d{1,4}(?:\s*[,–-]\s*\d{1,4})*)\](?!\()")
_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_SOURCE_LINE = re.compile(r"^(\d+)\. \[", re.MULTILINE)


def tool_order(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """No `round_order` / `round_open` / `no_sections` tool errors."""
    if ctx.raw is None:
        return needs_raw("tool_order")
    watched = tuple(params.get("codes") or ORDER_ERRORS)
    found: list[str] = []
    for e in ctx.entries():
        if e.get("kind") != "tool_result":
            continue
        p = e.get("payload") or {}
        d = p.get("details")
        code = d.get("error") if isinstance(d, dict) else None
        if code in watched:
            found.append(f"{p.get('toolName')}:{code}")
    return outcome(not found, "tool-order errors: " + ", ".join(found) if found else "no tool-order errors")


def _descendants(span: Span) -> Iterator[Span]:
    for c in span.children:
        yield c
        yield from _descendants(c)


def _failed_worker_args(ctx: GradeContext) -> list[str]:
    ids: list[str] = []
    for e in ctx.entries():
        if e.get("kind") != "assistant":
            continue
        for b in ((e.get("payload") or {}).get("message") or {}).get("blocks") or []:
            if b.get("type") == "tool_call" and b.get("name") in ("dw_record_reflection", "dw_finalize"):
                ids.extend(str(w) for w in (b.get("arguments") or {}).get("failed_workers") or [])
    return sorted(set(ids))


def no_failed_workers(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Every worker succeeded and saved learnings; none was reported as failed."""
    role = str(params.get("worker_role", WORKER_ROLE))
    problems: list[str] = []
    workers = [s for s in ctx.trace.walk() if s.kind == "subagent" and s.name == role]
    for i, w in enumerate(workers, 1):
        if w.status != "ok":
            problems.append(f"worker {i} ended {w.status}")
        saved = any(c.kind == "tool" and c.name in SAVE_TOOLS and c.status == "ok" for c in _descendants(w))
        if not saved:
            problems.append(f"worker {i} made no learnings save")
    if ctx.raw is not None and (ids := _failed_worker_args(ctx)):
        problems.append("reported failed_workers: " + ", ".join(ids))
    return outcome(not problems, "; ".join(problems) if problems else f"{len(workers)} workers ok")


def _learnings(ctx: GradeContext) -> list[dict[str, Any]] | None:
    v = ctx.files.get("learnings")
    return v if isinstance(v, list) else None


def _strip_code(markdown: str) -> str:
    out, fenced = [], False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            out.append(_INLINE_CODE.sub("", line))
    return "\n".join(out)


def cited_indexes(markdown: str) -> list[int]:
    """Citation numbers outside code; `[1, 2]` and `[2-4]` expand."""
    found: set[int] = set()
    for group in _CITATION.findall(_strip_code(markdown)):
        for part in group.split(","):
            ends = [int(n) for n in re.findall(r"\d+", part)]
            lo, hi = ends[0], ends[-1]
            found.update(range(lo, hi + 1) if lo <= hi else ends)
    return sorted(found)


def source_count(learnings: list[dict[str, Any]]) -> int:
    """Entries production's Sources section lists; its helper decides the numbering."""
    parsed = [Learning.model_validate({"fact": "", "confidence": 1.0, **l}) for l in learnings]
    return len(_SOURCE_LINE.findall(build_sources_section(parsed)))


def citation_map(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Every `[N]` in report.md is in 1..K, K = distinct sources.

    K comes from production's `build_sources_section`, so the numbering cannot drift.
    Each listed source is built from at least one learning, so a valid `[N]` has learnings.
    """
    report, learnings = ctx.files.get("report.md"), _learnings(ctx)
    if not isinstance(report, str) or learnings is None:
        return outcome(False, "citation_map needs files 'report.md' and 'learnings'")
    try:
        k = source_count(learnings)
    except ValidationError as e:
        return outcome(False, f"a learning is invalid: {e.errors()[0]['msg']}")
    cited = cited_indexes(report)
    bad = [n for n in cited if n < 1 or n > k]
    if bad:
        return outcome(False, f"citations {bad} do not map to a source ({k} distinct sources, {len(learnings)} learnings)")
    return outcome(True, f"{len(cited)} distinct citations within {k} sources ({len(learnings)} learnings)")


def learning_sources(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Every learning has a `source_url` unless its `source_type` is `model_knowledge`."""
    learnings = _learnings(ctx)
    if learnings is None:
        return outcome(False, "learning_sources needs file 'learnings'")
    bad = [
        i
        for i, l in enumerate(learnings, 1)
        if not l.get("source_url") and l.get("source_type") != MODEL_KNOWLEDGE
    ]
    return outcome(not bad, f"learnings {bad} have no source_url" if bad else "all learnings sourced")


def _norm(url: str) -> str:
    return url.strip().rstrip("/").lower()


def url_resolves(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Every learning's source URL appears among the replay fixtures' URLs."""
    learnings, fixtures = _learnings(ctx), ctx.files.get("fixture_urls")
    if learnings is None or not isinstance(fixtures, (list, set, tuple)):
        return outcome(False, "url_resolves needs files 'learnings' and 'fixture_urls'")
    known = {_norm(u) for u in fixtures}
    missing = sorted({str(l["source_url"]) for l in learnings if l.get("source_url") and _norm(str(l["source_url"])) not in known})
    return outcome(not missing, "URLs not in fixtures: " + ", ".join(missing) if missing else "all source URLs resolve")


def rounds_within_plan(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    """Research rounds started (`dw_start_round` calls that succeeded) within the limit."""
    cap = params.get("max_rounds")
    if cap is None:
        raise ValueError("rounds grader needs 'max_rounds'")
    n = sum(1 for s in ctx.trace.walk() if s.kind == "tool" and s.name == "dw_start_round" and s.status == "ok")
    return outcome(n <= cap, f"{n} research rounds (max {cap})")
