"""Deterministic graders (code, Deep Work, trajectory).

Every grader is `fn(ctx, params) -> {id, passed, score, detail}` and is pure over
the `Trace` plus optional extras on the `GradeContext`:

- `raw`: the merged trace route response (needed for run errors, tool error codes
  and tool arguments, which the flattened `Trace` does not keep). Graders that
  need it fail closed when it is absent.
- `files`: workspace artifacts a driver collected, e.g. `report.md` (str),
  `learnings` (list of dicts) and `fixture_urls` (URLs present in the replay
  fixtures).

`judge` graders are U7's; `grade` rejects them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from evals.schema import GraderResult, GraderSpec, Trace

Outcome = dict[str, Any]
GraderFn = Callable[["GradeContext", dict[str, Any]], Outcome]


@dataclass(frozen=True)
class GradeContext:
    trace: Trace
    raw: dict[str, Any] | None = None
    files: dict[str, Any] = field(default_factory=dict)

    def entries(self) -> Iterator[dict[str, Any]]:
        """Every entry of the root conversation and of each child, if `raw` is present."""
        if self.raw is None:
            return
        yield from self.raw.get("entries") or []
        for child in (self.raw.get("children") or {}).values():
            yield from child.get("entries") or []


def outcome(passed: bool, detail: str, score: float | None = None) -> Outcome:
    """Grader result without the id (`grade` adds it)."""
    return {"passed": passed, "score": float(passed) if score is None else float(score), "detail": detail}


def needs_raw(what: str) -> Outcome:
    return outcome(False, f"{what} needs the raw trace response (GradeContext.raw); none supplied")


def registry() -> dict[str, GraderFn]:
    from . import code, context, deep_work, memory, subagents, trajectory

    return {
        "budget": code.budget,
        "rounds": code.rounds,
        "cache_hit_ratio": code.cache_hit_ratio,
        "no_run_error": code.no_run_error,
        "no_compaction_failure": code.no_compaction_failure,
        "no_limit_exceeded": code.no_limit_exceeded,
        "tool_error_rate": code.tool_error_rate,
        "tool_order": deep_work.tool_order,
        "no_failed_workers": deep_work.no_failed_workers,
        "citation_map": deep_work.citation_map,
        "learning_sources": deep_work.learning_sources,
        "url_resolves": deep_work.url_resolves,
        "trajectory_match": trajectory.trajectory_match,
        "context_tokens": context.context_tokens,
        "probe_recall": context.probe_recall,
        "reread_after_clear": context.reread_after_clear,
        "no_verbatim_oversize": context.no_verbatim_oversize,
        # U24 (additive): subagent contract
        "worker_failure_rate": subagents.worker_failure_rate,
        "malformed_worker_answers": subagents.malformed_worker_answers,
        "cross_worker_url_overlap": subagents.cross_worker_url_overlap,
        "broken_citations": subagents.broken_citations,
        "uncited_sections": subagents.uncited_sections,
        "steer_applied": subagents.steer_applied,
        # U34 (additive): project memory
        "no_active_write_from_tainted_run": memory.no_active_write_from_tainted_run,
        "no_instruction_in_injected_block": memory.no_instruction_in_injected_block,
        "deleted_means_gone": memory.deleted_means_gone,
        "cross_project_leak": memory.cross_project_leak,
        "score_lift": memory.score_lift,
        "preference_applied": memory.preference_applied,
        "memory_overhead": memory.memory_overhead,
        "memory_curated": memory.memory_curated,
    }


def grade(spec: GraderSpec, ctx: GradeContext) -> Outcome:
    fn = registry().get(spec.type)
    if fn is None:
        raise ValueError(f"grader '{spec.id}': type '{spec.type}' is not a code grader")
    return {"id": spec.id, **fn(ctx, dict(spec.params))}


def grade_all(specs: Iterable[GraderSpec], ctx: GradeContext) -> list[Outcome]:
    """Run every code grader in `specs`, skipping `judge` ones."""
    return [grade(s, ctx) for s in specs if s.type != "judge"]


def to_grader_result(spec: GraderSpec, out: Outcome) -> GraderResult:
    return GraderResult(grader_id=spec.id, passed=bool(out["passed"]), gating=spec.gating, detail=str(out["detail"]))
