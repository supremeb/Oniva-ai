"""Gate B: do the context tiers ship? (plan 2026-10-21-001, U17)

Reads one report from `run --suite long_session` (tasks named `<id>@<tier>`; trials carry the per-trial `context`
metrics of `graders.context.trial_metrics`) and compares each tier arm with the baseline arm (`off`) on the same
non-`noisy` tasks. Criteria, from the plan:

1. mean input tokens per turn after turn 30: at least 50% lower;
2. early-fact and early-ID probe accuracy: not below baseline minus 2 points;
3. compactions: down at least 50% (blocking vs background is not in the trace, so all compactions are counted),
   `compaction_failed` zero, runs ended by a context overflow not above baseline;
4. cache-read share of input: not more than 5 points below baseline (needs measured usage: live only);
5. `reread_after_clear`: at most 10% of clears (needs clearing: not applicable to an offload-only arm);
6. no tool result above `offloadTokens` stored verbatim;
7. regression suite pass^3: no regression under KTD5 (a separate run with `--tiers <arm>`; pass it with `--regression`);
8. cost per passing trial: not above baseline.

A criterion is `pass`, `fail`, `n/a` (the data cannot speak: say why) or `pending` (a run that was not supplied).
An arm passes when nothing fails and nothing is pending; the tiers that pass ship and the others stay off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .arms import ARMS, BASELINE_ARM, split_arm
from .graders.context import OFFLOAD_TOKENS

MIN_TOKEN_REDUCTION = 0.5
PROBE_MARGIN = 0.02
CACHE_MARGIN = 0.05
MAX_REREAD_RATE = 0.10
MAX_COMPACTION_RATIO = 0.5

PASS, FAIL, NA, PENDING = "pass", "fail", "n/a", "pending"


@dataclass(frozen=True)
class Criterion:
    id: str
    label: str
    status: str
    baseline: str = ""
    value: str = ""
    detail: str = ""


@dataclass
class ArmVerdict:
    arm: str
    tasks: list[str] = field(default_factory=list)
    trials: int = 0
    criteria: list[Criterion] = field(default_factory=list)

    @property
    def failed(self) -> list[Criterion]:
        return [c for c in self.criteria if c.status == FAIL]

    @property
    def pending(self) -> list[Criterion]:
        return [c for c in self.criteria if c.status == PENDING]

    @property
    def decision(self) -> str:
        """`pass`, `fail` or `incomplete` (nothing failed, but a run is missing)."""
        return FAIL if self.failed else ("incomplete" if self.pending else PASS)


def _trials(report: dict[str, Any], arm: str) -> dict[str, list[dict[str, Any]]]:
    """Trials by base task id for one arm: only trials that ran and carry context metrics."""
    out: dict[str, list[dict[str, Any]]] = {}
    for tid, entry in report["tasks"].items():
        base, a = split_arm(tid)
        if a != arm:
            continue
        rows = [r for r in entry["results"] if r.get("context") and r.get("status") != "skipped_budget"]
        if rows:
            out[base] = rows
    return out


def _ratio(num: float, den: float) -> float | None:
    return None if not den else num / den


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.1%}"


def _cost_per_pass(report: dict[str, Any], arm: str, bases: set[str]) -> tuple[float, int]:
    cost = passes = 0
    for tid, entry in report["tasks"].items():
        base, a = split_arm(tid)
        if a == arm and base in bases:
            cost += entry["cost_usd"]
            passes += entry["passes"]
    return cost, passes


def _probe_rate(rows: list[dict[str, Any]]) -> tuple[int, int]:
    hits = total = 0
    for r in rows:
        for h, t in (r["context"].get("probes") or {}).values():
            hits, total = hits + h, total + t
    return hits, total


def _probe_kind(rows: list[dict[str, Any]], kind: str) -> tuple[int, int]:
    hits = total = 0
    for r in rows:
        h, t = (r["context"].get("probes") or {}).get(kind, [0, 0])
        hits, total = hits + h, total + t
    return hits, total


def evaluate_arm(
    report: dict[str, Any],
    arm: str,
    *,
    baseline_arm: str = BASELINE_ARM,
    offload_tokens: int = OFFLOAD_TOKENS,
    regression_regressed: bool | None = None,
) -> ArmVerdict:
    base_by_task, arm_by_task = _trials(report, baseline_arm), _trials(report, arm)
    noisy = {
        t for group in (base_by_task, arm_by_task) for t, rows in group.items() if any(r["context"].get("noisy") for r in rows)
    }
    common = sorted((set(base_by_task) & set(arm_by_task)) - noisy)
    verdict = ArmVerdict(arm=arm, tasks=common)
    if not common:
        verdict.criteria.append(Criterion("data", "tasks in both arms", FAIL, detail="no non-noisy task has trials in both arms"))
        return verdict
    base_rows = [r for t in common for r in base_by_task[t]]
    arm_rows = [r for t in common for r in arm_by_task[t]]
    verdict.trials = len(arm_rows)
    add = verdict.criteria.append

    # 1. tokens per turn
    b = _mean([r["context"]["mean_tokens_after"] for r in base_rows if r["context"].get("mean_tokens_after") is not None])
    a = _mean([r["context"]["mean_tokens_after"] for r in arm_rows if r["context"].get("mean_tokens_after") is not None])
    sources = {r["context"].get("token_source") for r in base_rows + arm_rows}
    if b is None or a is None:
        add(Criterion("tokens", "input tokens/turn after turn 30 down >= 50%", NA, detail="no sampled turn after turn 30"))
    else:
        cut = 1 - a / b if b else 0.0
        add(Criterion("tokens", "input tokens/turn after turn 30 down >= 50%", PASS if cut >= MIN_TOKEN_REDUCTION else FAIL,
                      f"{b:,.0f}", f"{a:,.0f}", f"{cut:.1%} lower ({'/'.join(sorted(map(str, sources)))})"))

    # 2. probes
    for kind, label in (("fact", "early-fact probe accuracy"), ("id", "early-ID probe accuracy")):
        bh, bt = _probe_kind(base_rows, kind)
        ah, at = _probe_kind(arm_rows, kind)
        if not bt or not at:
            add(Criterion(f"probe-{kind}", f"{label} within 2 points", NA, detail="no probes answered"))
            continue
        rb, ra = bh / bt, ah / at
        add(Criterion(f"probe-{kind}", f"{label} >= baseline - 2 points", PASS if ra >= rb - PROBE_MARGIN else FAIL,
                      f"{bh}/{bt} ({_pct(rb)})", f"{ah}/{at} ({_pct(ra)})"))

    # 3. compactions
    bc = sum(r["context"].get("compactions", 0) for r in base_rows)
    ac = sum(r["context"].get("compactions", 0) for r in arm_rows)
    if bc == 0:
        add(Criterion("compactions", "compactions down >= 50%", NA if ac == 0 else FAIL, "0", str(ac),
                      "the baseline never compacted, so there is nothing to reduce" if ac == 0 else "tiers compacted where the baseline did not"))
    else:
        add(Criterion("compactions", "compactions down >= 50%", PASS if ac <= MAX_COMPACTION_RATIO * bc else FAIL,
                      str(bc), str(ac), "all causes counted (blocking is not in the trace)"))
    failed = sum(r["context"].get("compaction_failed", 0) for r in arm_rows)
    add(Criterion("compaction-failed", "compaction_failed is 0", PASS if failed == 0 else FAIL, value=str(failed)))
    bo = sum(r["context"].get("overflow_failures", 0) for r in base_rows)
    ao = sum(r["context"].get("overflow_failures", 0) for r in arm_rows)
    add(Criterion("overflow", "context-overflow failures not above baseline", PASS if ao <= bo else FAIL, str(bo), str(ao),
                  "a retried overflow leaves no entry, so only runs that ended in one are counted"))

    # 4. cache share
    def share(rows: list[dict[str, Any]]) -> float | None:
        prompt = sum(r["context"].get("prompt_tokens_after") or 0 for r in rows)
        weighted = sum((r["context"].get("cache_read_share_after") or 0.0) * (r["context"].get("prompt_tokens_after") or 0) for r in rows)
        return weighted / prompt if prompt else None

    sb, sa = share(base_rows), share(arm_rows)
    if "estimate" in sources or sb is None or sa is None or sb == 0 == sa:
        add(Criterion("cache", "cache-read share not > 5 points below baseline", NA, detail="needs measured usage (live runs)"))
    else:
        add(Criterion("cache", "cache-read share not > 5 points below baseline", PASS if sa >= sb - CACHE_MARGIN else FAIL,
                      _pct(sb), _pct(sa)))

    # 5. rereads
    clears = sum(r["context"].get("clears", 0) for r in arm_rows)
    rereads = sum(r["context"].get("rereads", 0) for r in arm_rows)
    if clears == 0:
        add(Criterion("reread", "reread_after_clear <= 10% of clears", NA, detail="nothing was cleared in this arm"))
    else:
        add(Criterion("reread", "reread_after_clear <= 10% of clears", PASS if rereads / clears <= MAX_REREAD_RATE else FAIL,
                      value=f"{rereads}/{clears} ({_pct(rereads / clears)})"))

    # 6. verbatim
    biggest = max((r["context"].get("max_stored_result_tokens", 0) for r in arm_rows), default=0)
    add(Criterion("verbatim", f"no tool result above {offload_tokens} tokens verbatim",
                  PASS if biggest <= offload_tokens else FAIL, value=f"{biggest:,} tokens at most"))

    # 7. regression suite
    if regression_regressed is None:
        add(Criterion("regression", "regression suite pass^3, no regression", PENDING,
                      detail=f"run `run --suite regression --tiers {arm} -k 3 --baseline <phase A baseline>` and pass it with --regression"))
    else:
        add(Criterion("regression", "regression suite pass^3, no regression", FAIL if regression_regressed else PASS))

    # 8. cost per passing trial
    bset = set(common)
    bcost, bpass = _cost_per_pass(report, baseline_arm, bset)
    acost, apass = _cost_per_pass(report, arm, bset)
    if not bpass:
        add(Criterion("cost", "cost per passing trial not above baseline", NA, detail="the baseline arm had no passing trial"))
    elif not apass:
        add(Criterion("cost", "cost per passing trial not above baseline", FAIL, f"${bcost / bpass:.4f}", "no passing trial"))
    else:
        add(Criterion("cost", "cost per passing trial not above baseline", PASS if acost / apass <= bcost / bpass else FAIL,
                      f"${bcost / bpass:.4f}", f"${acost / apass:.4f}"))
    return verdict


def evaluate(
    report: dict[str, Any],
    *,
    baseline_arm: str = BASELINE_ARM,
    arms: tuple[str, ...] | None = None,
    regressions: dict[str, bool] | None = None,
    offload_tokens: int = OFFLOAD_TOKENS,
) -> list[ArmVerdict]:
    """A verdict per non-baseline arm present in `report` (or in `arms`)."""
    present = {a for tid in report["tasks"] if (a := split_arm(tid)[1]) is not None}
    wanted = arms or tuple(a for a in ARMS if a != baseline_arm)
    return [
        evaluate_arm(report, arm, baseline_arm=baseline_arm, offload_tokens=offload_tokens,
                     regression_regressed=(regressions or {}).get(arm))
        for arm in wanted
        if arm in present
    ]


def render_markdown(report: dict[str, Any], verdicts: list[ArmVerdict], *, baseline_arm: str = BASELINE_ARM) -> str:
    lines = [f"# Gate B: {report['name']}", "", f"- mode `{report['mode']}`, k={report['k']}, baseline arm `{baseline_arm}`"]
    if report["mode"] != "live":
        lines.append("- **fake mode: the mechanics only.** Token counts are rebuilt from entries; quality needs the live gate.")
    if not verdicts:
        return "\n".join(lines + ["", "No tier arm found in this report (task ids like `<task>@<tier>`)."]) + "\n"
    for v in verdicts:
        lines += ["", f"## Arm `{v.arm}`: {v.decision.upper()}", "",
                  f"- {len(v.tasks)} task(s), {v.trials} trial(s): {', '.join(v.tasks) or '-'}", "",
                  "| criterion | status | baseline | arm | note |", "|---|---|---|---|---|"]
        lines += [f"| {c.label} | {c.status} | {c.baseline or '-'} | {c.value or '-'} | {c.detail} |" for c in v.criteria]
    passing = [v.arm for v in verdicts if v.decision == PASS]
    lines += ["", f"Ship: {', '.join(passing) if passing else 'nothing yet'}. Tiers that do not pass stay off."]
    return "\n".join(lines) + "\n"
