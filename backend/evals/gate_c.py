"""Gate C (plan 2026-10-21-001, U24): the subagent contract v2 against the baseline, from two stored `run` reports.

    python -m evals.cli run --suite dw_subagents --mode live -k 3 --max-usd 30 --arm baseline --out base.json
    python -m evals.cli run --suite dw_subagents --mode live -k 3 --max-usd 30 --arm v2       --out v2.json
    python -m evals.gate_c base.json v2.json [--regression-baseline R0.json --regression-v2 R1.json] [--noisy TASK ...]

(`--arm` defaults to the baseline, so the plan's own command `run --suite dw_subagents --mode live -k 3 --max-usd 30` is
the baseline arm; `EVAL_ARM=v2` selects the other.) Nothing here calls a model or the network: every number is read from
the graders' recorded details (`evals.graders.subagents.parse_data`) and the judge's `N of M yes|no` detail.

Criteria (non-`noisy` tasks only; `--noisy` excludes tasks the Gate A baseline flagged):

1. failed-worker rate: v2 at most half of baseline, or at most 2%.
2. malformed worker answers reaching the orchestrator: 0 in v2.
3. cross-worker source-URL overlap per round: v2 at least 30% below baseline.
4. unsupported-claim rate (calibrated `claim_supported` judge): v2 at least 25% below baseline, relatively.
5. broken citations in the final report: 0 in v2.
6. steer tasks: the steered worker's behaviour changed in at least 80% of v2 trials.
7. regression suite (needs `--regression-*`): no regression versus the baseline run (KTD5 paired comparison, pass^k), and
   cost per dw_subagents trial at most 15% above baseline.

A criterion with nothing to measure (no judge verdicts in a fake run, a baseline with no overlap, no regression reports)
is `n/a`; it never passes silently: exit code 1 if any criterion failed, and with `--require-all` also if any is `n/a`.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import report as rpt
from .graders.subagents import parse_data

JUDGE_ID = "claims-supported"
_JUDGE = re.compile(r"judge claim_supported: (\d+) of (\d+) (yes|no)\b")
COST_LIMIT = 1.15
FAILED_WORKER_FLOOR = 0.02


@dataclass
class Measures:
    """Sums over the included trials of one arm."""

    trials: int = 0
    workers: int = 0
    failed_workers: int = 0
    answers: int = 0
    malformed: int = 0
    nudged: int = 0
    overlap_urls: int = 0
    overlap_distinct: int = 0
    broken: int = 0
    blocked_finalizes: int = 0
    uncited_sections: int = 0
    steer_trials: int = 0
    steer_applied: int = 0
    claims: int = 0
    unsupported: int = 0
    cost_usd: float = 0.0
    passed_trials: int = 0
    tasks: list[str] = field(default_factory=list)

    @property
    def failed_worker_rate(self) -> float | None:
        return self.failed_workers / self.workers if self.workers else None

    @property
    def overlap(self) -> float | None:
        return 1 - self.overlap_distinct / self.overlap_urls if self.overlap_urls else None

    @property
    def unsupported_rate(self) -> float | None:
        return self.unsupported / self.claims if self.claims else None

    @property
    def steer_rate(self) -> float | None:
        return self.steer_applied / self.steer_trials if self.steer_trials else None

    @property
    def cost_per_trial(self) -> float | None:
        return self.cost_usd / self.trials if self.trials else None


def measure(report: dict[str, Any], exclude: Iterable[str] = ()) -> Measures:
    """Aggregate the recorded grader measurements of `report`, skipping tasks in `exclude` and skipped-budget trials."""
    skip = set(exclude)
    m = Measures()
    for tid, entry in sorted(report.get("tasks", {}).items()):
        if tid in skip:
            continue
        m.tasks.append(tid)
        for res in entry.get("results", []):
            if res.get("status") == "skipped_budget":
                continue
            m.trials += 1
            m.cost_usd += float(res.get("cost_usd") or 0.0)
            m.passed_trials += bool(res.get("passed"))
            for g in res.get("graders") or []:
                _add_grader(m, g)
    return m


def _add_grader(m: Measures, g: dict[str, Any]) -> None:
    detail = str(g.get("detail", ""))
    if g.get("id") == JUDGE_ID and (hit := _JUDGE.search(detail)):
        n, total, verdict = int(hit.group(1)), int(hit.group(2)), hit.group(3)
        m.claims += total
        m.unsupported += n if verdict == "no" else 0
        return
    data = parse_data(detail)
    if data is None:
        return
    if "workers" in data and "failed" in data and "answers" not in data:
        if data.get("gate") is not False:  # failures injected on purpose are not counted
            m.workers += int(data["workers"])
            m.failed_workers += int(data["failed"])
    elif "answers" in data and "malformed" in data:
        m.answers += int(data["answers"])
        m.malformed += int(data["malformed"])
        m.nudged += int(data.get("nudged", 0))
    elif "rounds" in data and "distinct" in data:
        m.overlap_urls += int(data["urls"])
        m.overlap_distinct += int(data["distinct"]) if data["urls"] else 0
    elif "broken" in data and "blocked" in data:
        m.broken += int(data["broken"])
        m.blocked_finalizes += int(data["blocked"])
    elif "uncited" in data:
        m.uncited_sections += int(data["uncited"])
    elif "steer_calls" in data:
        m.steer_trials += 1
        m.steer_applied += bool(g.get("passed"))


@dataclass(frozen=True)
class Criterion:
    name: str
    rule: str
    baseline: str
    v2: str
    passed: bool | None  # None: nothing to measure


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _num(x: float | int | None) -> str:
    return "n/a" if x is None else f"{x}"


def evaluate(
    base: Measures,
    v2: Measures,
    *,
    regression: rpt.Diff | None = None,
) -> list[Criterion]:
    out: list[Criterion] = []

    b, v = base.failed_worker_rate, v2.failed_worker_rate
    out.append(
        Criterion(
            "failed-worker rate",
            "v2 <= half of baseline, or <= 2%",
            _pct(b),
            _pct(v),
            None if v is None or b is None else (v <= 0.5 * b or v <= FAILED_WORKER_FLOOR),
        )
    )
    out.append(
        Criterion(
            "malformed worker answers reaching the orchestrator",
            "v2 == 0",
            f"{base.malformed} of {base.answers}",
            f"{v2.malformed} of {v2.answers}",
            None if not v2.answers else v2.malformed == 0,
        )
    )
    b, v = base.overlap, v2.overlap
    out.append(
        Criterion(
            "cross-worker source-URL overlap per round",
            "v2 >= 30% below baseline",
            _pct(b),
            _pct(v),
            None if b is None or v is None or b == 0 else v <= 0.7 * b,
        )
    )
    b, v = base.unsupported_rate, v2.unsupported_rate
    out.append(
        Criterion(
            "unsupported-claim rate (judge)",
            "v2 >= 25% below baseline (relative)",
            _pct(b),
            _pct(v),
            None if b is None or v is None else (v == 0 if b == 0 else v <= 0.75 * b),
        )
    )
    out.append(
        Criterion(
            "broken citations in the final report",
            "v2 == 0",
            f"{base.broken} ({base.blocked_finalizes} finalize refusals)",
            f"{v2.broken} ({v2.blocked_finalizes} finalize refusals)",
            None if not v2.trials else v2.broken == 0,
        )
    )
    out.append(
        Criterion(
            "steered worker's behaviour changes",
            "v2 >= 80% of steer trials",
            _pct(base.steer_rate),
            _pct(v2.steer_rate),
            None if v2.steer_rate is None else v2.steer_rate >= 0.8,
        )
    )
    if regression is None:
        out.append(Criterion("regression suite pass^k (KTD5)", "no regression", "n/a", "n/a (no --regression-* reports)", None))
    else:
        out.append(
            Criterion(
                "regression suite pass^k (KTD5)",
                "no regression",
                "-",
                "regressed: " + ", ".join(t.task_id for t in regression.regressions) if regression.regressed else "no regression",
                not regression.regressed,
            )
        )
    b, v = base.cost_per_trial, v2.cost_per_trial
    out.append(
        Criterion(
            "cost per Deep Work trial",
            "v2 <= 115% of baseline",
            "n/a" if b is None else f"${b:.4f}",
            "n/a" if v is None else f"${v:.4f}",
            None if not b or v is None else v <= COST_LIMIT * b,
        )
    )
    return out


def render(criteria: Sequence[Criterion], base: Measures, v2: Measures) -> str:
    lines = [
        f"Gate C: {len(v2.tasks)} task(s), baseline {base.trials} trial(s), v2 {v2.trials} trial(s); "
        f"nudged to complete_task: baseline {base.nudged}, v2 {v2.nudged}",
        "",
        "| criterion | rule | baseline | v2 | result |",
        "|---|---|---|---|---|",
    ]
    for c in criteria:
        verdict = "n/a" if c.passed is None else "PASS" if c.passed else "FAIL"
        lines.append(f"| {c.name} | {c.rule} | {c.baseline} | {c.v2} | {verdict} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.gate_c", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("baseline", help="report of the baseline arm (contract v2 off)")
    p.add_argument("v2", help="report of the v2 arm")
    p.add_argument("--regression-baseline", help="regression-suite report, baseline arm")
    p.add_argument("--regression-v2", help="regression-suite report, v2 arm")
    p.add_argument("--noisy", action="append", default=[], metavar="TASK", help="exclude a task flagged noisy (repeatable)")
    p.add_argument("--require-all", action="store_true", help="also exit 1 when a criterion could not be measured")
    args = p.parse_args(argv)
    if bool(args.regression_baseline) != bool(args.regression_v2):
        print("give both --regression-baseline and --regression-v2, or neither", file=sys.stderr)
        return 2
    try:
        base_report, v2_report = rpt.load_report(args.baseline), rpt.load_report(args.v2)
        regression = None
        if args.regression_baseline:
            regression = rpt.diff_reports(rpt.load_report(args.regression_baseline), rpt.load_report(args.regression_v2))
    except (OSError, ValueError) as e:
        print(f"cannot read report: {e}", file=sys.stderr)
        return 2
    base, v2 = measure(base_report, args.noisy), measure(v2_report, args.noisy)
    criteria = evaluate(base, v2, regression=regression)
    print(render(criteria, base, v2))
    failed = [c for c in criteria if c.passed is False]
    unmeasured = [c for c in criteria if c.passed is None]
    print("\nRESULT: " + ("FAIL" if failed else "PASS" if not unmeasured else "PASS (some criteria not measured)"))
    return 1 if failed or (args.require_all and unmeasured) else 0


if __name__ == "__main__":
    raise SystemExit(main())
