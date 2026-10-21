"""U24: Gate C is computed from two stored reports, offline (no stack, no model)."""

import json
from pathlib import Path

import pytest

from evals import gate_c
from evals import report as rpt
from evals.graders.subagents import with_data
from evals.report import REPORT_VERSION


def g(gid: str, data: dict | None = None, *, passed: bool = True, text: str = "t") -> dict:
    return {"id": gid, "passed": passed, "gating": True, "detail": with_data(text, data) if data is not None else text}


def trial(graders: list[dict], *, cost: float = 0.1, passed: bool = True) -> dict:
    return {"index": 0, "status": "ok", "passed": passed, "error": None, "cost_usd": cost, "latency_s": 1.0,
            "failed_graders": [], "trace": None, "graders": graders}


def report(name: str, tasks: dict[str, list[dict]]) -> dict:
    out = {"version": REPORT_VERSION, "name": name, "suite": ["dw_subagents"], "mode": "live", "k": 1, "tasks": {}}
    for tid, trials in tasks.items():
        out["tasks"][tid] = {"trials": len(trials), "cost_usd": sum(t["cost_usd"] for t in trials), "results": trials}
    return out


def arm(*, failed, workers=10, malformed, answers=20, urls, distinct, judge, broken=0, steer=(True, True, True, True, False)):
    """One synthetic arm: three tasks (a worker/answer task, an overlap task, a steer task)."""
    return report("arm", {
        "t-a": [trial([g("workers-ok", {"workers": workers, "failed": failed}),
                       g("malformed", {"answers": answers, "malformed": malformed, "nudged": 1}),
                       g("claims-supported", text=judge),
                       g("citations-clean", {"broken": broken, "blocked": 0, "sources": 4})])],
        "t-o": [trial([g("url-overlap", {"rounds": [], "urls": urls, "distinct": distinct, "later_urls": 0, "repeated": 0})])],
        "t-s": [trial([g("steered", {"steer_calls": 1, "steer_ok": 1, "delivered": ok, "changed": ok}, passed=ok)]) for ok in steer],
    })


BASE = arm(failed=4, malformed=3, urls=100, distinct=60, judge="judge claim_supported: 4 of 10 no (x)")
GOOD = arm(failed=1, malformed=0, urls=100, distinct=80, judge="judge claim_supported: 1 of 10 no (x)")


def by_name(criteria):
    return {c.name.split(" (")[0]: c for c in criteria}


def test_measure_reads_the_graders_recorded_numbers():
    m = gate_c.measure(BASE)
    assert (m.workers, m.failed_workers, m.answers, m.malformed, m.nudged) == (10, 4, 20, 3, 1)
    assert (m.overlap_urls, m.overlap_distinct, m.overlap) == (100, 60, pytest.approx(0.4))
    assert (m.claims, m.unsupported, m.unsupported_rate) == (10, 4, pytest.approx(0.4))
    assert (m.steer_trials, m.steer_applied, m.steer_rate) == (5, 4, pytest.approx(0.8))
    assert m.trials == 7 and m.cost_per_trial == pytest.approx(0.1)


def test_a_clean_v2_arm_passes_every_measurable_criterion():
    criteria = gate_c.evaluate(gate_c.measure(BASE), gate_c.measure(GOOD))
    verdicts = {c.name: c.passed for c in criteria}
    assert verdicts["failed-worker rate"] is True  # 10% against 40%
    assert verdicts["malformed worker answers reaching the orchestrator"] is True
    assert verdicts["cross-worker source-URL overlap per round"] is True  # 20% against 40%: half
    assert verdicts["unsupported-claim rate (judge)"] is True  # 10% against 40%
    assert verdicts["broken citations in the final report"] is True
    assert verdicts["steered worker's behaviour changes"] is True  # 4 of 5 is the 80% line
    assert verdicts["regression suite pass^k (KTD5)"] is None  # no regression reports given
    assert verdicts["cost per Deep Work trial"] is True


@pytest.mark.parametrize(
    ("changes", "criterion"),
    [
        ({"failed": 3}, "failed-worker rate"),  # 30% is not half of 40%
        ({"malformed": 1}, "malformed worker answers reaching the orchestrator"),
        ({"distinct": 70}, "cross-worker source-URL overlap per round"),  # 30% against 40%: only 25% lower
        ({"judge": "judge claim_supported: 4 of 10 no (x)"}, "unsupported-claim rate (judge)"),  # 40%: no better
        ({"broken": 1}, "broken citations in the final report"),
        ({"steer": (True, True, True, False, False)}, "steered worker's behaviour changes"),
    ],
)
def test_each_criterion_fails_on_its_own_threshold(changes, criterion):
    args = {"failed": 1, "malformed": 0, "urls": 100, "distinct": 80, "judge": "judge claim_supported: 1 of 10 no (x)"}
    args.update(changes)
    steer = args.pop("steer", (True,) * 5)
    v2 = arm(failed=args["failed"], malformed=args["malformed"], urls=args["urls"], distinct=args["distinct"],
             judge=args["judge"], broken=changes.get("broken", 0), steer=steer)
    verdicts = {c.name: c.passed for c in gate_c.evaluate(gate_c.measure(BASE), gate_c.measure(v2))}
    assert verdicts[criterion] is False
    assert [n for n, ok in verdicts.items() if ok is False] == [criterion]


def test_two_percent_failed_worker_floor_applies_when_the_baseline_is_tiny():
    base = gate_c.measure(arm(failed=1, workers=100, malformed=0, urls=10, distinct=10, judge="skipped: judges are off"))
    v2 = gate_c.measure(arm(failed=2, workers=100, malformed=0, urls=10, distinct=10, judge="skipped: judges are off"))
    assert gate_c.evaluate(base, v2)[0].passed is True  # 2% is at most 2%
    worse = gate_c.measure(arm(failed=3, workers=100, malformed=0, urls=10, distinct=10, judge="skipped: judges are off"))
    assert gate_c.evaluate(base, worse)[0].passed is False


def test_unmeasurable_criteria_are_na_not_pass():
    empty = gate_c.measure(report("e", {"t": [trial([g("other")])]}))
    verdicts = {c.name: c.passed for c in gate_c.evaluate(empty, empty)}
    assert verdicts["failed-worker rate"] is None and verdicts["unsupported-claim rate (judge)"] is None
    assert verdicts["malformed worker answers reaching the orchestrator"] is None
    # a judge that was skipped (fake mode) contributes no verdicts
    skipped = gate_c.measure(arm(failed=0, malformed=0, urls=0, distinct=0, judge="skipped: judges are off"))
    assert skipped.claims == 0 and skipped.unsupported_rate is None


def test_noisy_tasks_and_skipped_trials_are_left_out():
    m = gate_c.measure(BASE, exclude=["t-a"])
    assert m.workers == 0 and "t-a" not in m.tasks
    rep = report("r", {"t": [trial([g("workers-ok", {"workers": 2, "failed": 1})])]})
    rep["tasks"]["t"]["results"][0]["status"] = "skipped_budget"
    assert gate_c.measure(rep).trials == 0


def test_cost_limit_is_115_percent():
    cheap = report("c", {"t": [trial([], cost=1.0)]})
    ok = report("c", {"t": [trial([], cost=1.15)]})
    over = report("c", {"t": [trial([], cost=1.16)]})
    cost = lambda a, b: gate_c.evaluate(gate_c.measure(a), gate_c.measure(b))[-1].passed  # noqa: E731
    assert cost(cheap, ok) is True and cost(cheap, over) is False


def test_cli_prints_a_table_and_exits_by_the_verdicts(tmp_path: Path, capsys):
    paths = {}
    for name, rep in (("base", BASE), ("good", GOOD), ("bad", arm(failed=4, malformed=2, urls=100, distinct=60, judge="x"))):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(rep))
    assert gate_c.main([str(paths["base"]), str(paths["good"])]) == 0
    out = capsys.readouterr().out
    assert "| failed-worker rate |" in out and "RESULT: PASS (some criteria not measured)" in out
    assert gate_c.main([str(paths["base"]), str(paths["good"]), "--require-all"]) == 1
    assert gate_c.main([str(paths["base"]), str(paths["bad"])]) == 1
    assert "RESULT: FAIL" in capsys.readouterr().out
    assert gate_c.main([str(paths["base"]), str(paths["good"]), "--regression-v2", str(paths["good"])]) == 2
    assert gate_c.main([str(tmp_path / "missing.json"), str(paths["good"])]) == 2


def test_regression_reports_feed_the_pass_hat_k_criterion(tmp_path: Path):
    def reg(rate_outcomes):
        tasks = {f"r{i}": {"trials": 3, "cost_usd": 0.0, "k": 3, "pass_at_k": 1.0, "pass_hat_k": float(all(o)),
                           "outcomes": [1.0 if x else 0.0 for x in o], "results": []} for i, o in enumerate(rate_outcomes)}
        return {"version": REPORT_VERSION, "name": "reg", "suite": ["regression"], "mode": "live", "k": 3,
                "totals": {}, "tasks": tasks}

    same = reg([(True, True, True)] * 6)
    worse = reg([(False, False, False)] * 3 + [(True, True, True)] * 3)
    ok = gate_c.evaluate(gate_c.measure(BASE), gate_c.measure(GOOD), regression=rpt.diff_reports(same, same))
    assert by_name(ok)["regression suite pass^k"].passed is True
    bad = gate_c.evaluate(gate_c.measure(BASE), gate_c.measure(GOOD), regression=rpt.diff_reports(same, worse))
    assert by_name(bad)["regression suite pass^k"].passed is False
