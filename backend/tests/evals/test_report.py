"""Reports: numbers, markdown, baseline diff, optional Braintrust push (no engine, no network)."""

from __future__ import annotations

import copy
import json

import pytest

from evals import report as rpt
from evals.braintrust_push import push_report
from evals.schema import GraderResult, Span, Trace, Trial, TrialResult, Usage


def _trace(cost: float = 0.1, cache: int = 50, compaction: bool = False) -> Trace:
    model = Span(kind="model", name="m", usage=Usage(input_tokens=40, output_tokens=10))
    kids = [model, Span(kind="tool", name="web_search"), Span(kind="tool", name="web_search")]
    if compaction:
        kids.append(Span(kind="compaction", name="compaction"))
    return Trace(
        root=Span(kind="subagent", name="root", children=tuple(kids)),
        usage_by_role={"root": Usage(input_tokens=50, output_tokens=30, cache_read_tokens=cache, cost_usd=cost)},
    )


def _trial(task: str, i: int, ok: bool, *, cost: float = 0.1, latency: float = 1.0, **kw) -> TrialResult:
    graders = (GraderResult("g1", ok, True, "" if ok else "boom"),)
    return TrialResult(trial=Trial(task, i), grader_results=graders, trace=_trace(cost, **kw), latency_s=latency)


def _results(pass_a: list[bool], pass_b: list[bool], **kw) -> list[TrialResult]:
    return [_trial("a", i, ok, **kw) for i, ok in enumerate(pass_a)] + [
        _trial("b", i, ok, **kw) for i, ok in enumerate(pass_b)
    ]


def test_synthetic_results_render_the_expected_numbers():
    rs = _results([True, True, False], [False, False, False], compaction=True)
    rs.append(TrialResult(trial=Trial("b", 3), status="skipped_budget", error="skipped_budget"))
    rep = rpt.build_report(rs, name="n", suite=["smoke"], k=2, spent_usd=0.6)
    a, b = rep["tasks"]["a"], rep["tasks"]["b"]
    assert (a["trials"], a["passes"], a["k"]) == (3, 2, 2)
    assert a["pass_at_k"] == pytest.approx(1.0)  # 1 - C(1,2)/C(3,2)
    assert a["pass_hat_k"] == pytest.approx(1 / 3)  # C(2,2)/C(3,2)
    assert a["cost_usd"] == pytest.approx(0.3) and a["cost_per_pass_usd"] == pytest.approx(0.15)
    assert a["tool_calls"] == {"web_search": 6} and a["compactions"] == 3
    assert a["cache_read_share"] == pytest.approx(50 / 100)
    assert (b["trials"], b["skipped"], b["pass_at_k"], b["cost_per_pass_usd"]) == (3, 1, 0.0, None)
    assert a["usage_by_purpose"]["generation_tokens"] == 150 and a["usage_by_purpose"]["compaction_tokens"] == 90
    assert rep["totals"]["pass_at_k"] == pytest.approx(0.5)
    assert rep["spent_usd"] == 0.6

    md = rpt.render_markdown(rep)
    assert "| a | 2/3 |" in md and "pass@k 0.50" in md and "b#0" in md and "boom" not in md.split("## Tasks")[0]
    assert "g1" in md  # failed trials list their gating graders


def test_percentiles_and_file_roundtrip(tmp_path):
    assert rpt.percentile([1, 2, 3, 4], 50) == 2 and rpt.percentile([1, 2, 3, 4], 95) == 4 and rpt.percentile([], 50) == 0
    rep = rpt.build_report(_results([True], [True]), name="x")
    path = rpt.write_report(rep, tmp_path / "r" / "x.json")
    assert rpt.load_report(path) == json.loads(path.read_text())
    (tmp_path / "bad.json").write_text("{}")
    with pytest.raises(ValueError):
        rpt.load_report(tmp_path / "bad.json")
    first = rpt.result_path(tmp_path, ["smoke"], "fake", today="2026-10-21")
    assert first.name == "2026-10-21-smoke-fake.json"
    first.write_text("{}")
    assert rpt.result_path(tmp_path, ["smoke"], "fake", today="2026-10-21").name.endswith("fake-2.json")


def test_failed_traces_are_stored_and_linked(tmp_path):
    bad = _trial("a", 0, False)
    bad = TrialResult(**{**bad.__dict__, "raw": {"entries": []}})
    files = rpt.write_traces([bad, _trial("a", 1, True)], tmp_path)
    assert list(files) == [("a", 0)]
    rep = rpt.build_report([bad], name="n", trace_files=files)
    assert "[trace](" in rpt.render_markdown(rep)


def test_compare_flags_a_seeded_drop_and_passes_within_noise():
    base = rpt.build_report(_results([True] * 10, [True] * 10), name="base")
    drop = rpt.build_report(_results([True] * 10, [True] * 5 + [False] * 5), name="cur")
    diff = rpt.diff_reports(base, drop)
    assert diff.regressed and [t.task_id for t in diff.regressions] == ["b"]
    assert diff.regressions[0].failing_graders == ("g1",)
    md = rpt.render_diff_markdown(diff, baseline_name="base", current_name="cur")
    assert "REGRESSION (pass" in md and "`b`" in md

    same = rpt.diff_reports(base, copy.deepcopy(base))
    assert not same.regressed and same.suite is not None and same.suite.pass_delta == 0


def test_compare_flags_a_cost_regression_and_reports_missing_and_new_tasks():
    base = rpt.build_report(_results([True] * 4, [True] * 4, cost=0.10), name="base")
    pricey = rpt.build_report(_results([True] * 4, [True] * 4, cost=0.20), name="cur")
    assert rpt.diff_reports(base, pricey).regressed
    only_a = rpt.build_report([_trial("a", 0, True), _trial("c", 0, True)], name="cur")
    diff = rpt.diff_reports(base, only_a)
    assert diff.missing == ["b"] and diff.new == ["c"]


def test_braintrust_push_is_skipped_without_a_key_and_never_raises():
    rep = rpt.build_report(_results([True], [False]), name="n")
    res = push_report(rep, api_key="", project="p")
    assert not res.pushed and "BRAINTRUST_API_KEY" in res.reason

    def boom(**_):
        raise RuntimeError("network down")

    res = push_report(rep, api_key="k", project="p", init=boom)
    assert not res.pushed and "network down" in res.reason


def test_braintrust_push_logs_one_record_per_task():
    logged, calls = [], {}

    class Exp:
        def log(self, **kw):
            logged.append(kw)

        def flush(self):
            calls["flushed"] = True

    def init(**kw):
        calls["init"] = kw
        return Exp()

    rep = rpt.build_report(_results([True], [False]), name="n")
    res = push_report(rep, api_key="k", project="proj", init=init)
    assert res.pushed and res.records == 2 and calls["flushed"]
    assert calls["init"]["project"] == "proj" and calls["init"]["experiment"] == "n"
    assert [r["input"]["task_id"] for r in logged] == ["a", "b"]
    assert logged[0]["scores"]["pass_at_k"] == 1.0
