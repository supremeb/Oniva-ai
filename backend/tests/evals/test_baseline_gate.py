"""U10 CI gate, offline: the strict comparator, `promote-baseline`, and the committed fake-mode baseline."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evals import cli
from evals import report as rpt
from evals.loader import load_tasks, select_by_tags
from evals.schema import GraderResult, Span, Trace, Trial, TrialResult, Usage

pytestmark = pytest.mark.eval

EVALS = Path(__file__).resolve().parents[2] / "evals"


def trial(task: str, i: int, failing: tuple[str, ...] = (), *, cost: float = 0.1) -> TrialResult:
    graders = (GraderResult("ok-grader", True, True, ""), *(GraderResult(g, False, True, "boom") for g in failing))
    trace = Trace(
        root=Span(kind="subagent", name="root", children=(Span(kind="model", name="m"),)),
        usage_by_role={"root": Usage(input_tokens=10, output_tokens=5, cost_usd=cost)},
    )
    return TrialResult(trial=Trial(task, i), grader_results=graders, trace=trace, latency_s=1.0)


def report(spec: dict[str, list[tuple[str, ...]]], **kw) -> dict:
    """`spec`: task id -> per-trial failing grader ids (empty tuple = pass)."""
    rs = [trial(t, i, f, **kw) for t, trials in spec.items() for i, f in enumerate(trials)]
    return rpt.build_report(rs, name="r", suite=["regression"], mode="fake", k=1)


GREEN = {"a": [()], "b": [()]}


def test_a_fully_failing_task_is_a_regression_with_or_without_strict():
    base = report(GREEN)
    cur = report({"a": [()], "b": [("g",)]})
    for strict in (False, True):
        d = rpt.diff_reports(base, cur, strict=strict)
        assert d.regressed and [t.task_id for t in d.regressions] == ["b"]


def test_identical_runs_do_not_regress():
    base = report(GREEN)
    assert not rpt.diff_reports(base, copy.deepcopy(base), strict=True).regressed


def test_strict_flags_a_newly_failing_grader_inside_the_noise_band():
    base = report({"a": [()] * 40})
    cur = report({"a": [()] * 39 + [("citations-map",)]})  # one flaky-looking failure: -0.025 is within the KTD5 floor
    assert not rpt.diff_reports(base, cur).regressed
    strict = rpt.diff_reports(base, cur, strict=True)
    assert strict.regressed and "citations-map" in strict.strict_failures[0]
    assert "## Strict gate" in rpt.render_diff_markdown(strict, baseline_name="b", current_name="c")


def test_strict_does_not_flag_a_grader_that_already_failed_in_the_baseline():
    base = report({"a": [("known",)] + [()] * 19})
    cur = report({"a": [("known",)] + [()] * 19})
    assert not rpt.diff_reports(base, cur, strict=True).regressed


def test_strict_fails_on_a_task_missing_from_the_run_but_lenient_does_not():
    base = report(GREEN)
    cur = report({"a": [()]})
    assert not rpt.diff_reports(base, cur).regressed
    d = rpt.diff_reports(base, cur, strict=True)
    assert d.regressed and d.strict_failures == ["b: in the baseline but not in this run"]


def test_new_tasks_are_not_a_regression():
    base = report({"a": [()]})
    assert not rpt.diff_reports(base, report(GREEN), strict=True).regressed


def test_cost_regression_is_caught():
    assert rpt.diff_reports(report(GREEN), report(GREEN, cost=0.2), strict=True).regressed


# --- promote -------------------------------------------------------------------------------------------------------


def test_promote_writes_suite_mode_json_without_run_local_paths(tmp_path):
    rep = report(GREEN)
    rep["tasks"]["a"]["results"][0]["trace"] = "/somewhere/results/a-0.json"
    path = rpt.promote_baseline(rep, tmp_path / "baselines", commit="abc123")
    assert path.name == "regression-fake.json"
    stored = rpt.load_report(path)
    assert stored["name"] == "regression-fake" and stored["commit"] == "abc123"
    assert all(r["trace"] is None for e in stored["tasks"].values() for r in e["results"])
    assert stored["tasks"]["a"]["outcomes"] == [1.0]


def test_promote_refuses_failures_and_existing_files(tmp_path):
    bad = report({"a": [("g",)]})
    with pytest.raises(rpt.PromoteError, match="failed trials in a"):
        rpt.promote_baseline(bad, tmp_path)
    assert not list(tmp_path.glob("*.json"))
    rpt.promote_baseline(bad, tmp_path, allow_failures=True)
    with pytest.raises(rpt.PromoteError, match="already exists"):
        rpt.promote_baseline(bad, tmp_path, allow_failures=True)
    rpt.promote_baseline(bad, tmp_path, allow_failures=True, force=True)
    skipped_only = rpt.build_report([TrialResult(trial=Trial("a", 0), status="skipped_budget", error="x")], name="n")
    with pytest.raises(rpt.PromoteError, match="no counted trials"):
        rpt.promote_baseline(skipped_only, tmp_path, name="other")


def test_cli_promote_then_strict_gate_exit_codes(tmp_path, capsys):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    rpt.write_report(report(GREEN), good)
    rpt.write_report(report({"a": [()], "b": [("g",)]}), bad)
    bdir = ["--baselines-dir", str(tmp_path / "baselines")]
    assert cli.main(["promote-baseline", str(good), "--commit", "deadbeef", *bdir], env={}) == 0
    assert (tmp_path / "baselines" / "regression-fake.json").exists()
    assert cli.main(["promote-baseline", str(good), *bdir], env={}) == 1  # exists
    assert cli.main(["promote-baseline", str(bad), "--name", "other", *bdir], env={}) == 1  # failing trials
    assert cli.main(["promote-baseline", str(tmp_path / "nope.json"), *bdir], env={}) == 2
    capsys.readouterr()
    assert cli.main(["compare", str(good), "regression-fake", "--strict", *bdir], env={}) == 0
    assert cli.main(["compare", str(bad), "regression-fake", "--strict", *bdir], env={}) == 1
    assert "RESULT: REGRESSION" in capsys.readouterr().out


# --- the committed baseline ------------------------------------------------------------------------------------------


def test_committed_fake_baseline_covers_exactly_the_regression_tasks_and_all_pass():
    path = EVALS / "baselines" / "regression-fake.json"
    assert path.exists(), "run `python -m evals run --tag regression` and `promote-baseline` (see TESTING.md)"
    base = rpt.load_report(path)
    ids = {t.id for t in select_by_tags(load_tasks(EVALS / "tasks"), ["regression"])}
    assert set(base["tasks"]) == ids, "the regression tag changed: re-promote the baseline deliberately"
    assert base["mode"] == "fake" and base["suite"] == ["regression"]
    assert all(e["pass_hat_k"] == 1.0 and e["trials"] >= 1 for e in base["tasks"].values())
    assert base["commit"]
    json.dumps(base)


# --- the Deep Work driver waits for the closing message --------------------------------------------------------------


async def test_dw_driver_waits_for_open_submissions_before_reading_the_trace(monkeypatch):
    from types import SimpleNamespace

    from evals.drivers import deep_work

    pages = [
        {"entries": [1], "submissions": [{"status": "active"}]},
        {"entries": [1, 2], "submissions": [{"status": "active"}]},
        # all settled, but the entries were read just before the closing message committed (entries are read first)
        {"entries": [1, 2], "submissions": [{"status": "done"}]},
        {"entries": [1, 2, 3], "submissions": [{"status": "done"}]},
    ]
    seen = []

    async def fake_fetch(api, cid, raw=False):
        seen.append(cid)
        return pages[min(len(seen), len(pages)) - 1]

    async def no_sleep(_):
        return None

    monkeypatch.setattr(deep_work, "fetch_trace", fake_fetch)
    monkeypatch.setattr(deep_work.asyncio, "sleep", no_sleep)
    ctx = SimpleNamespace(api=None, remaining=lambda: 100.0)
    assert (await deep_work.fetch_settled_trace(ctx, "c"))["entries"] == [1, 2, 3]
    assert len(seen) == 4
    seen.clear()  # a timed-out run is read once, as it is
    assert (await deep_work.fetch_settled_trace(ctx, "c", wait=False))["entries"] == [1]
    assert len(seen) == 1


# --- the workflow ----------------------------------------------------------------------------------------------------


def test_evals_workflow_parses_filters_paths_and_gates_on_the_committed_baseline():
    import yaml

    wf = yaml.safe_load((EVALS.parents[1] / ".github" / "workflows" / "evals.yml").read_text())
    triggers = wf[True] if True in wf else wf["on"]  # YAML 1.1 reads a bare `on` as True
    for event in ("push", "pull_request"):
        paths = triggers[event]["paths"]
        assert {"engine/src/agent/**", "backend/evals/**", "backend/tests/evals/**", "backend/app/services/deep_work/**"} <= set(paths)
        assert not any(p.startswith("frontend/") for p in paths)  # unrelated paths do not trigger it
    job = wf["jobs"]["regression-fake"]
    assert job["timeout-minutes"] <= 10 and "postgres" in job["services"]
    assert job["env"]["ONIVA_E2E"] == "1"
    runs = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "--tag regression" in runs and "--baseline regression-fake" in runs and "--strict" in runs
    assert "--live" not in runs and "OPENROUTER" not in json.dumps(wf)  # fake mode: no key, nothing spent
    assert (EVALS / "baselines" / "regression-fake.json").exists()
