"""Decision-ready reports (U8): build, store, render and diff.

A report is a plain JSON-able dict (`REPORT_VERSION`), built from `TrialResult`s. Judge graders arrive as ordinary
`GraderResult`s, so they need no special handling here. Pure functions plus small file helpers; nothing here calls
the engine.

Per task and per suite: pass@k and pass^k (KTD5), cost, cost per passing trial, p50/p95 latency, cache-read share,
tool-call counts, compaction counts and generation-vs-compaction usage. `diff_reports` applies `metrics.compare`
(KTD5) per task and over the pooled outcomes of the tasks both reports share.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import metrics
from .schema import TrialResult
from .trace import cache_read_share, total_usage

REPORT_VERSION = 1


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100); 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _tool_counts(r: TrialResult) -> Counter[str]:
    counts: Counter[str] = Counter()
    if r.trace is not None:
        for span in r.trace.walk():
            if span is not r.trace.root and span.kind in ("tool", "subagent"):
                counts[span.name] += 1
    return counts


def _compactions(r: TrialResult) -> int:
    return sum(1 for s in r.trace.walk() if s.kind == "compaction") if r.trace is not None else 0


def _tokens(r: TrialResult) -> tuple[int, int, int, int]:
    """(cache_read, prompt, generation tokens, compaction tokens) of one trial."""
    if r.trace is None:
        return 0, 0, 0, 0
    u = total_usage(r.trace)
    prompt = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens
    gen = sum(s.usage.input_tokens + s.usage.output_tokens for s in r.trace.walk() if s.kind == "model")
    all_tokens = u.input_tokens + u.output_tokens
    return u.cache_read_tokens, prompt, gen, max(0, all_tokens - gen)


def _failed_graders(r: TrialResult) -> list[dict[str, str]]:
    return [{"id": g.grader_id, "detail": g.detail} for g in r.grader_results if g.gating and not g.passed]


def _block(trials: Sequence[TrialResult], k: int) -> dict[str, Any]:
    """The aggregate numbers shared by a task entry and the suite totals."""
    counted = [t for t in trials if t.status != "skipped_budget"]
    passes = sum(1 for t in counted if t.passed)
    cost = sum(t.cost_usd for t in counted)
    lat = [t.latency_s for t in counted]
    tools: Counter[str] = Counter()
    cache = prompt = gen = comp = 0
    for t in counted:
        tools.update(_tool_counts(t))
        c, p, g, m = _tokens(t)
        cache, prompt, gen, comp = cache + c, prompt + p, gen + g, comp + m
    split = gen + comp
    return {
        "trials": len(counted),
        "skipped": len(trials) - len(counted),
        "passes": passes,
        "pass_rate": passes / len(counted) if counted else None,
        "cost_usd": round(cost, 6),
        "cost_per_pass_usd": round(cost / passes, 6) if passes else None,
        "latency_p50_s": round(percentile(lat, 50), 3),
        "latency_p95_s": round(percentile(lat, 95), 3),
        "cache_read_share": round(cache / prompt, 4) if prompt else 0.0,
        "tool_calls": dict(sorted(tools.items())),
        "compactions": sum(_compactions(t) for t in counted),
        "usage_by_purpose": {
            "generation_tokens": gen,
            "compaction_tokens": comp,
            # The ledger has no per-request split, so dollars are shared out by token count (an estimate).
            "generation_usd_est": round(cost * gen / split, 6) if split else round(cost, 6),
            "compaction_usd_est": round(cost * comp / split, 6) if split else 0.0,
        },
    }


def build_report(
    results: Iterable[TrialResult],
    *,
    name: str,
    suite: Sequence[str] = (),
    mode: str = "fake",
    k: int = 1,
    spent_usd: float | None = None,
    max_usd: float | None = None,
    trace_files: dict[tuple[str, int], str] | None = None,
) -> dict[str, Any]:
    """Aggregate trial results into a report. `trace_files` maps (task, trial index) to a stored trace path."""
    results = list(results)
    trace_files = trace_files or {}
    by_task: dict[str, list[TrialResult]] = {}
    for r in results:
        by_task.setdefault(r.trial.task_id, []).append(r)

    tasks: dict[str, Any] = {}
    for tid in sorted(by_task):
        trials = sorted(by_task[tid], key=lambda t: t.trial.index)
        counted = [t for t in trials if t.status != "skipped_budget"]
        n, c = len(counted), sum(1 for t in counted if t.passed)
        kk = min(k, n)
        entry = _block(trials, k)
        entry.update(
            k=kk,
            pass_at_k=metrics.pass_at_k(n, c, kk) if n else None,
            pass_hat_k=metrics.pass_hat_k(n, c, kk) if n else None,
            outcomes=[1.0 if t.passed else 0.0 for t in counted],
            results=[
                {
                    "index": t.trial.index,
                    "status": t.status,
                    "passed": t.passed,
                    "error": t.error,
                    "cost_usd": round(t.cost_usd, 6),
                    "latency_s": t.latency_s,
                    "failed_graders": _failed_graders(t),
                    "trace": trace_files.get((tid, t.trial.index)),
                }
                for t in trials
            ],
        )
        tasks[tid] = entry

    totals = _block([r for r in results], k)
    scored = [t for t in tasks.values() if t["pass_at_k"] is not None]
    totals.update(
        tasks=len(tasks),
        pass_at_k=sum(t["pass_at_k"] for t in scored) / len(scored) if scored else None,
        pass_hat_k=sum(t["pass_hat_k"] for t in scored) / len(scored) if scored else None,
    )
    return {
        "version": REPORT_VERSION,
        "name": name,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "suite": list(suite),
        "mode": mode,
        "k": k,
        "spent_usd": round(spent_usd, 6) if spent_usd is not None else round(totals["cost_usd"], 6),
        "max_usd": max_usd,
        "totals": totals,
        "tasks": tasks,
    }


def _fmt(x: float | None, digits: int = 2) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    t = report["totals"]
    lines = [
        f"# Eval report: {report['name']}",
        "",
        f"- mode: `{report['mode']}`, k={report['k']}, suite: {', '.join(report['suite']) or 'all'}",
        f"- created: {report['created_at']}",
        f"- trials: {t['trials']} ({t['skipped']} skipped for budget), passes: {t['passes']}",
        f"- pass@k {_fmt(t['pass_at_k'])}, pass^k {_fmt(t['pass_hat_k'])}",
        f"- cost ${t['cost_usd']:.4f} (${_fmt(t['cost_per_pass_usd'], 4)} per pass)"
        + (f", cap ${report['max_usd']:.2f}" if report.get("max_usd") is not None else ""),
        f"- latency p50 {t['latency_p50_s']:.1f}s, p95 {t['latency_p95_s']:.1f}s",
        f"- cache-read share {t['cache_read_share']:.1%}, compactions {t['compactions']}",
        "- usage by purpose: generation {g:,} tokens (${gu:.4f} est), compaction {c:,} tokens (${cu:.4f} est)".format(
            g=t["usage_by_purpose"]["generation_tokens"],
            c=t["usage_by_purpose"]["compaction_tokens"],
            gu=t["usage_by_purpose"]["generation_usd_est"],
            cu=t["usage_by_purpose"]["compaction_usd_est"],
        ),
        "",
        "## Tasks",
        "",
        "| task | pass | pass@k | pass^k | cost | $/pass | p50 | p95 | cache | tools | compactions |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for tid, e in report["tasks"].items():
        tools = sum(e["tool_calls"].values())
        lines.append(
            f"| {tid} | {e['passes']}/{e['trials']} | {_fmt(e['pass_at_k'])} | {_fmt(e['pass_hat_k'])} "
            f"| ${e['cost_usd']:.4f} | {_fmt(e['cost_per_pass_usd'], 4)} | {e['latency_p50_s']:.1f}s "
            f"| {e['latency_p95_s']:.1f}s | {e['cache_read_share']:.0%} | {tools} | {e['compactions']} |"
        )
    failures = [(tid, r) for tid, e in report["tasks"].items() for r in e["results"] if not r["passed"]]
    failures = [(tid, r) for tid, r in failures if r["status"] != "skipped_budget"]
    if failures:
        lines += ["", "## Failed trials", ""]
        for tid, r in failures:
            why = ", ".join(g["id"] for g in r["failed_graders"]) or r["error"] or r["status"]
            link = f" ([trace]({r['trace']}))" if r.get("trace") else ""
            lines.append(f"- `{tid}#{r['index']}` {r['status']}: {why}{link}")
    return "\n".join(lines) + "\n"


# --- files -------------------------------------------------------------------------------------------------------


def write_report(report: dict[str, Any], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path


def load_report(path: Path | str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("version") != REPORT_VERSION or "tasks" not in data:
        raise ValueError(f"{path}: not a version {REPORT_VERSION} eval report")
    return data


def result_path(results_dir: Path | str, suite: Sequence[str], mode: str, *, today: str | None = None) -> Path:
    """`<dir>/<date>-<suite>-<mode>.json`; an existing file is never overwritten (`-2`, `-3`, ...)."""
    results_dir = Path(results_dir)
    stem = f"{today or datetime.now(UTC).strftime('%Y-%m-%d')}-{'+'.join(suite) or 'all'}-{mode}"
    path = results_dir / f"{stem}.json"
    n = 2
    while path.exists():
        path = results_dir / f"{stem}-{n}.json"
        n += 1
    return path


def write_traces(results: Iterable[TrialResult], directory: Path | str, *, only_failed: bool = True) -> dict[tuple[str, int], str]:
    """Store raw traces (default: failed trials only) as `<dir>/<task>-<index>.json`; returns the paths by trial."""
    directory = Path(directory)
    out: dict[tuple[str, int], str] = {}
    for r in results:
        if r.raw is None or (only_failed and r.passed):
            continue
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{r.trial.task_id}-{r.trial.index}.json"
        p.write_text(json.dumps(r.raw, sort_keys=True))
        out[(r.trial.task_id, r.trial.index)] = str(p)
    return out


# --- baseline diff -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskDiff:
    task_id: str
    comparison: metrics.Comparison
    failing_graders: tuple[str, ...] = ()
    traces: tuple[str, ...] = ()


@dataclass
class Diff:
    suite: metrics.Comparison | None
    tasks: list[TaskDiff] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # in the baseline, not in the current report
    new: list[str] = field(default_factory=list)  # in the current report only
    strict_failures: list[str] = field(default_factory=list)  # strict mode only: why the gate fails beyond KTD5

    @property
    def regressions(self) -> list[TaskDiff]:
        return [t for t in self.tasks if t.comparison.regressed]

    @property
    def regressed(self) -> bool:
        return bool(self.regressions) or bool(self.suite and self.suite.regressed) or bool(self.strict_failures)


def _failing_graders(entry: dict[str, Any]) -> set[str]:
    return {g["id"] for r in entry["results"] if r["status"] != "skipped_budget" for g in r["failed_graders"]}


def diff_reports(baseline: dict[str, Any], current: dict[str, Any], *, strict: bool = False) -> Diff:
    """Compare two reports with KTD5. Tasks with no counted trials on either side are not compared.

    `strict` (the deterministic fake-mode CI gate) also fails when a baseline task is missing from the current
    report (a deleted or renamed task must be a deliberate re-baseline), or when a gating grader fails in the
    current report that did not fail in the baseline, even if the pass rate stayed inside the KTD5 noise band.
    """
    b_tasks, c_tasks = baseline["tasks"], current["tasks"]
    common = sorted(set(b_tasks) & set(c_tasks))
    diff = Diff(
        suite=None,
        missing=sorted(set(b_tasks) - set(c_tasks)),
        new=sorted(set(c_tasks) - set(b_tasks)),
    )
    pooled_b: list[float] = []
    pooled_c: list[float] = []
    for tid in common:
        b, c = b_tasks[tid], c_tasks[tid]
        if not b["outcomes"] or not c["outcomes"]:
            continue
        cmp = metrics.compare(
            b["outcomes"],
            c["outcomes"],
            baseline_cost_per_pass=b.get("cost_per_pass_usd"),
            current_cost_per_pass=c.get("cost_per_pass_usd"),
        )
        failing: list[str] = []
        traces: list[str] = []
        if cmp.regressed:
            for r in c["results"]:
                if not r["passed"] and r["status"] != "skipped_budget":
                    failing += [g["id"] for g in r["failed_graders"]] or [r["status"]]
                    if r.get("trace"):
                        traces.append(r["trace"])
        diff.tasks.append(TaskDiff(tid, cmp, tuple(dict.fromkeys(failing)), tuple(traces)))
        pooled_b += b["outcomes"]
        pooled_c += c["outcomes"]
        if strict and (flipped := sorted(_failing_graders(c) - _failing_graders(b))):
            diff.strict_failures.append(f"{tid}: gating graders newly failing: {', '.join(flipped)}")
    if strict:
        diff.strict_failures += [f"{tid}: in the baseline but not in this run" for tid in diff.missing]
    if pooled_b and pooled_c:
        bt, ct = baseline["totals"], current["totals"]
        diff.suite = metrics.compare(
            pooled_b,
            pooled_c,
            baseline_cost_per_pass=bt.get("cost_per_pass_usd"),
            current_cost_per_pass=ct.get("cost_per_pass_usd"),
        )
    return diff


def render_diff_markdown(diff: Diff, *, baseline_name: str, current_name: str) -> str:
    lines = [f"# Eval comparison: {current_name} vs baseline {baseline_name}", ""]
    if diff.suite is not None:
        s = diff.suite
        lines.append(
            f"Suite pass rate {s.baseline_rate:.2f} -> {s.current_rate:.2f} (delta {s.pass_delta:+.2f}, "
            f"threshold {s.threshold:.2f}): **{'REGRESSION' if s.regressed else 'ok'}**"
        )
        lines.append("")
    lines += ["| task | baseline | current | delta | threshold | verdict |", "|---|---|---|---|---|---|"]
    for t in diff.tasks:
        c = t.comparison
        verdict = "ok"
        if c.regressed:
            verdict = "REGRESSION (" + ", ".join(n for n, f in (("pass", c.pass_regressed), ("cost", c.cost_regressed)) if f) + ")"
        lines.append(
            f"| {t.task_id} | {c.baseline_rate:.2f} | {c.current_rate:.2f} | {c.pass_delta:+.2f} "
            f"| {c.threshold:.2f} | {verdict} |"
        )
    if diff.regressions:
        lines += ["", "## Regressions", ""]
        for t in diff.regressions:
            graders = ", ".join(t.failing_graders) or "none recorded"
            links = " ".join(f"[trace]({p})" for p in t.traces)
            lines.append(f"- `{t.task_id}`: failing graders: {graders} {links}".rstrip())
    if diff.strict_failures:
        lines += ["", "## Strict gate", ""] + [f"- {m}" for m in diff.strict_failures]
    if diff.missing:
        lines += ["", f"Missing from the current report: {', '.join(diff.missing)}"]
    if diff.new:
        lines += ["", f"New in the current report (no baseline): {', '.join(diff.new)}"]
    return "\n".join(lines) + "\n"


# --- promoting a baseline ------------------------------------------------------------------------------------------


class PromoteError(Exception):
    """The result cannot become a baseline (it has failures, the target exists, ...)."""


def baseline_name(report: dict[str, Any]) -> str:
    """`<suite>-<mode>`, e.g. `regression-fake` (the file name CI compares against)."""
    return f"{'+'.join(report.get('suite') or []) or 'all'}-{report['mode']}"


def promote_baseline(
    report: dict[str, Any],
    baselines_dir: Path | str,
    *,
    name: str | None = None,
    commit: str | None = None,
    force: bool = False,
    allow_failures: bool = False,
) -> Path:
    """Write `<baselines_dir>/<name>.json` from a results run and return the path.

    The stored copy is the report with its run-local bits removed (trace file paths point into a results folder
    that is not committed) and `name` and `commit` set. A run with a failed trial is refused unless
    `allow_failures`, and an existing baseline is not replaced without `force`: a baseline records the engine as
    it was, and moving it is a decision.
    """
    counted = [r for e in report["tasks"].values() for r in e["results"] if r["status"] != "skipped_budget"]
    if not counted:
        raise PromoteError("the result has no counted trials")
    bad = sorted({tid for tid, e in report["tasks"].items() for r in e["results"] if not r["passed"] and r["status"] != "skipped_budget"})
    if bad and not allow_failures:
        raise PromoteError(f"not promoting: failed trials in {', '.join(bad)} (use --allow-failures to record them)")
    target_name = name or baseline_name(report)
    target = Path(baselines_dir) / f"{target_name}.json"
    if target.exists() and not force:
        raise PromoteError(f"{target} already exists (use --force to replace it)")
    stored = json.loads(json.dumps(report))
    stored["name"] = target_name
    if commit:
        stored["commit"] = commit
    for entry in stored["tasks"].values():
        for r in entry["results"]:
            r["trace"] = None
    return write_report(stored, target)
