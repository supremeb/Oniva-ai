"""Mutation meta-eval (U10): prove the graders catch breakage.

Each `Mutation` injects one known fault into a clean, recorded run (the raw trace the engine produced, the
workspace files the Deep Work graders read, the run's cost) and names the gating graders of a regression-tagged task
that must then fail. `run_meta_eval` grades the clean sample first (it must pass: a grader that fails on a healthy
run proves nothing) and then every mutation. A mutation is *caught* when every grader it names fails; a grader that
cannot catch its mutation is a test failure (`tests/evals/test_mutations.py`).

This is the offline half: it exercises the real task specs and the real grader code on traces, with no stack, no
network and no key. `tests/evals/test_regression_subset.py` repeats two of the faults through the live fake stack
(scripted model), so the whole path from script to verdict is covered as well.

The faults are injected into the *output* of the agent (the trace), not into prompts or the engine source: that is
what the graders see, so it is what decides whether a real regression of that kind would be noticed.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .graders import GradeContext
from .runner import grade_trace
from .schema import GraderResult, Span, Task, Usage
from .trace import ROOT_ROLE, flatten


@dataclass
class Sample:
    """A recorded run: raw trace response, workspace files, and the run's dollar cost."""

    raw: dict[str, Any]
    files: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    extra_model_rounds: int = 0

    def copy(self) -> Sample:
        return Sample(copy.deepcopy(self.raw), copy.deepcopy(self.files), self.cost_usd, self.extra_model_rounds)

    def context(self) -> GradeContext:
        trace = flatten(self.raw)
        roles = dict(trace.usage_by_role)
        roles = {n: replace(u, cost_usd=0.0) for n, u in roles.items()}
        roles[ROOT_ROLE] = replace(roles.get(ROOT_ROLE, Usage()), cost_usd=self.cost_usd)
        root = trace.root
        if self.extra_model_rounds:
            extra = tuple(Span(kind="model", name="model") for _ in range(self.extra_model_rounds))
            root = replace(root, children=root.children + extra)
        return GradeContext(trace=replace(trace, root=root, usage_by_role=roles), raw=self.raw, files=self.files)


@dataclass(frozen=True)
class Mutation:
    id: str
    task_id: str  # a regression-tagged task whose graders must notice
    description: str
    apply: Callable[[Sample], None]  # mutates the (already copied) sample in place
    must_fail: tuple[str, ...]  # grader ids (of that task) that must fail


@dataclass(frozen=True)
class MutationResult:
    mutation: Mutation
    failed: tuple[str, ...]  # gating graders that failed on the mutated sample
    missed: tuple[str, ...]  # must_fail graders that did NOT fail

    @property
    def caught(self) -> bool:
        return not self.missed


def _failed(results: Iterable[GraderResult]) -> tuple[str, ...]:
    return tuple(g.grader_id for g in results if g.gating and not g.passed)


def grade_sample(task: Task, sample: Sample) -> tuple[str, ...]:
    """Gating graders of `task` (code graders only) that fail on `sample`."""
    return _failed(grade_trace(task, sample.context()))


# --- fault injectors -------------------------------------------------------------------------------------------


def _entry(kind: str, payload: dict[str, Any], n: int = 0) -> dict[str, Any]:
    return {"id": f"mut_{kind}_{n}", "kind": kind, "payload": payload, "createdAt": 0}


def _swap(obj: Any, old: str, new: str) -> Any:
    """`obj` with every JSON string occurrence of `old` replaced (tool names live in several places)."""
    return json.loads(json.dumps(obj).replace(f'"{old}"', f'"{new}"'))


def cite_out_of_range(s: Sample) -> None:
    """The report cites a source number that has no entry in the Sources section."""
    s.files["report.md"] = str(s.files["report.md"]) + " A further claim [9]."


def learnings_lose_sources(s: Sample) -> None:
    for item in s.files["learnings"]:
        item.pop("source_url", None)
        item["source_type"] = "web"


def sources_not_in_fixtures(s: Sample) -> None:
    """A learning cites a URL no search or page fetch ever returned (invented source)."""
    s.files["learnings"][0]["source_url"] = "https://invented.example.org/never-fetched"


def limit_exceeded(s: Sample) -> None:
    s.raw["entries"].append(
        _entry(
            "tool_result",
            {"toolCallId": "mut_call", "toolName": "web_search", "status": "limit_exceeded", "isError": True, "content": "too many calls"},
        )
    )


def tool_order_violation(s: Sample) -> None:
    s.raw["entries"].append(
        _entry(
            "tool_result",
            {"toolCallId": "mut_call", "toolName": "dw_write_section", "status": "error", "isError": True,
             "details": {"error": "round_order"}, "content": "no round has finished"},
        )
    )


def run_error(s: Sample) -> None:
    s.raw["entries"].append(_entry("run_error", {"error": {"kind": "provider", "message": "upstream 500"}}))


def compaction_failed(s: Sample) -> None:
    s.raw["submissions"] = [
        *(s.raw.get("submissions") or []),
        {"id": "mut_sub", "requestId": "mut", "status": "unanswered", "unanswered": {"reason": "compaction_failed", "message": "x"}},
    ]


def worker_skips_save(s: Sample) -> None:
    """The research workers never call `dw_save_learnings`."""
    s.raw["children"] = _swap(s.raw.get("children") or {}, "dw_save_learnings", "dw_noop")
    s.raw["children"] = _swap(s.raw["children"], "dw_save_learning", "dw_noop")


def worker_reported_failed(s: Sample) -> None:
    for e in s.raw["entries"]:
        for b in ((e.get("payload") or {}).get("message") or {}).get("blocks") or []:
            if b.get("type") == "tool_call" and b.get("name") == "dw_finalize":
                b.setdefault("arguments", {})["failed_workers"] = ["w1"]


def cost_blowup(s: Sample) -> None:
    s.cost_usd += 50.0


def runaway_rounds(s: Sample) -> None:
    s.extra_model_rounds += 40


def wrong_tool(s: Sample) -> None:
    """The agent searches instead of reading the page (same shape, different tool)."""
    s.raw["entries"] = _swap(s.raw["entries"], "browse_url", "web_search")


def tool_fails(s: Sample) -> None:
    for e in s.raw["entries"]:
        if e.get("kind") == "tool_result":
            e["payload"]["status"] = "error"
            e["payload"]["isError"] = True
            return


# The gating graders named here exist in the corpus tasks `dw-fake-smoke` and `chat-fake-browse`; a rename there
# makes `run_meta_eval` report the mutation as missed (the named grader id is not in the failed set).
MUTATIONS: tuple[Mutation, ...] = (
    Mutation("drop-citation-target", "dw-fake-smoke", "report cites a source number with no Sources entry", cite_out_of_range, ("citations-map",)),
    Mutation("drop-sources", "dw-fake-smoke", "learnings lose their source_url", learnings_lose_sources, ("learnings-sourced", "citations-map")),
    Mutation("invented-source", "dw-fake-smoke", "a learning cites a URL absent from the fixtures", sources_not_in_fixtures, ("urls-resolve",)),
    Mutation("limit-exceeded", "dw-fake-smoke", "a tool call is refused for exceeding the per-round cap", limit_exceeded, ("no-limit-exceeded",)),
    Mutation("tool-order", "dw-fake-smoke", "a section is written before any round finished", tool_order_violation, ("tool-order",)),
    Mutation("run-error", "dw-fake-smoke", "the run ends with a provider error", run_error, ("no-run-error",)),
    Mutation("compaction-failed", "dw-fake-smoke", "an input ended unanswered(compaction_failed)", compaction_failed, ("no-compaction-failure",)),
    Mutation("worker-skips-save", "dw-fake-smoke", "workers never save learnings", worker_skips_save, ("workers-ok",)),
    Mutation("worker-failed", "dw-fake-smoke", "the finalize call reports a failed worker", worker_reported_failed, ("workers-ok",)),
    Mutation("cost-blowup", "dw-fake-smoke", "the run costs far more than its cap", cost_blowup, ("budget",)),
    Mutation("runaway-rounds", "dw-fake-smoke", "many extra model rounds", runaway_rounds, ("budget",)),
    Mutation("wrong-tool", "chat-fake-browse", "web_search instead of browse_url", wrong_tool, ("read-the-page",)),
    Mutation("tool-error", "chat-fake-browse", "the tool call fails", tool_fails, ("tools-ok",)),
)


def run_meta_eval(
    tasks: Mapping[str, Task],
    samples: Mapping[str, Sample],
    mutations: Sequence[Mutation] = MUTATIONS,
) -> tuple[dict[str, tuple[str, ...]], list[MutationResult]]:
    """(clean failures by task, one result per mutation). `samples` maps task id to its clean sample."""
    clean = {tid: grade_sample(tasks[tid], samples[tid]) for tid in samples}
    out: list[MutationResult] = []
    for m in mutations:
        sample = samples[m.task_id].copy()
        m.apply(sample)
        failed = grade_sample(tasks[m.task_id], sample)
        out.append(MutationResult(m, failed, tuple(g for g in m.must_fail if g not in failed)))
    return clean, out


def render_table(results: Sequence[MutationResult]) -> str:
    lines = ["| mutation | task | expected to fail | failed graders | verdict |", "|---|---|---|---|---|"]
    for r in results:
        m = r.mutation
        lines.append(
            f"| {m.id} | {m.task_id} | {', '.join(m.must_fail)} | {', '.join(r.failed) or '-'} | "
            f"{'caught' if r.caught else 'MISSED: ' + ', '.join(r.missed)} |"
        )
    return "\n".join(lines)
