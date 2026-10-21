"""Integration glue: judges in the runner, production citation helpers, `calibrate` and `record-web`. No network."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.deep_work import engine_tools
from evals import cli, replay, runner, web_record
from evals.budget import BudgetMeter
from evals.graders import GradeContext
from evals.graders import deep_work as dw
from evals.graders import judge as j
from evals.loader import load_task
from evals.schema import GraderSpec, Span, Trace

CORPUS = Path(runner.__file__).parent / "tasks"
JUDGE = GraderSpec(id="cs", type="judge", gating=True, params={"judge": "claim_supported"})


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def verdict_transport(calls=None):
    async def t(call: j.JudgeCall) -> j.JudgeReply:
        if calls is not None:
            calls.append(call)
        yes = "supported" in call.user and "unsupported" not in call.user
        return j.JudgeReply(json.dumps({"verdict": "yes" if yes else "no", "reason": "r"}), cost_usd=0.002)

    return t


# --- gap 1: judges in the runner -------------------------------------------------------------------------------------

LEARNINGS = [
    {"fact": "Team: 29 EUR per seat", "source_url": "https://a.test/p"},
    {"fact": "Team: 29 EUR billed monthly", "source_url": "https://a.test/p"},  # same URL: still source 1
    {"fact": "Enterprise by quote", "source_url": "https://a.test/e"},
]
REPORT = "The Team plan costs 29 EUR [1]. Enterprise is by quote [2]."


def gctx():
    return GradeContext(trace=Trace(root=Span("model", "root")), files={"report.md": REPORT, "learnings": LEARNINGS})


def task_with_judge():
    return replace(load_task(CORPUS / "chat-fake-browse.yaml"), graders=(JUDGE,))


def test_judges_off_gives_skipped_non_gating_passing_results():
    out = run(runner.judge_trace(task_with_judge(), gctx(), None, why_not="fake mode"))
    assert len(out) == 1 and out[0].passed and not out[0].gating and out[0].detail == "skipped: fake mode"
    assert runner.skipped_judges(task_with_judge(), "x")[0].grader_id == "cs"


def test_judges_run_cached_and_metered(tmp_path):
    calls: list[j.JudgeCall] = []
    meter = BudgetMeter(1.0)
    cache = j.JudgeCache(tmp_path / "cache")
    jr = j.JudgeRunner(verdict_transport(calls), "m/1", cache=cache, meter=meter)
    first = run(runner.judge_trace(task_with_judge(), gctx(), jr))
    assert len(first) == 1 and first[0].grader_id == "cs" and "no" in first[0].detail
    assert len(calls) == 2 and meter.spent == pytest.approx(0.004)
    # a second scoring of the same trace is free
    second = run(runner.judge_trace(task_with_judge(), gctx(), j.JudgeRunner(verdict_transport(calls), "m/1", cache=cache, meter=meter)))
    assert len(calls) == 2 and meter.spent == pytest.approx(0.004) and second[0].detail == first[0].detail
    assert (tmp_path / "cache").is_dir()


def test_spent_budget_turns_a_judge_into_an_error_not_a_pass():
    meter = BudgetMeter(0.001)
    meter.charge(0.001)
    jr = j.JudgeRunner(verdict_transport(), "m/1", cache=j.JudgeCache(None), meter=meter)
    out = run(runner.judge_trace(task_with_judge(), gctx(), jr))
    assert not out[0].passed and "budget" in out[0].detail


class _World:
    mode = "fake"


def _patch_stack(monkeypatch):
    seen = {}

    @contextlib.contextmanager
    def stack(mode, **kw):
        yield _World()

    async def on_stack(ew, group, k, meter, http, timeout_s=None, *, judges=None):
        seen["judges"] = judges
        return {(t.id, i): runner.skipped(t, i, "fake") for t in group for i in range(k)}

    monkeypatch.setattr(runner, "eval_stack", stack)
    monkeypatch.setattr(runner, "run_on_stack", on_stack)
    return seen


async def test_fake_mode_never_builds_a_judge_runner(monkeypatch):
    seen = _patch_stack(monkeypatch)
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "m/1")
    await runner.run_suite([task_with_judge()], mode="fake")
    assert seen["judges"] is None


async def test_an_injected_runner_is_used_and_metered_in_fake_mode(monkeypatch):
    seen = _patch_stack(monkeypatch)
    jr = j.JudgeRunner(verdict_transport(), "m/1", cache=j.JudgeCache(None))
    meter = BudgetMeter(5)
    await runner.run_suite([task_with_judge()], mode="fake", meter=meter, judges=jr)
    assert seen["judges"] is jr and jr.meter is meter


async def test_live_mode_with_judges_and_no_judge_model_refuses_before_booting(monkeypatch):
    monkeypatch.setenv("EVAL_OPENROUTER_API_KEY", "present-but-never-used")
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)

    def boom(*a, **k):
        raise AssertionError("a stack was booted")

    monkeypatch.setattr(runner, "eval_stack", boom)
    with pytest.raises(j.JudgeConfigError, match="EVAL_JUDGE_MODEL"):
        await runner.run_suite([task_with_judge()], mode="live", meter=BudgetMeter(5))


async def test_live_mode_with_judges_disabled_needs_no_judge_model(monkeypatch):
    seen = _patch_stack(monkeypatch)
    monkeypatch.setenv("EVAL_OPENROUTER_API_KEY", "present-but-never-used")
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    monkeypatch.setattr(runner, "require_live_key", lambda: None)
    await runner.run_suite([task_with_judge()], mode="live", meter=BudgetMeter(5), judges=False)
    assert seen["judges"] is None


def test_cli_run_no_judges_flag(monkeypatch, tmp_path):
    from tests.evals.test_cli import _fake_run

    fake = _fake_run(True)
    monkeypatch.setattr(runner, "run_suite", fake)
    d = ["--results-dir", str(tmp_path / "r"), "--baselines-dir", str(tmp_path / "b"), "--tag", "smoke"]
    assert cli.main(["run", *d], env={}) == 0
    assert cli.main(["run", "--no-judges", *d], env={}) == 0
    assert fake.judges == [None, False]


# --- gap 2: production citation helpers ------------------------------------------------------------------------------


def test_graders_use_the_production_citation_parser():
    md = "A [1]. B [2, 3]. `[9]` and\n```\n[8]\n```\nC [4](http://x.test). D [5-6]."
    assert dw.cited_indexes(md) == sorted(set(engine_tools._cited_indexes(md)))
    assert dw._cited_indexes is engine_tools._cited_indexes
    assert dw.lint_citations is engine_tools.lint_citations
    assert 9 not in dw.cited_indexes(md) and 8 not in dw.cited_indexes(md) and 4 not in dw.cited_indexes(md)


def test_source_numbering_comes_from_build_sources_section():
    assert dw.numbered_sources(LEARNINGS) == {1: "https://a.test/p", 2: "https://a.test/e"}
    assert dw.source_count(LEARNINGS) == 2
    assert dw.numbered_sources([{"fact": "x. y", "source_url": "https://a.test/p?q=(1)"}]) == {
        1: "https://a.test/p?q=(1)"
    }


def test_citation_map_uses_production_lint():
    spec = GraderSpec(id="c", type="citation_map")
    ok = dw.citation_map(gctx(), {})
    assert ok["passed"]
    bad = dw.citation_map(
        GradeContext(trace=gctx().trace, files={"report.md": "x [3].", "learnings": LEARNINGS}), {}
    )
    assert not bad["passed"] and "[3]" in bad["detail"] and spec.type == "citation_map"


def test_claim_supported_numbers_sources_like_production():
    calls: list[j.JudgeCall] = []
    jr = j.JudgeRunner(verdict_transport(calls), "m/1", cache=j.JudgeCache(None))
    run(jr.grade(GraderSpec(id="cs", type="judge", params={"judge": "claim_supported"}), gctx()))
    by_claim = {c.user.split("<EVIDENCE>")[0]: c.user for c in calls}
    second = next(u for k, u in by_claim.items() if "Enterprise" in k)
    assert "Enterprise by quote" in second and "29 EUR" not in second.split("<EVIDENCE>")[1]
    first = next(u for k, u in by_claim.items() if "Team plan" in k)
    assert "billed monthly" in first  # both learnings of source 1


# --- gap 3: calibrate ------------------------------------------------------------------------------------------------


def _labelled(calib: Path, n=20):
    calib.mkdir()
    rows = []
    for i in range(n):
        rows.append({"id": f"y{i}", "label": "yes", "input": {"claim": f"supported {i}", "evidence": "e"}})
        rows.append({"id": f"n{i}", "label": "no", "input": {"claim": f"unsupported {i}", "evidence": "e"}})
    (calib / "claim_supported.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


ENV = {"EVAL_JUDGE_MODEL": "m/1", "EVAL_MAX_USD": "5"}


def test_calibrate_passes_writes_record_and_gates(tmp_path, capsys):
    calib = tmp_path / "calib"
    _labelled(calib)
    code = cli.main(
        ["calibrate", "claim_supported", "--calibration-dir", str(calib), "--no-cache"],
        env=ENV,
        judge_transport=verdict_transport(),
    )
    assert code == 0 and "CALIBRATED" in capsys.readouterr().out
    rec = json.loads((calib / "claim_supported.record.json").read_text())
    assert rec["passed"] and rec["tpr"] == 1.0 and rec["tnr"] == 1.0
    assert j.gating_status("claim_supported", "m/1", calibration_dir=calib)[0]


def test_calibrate_below_the_gate_exits_non_zero(tmp_path, capsys):
    calib = tmp_path / "calib"
    _labelled(calib)

    async def always_yes(call):
        return j.JudgeReply('{"verdict": "yes", "reason": "r"}', cost_usd=0.001)

    code = cli.main(
        ["calibrate", "claim_supported", "--calibration-dir", str(calib), "--no-cache"],
        env=ENV,
        judge_transport=always_yes,
    )
    assert code == 1 and "NOT CALIBRATED" in capsys.readouterr().out
    assert not j.gating_status("claim_supported", "m/1", calibration_dir=calib)[0]


def test_calibrate_refuses_without_a_cap_or_a_labelled_set(tmp_path, capsys):
    calib = tmp_path / "calib"
    _labelled(calib)
    args = ["calibrate", "claim_supported", "--calibration-dir", str(calib), "--no-cache"]
    assert cli.main(args, env={"EVAL_JUDGE_MODEL": "m/1"}, judge_transport=verdict_transport()) == 2
    assert "EVAL_MAX_USD" in capsys.readouterr().err
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["calibrate", "claim_supported", "--calibration-dir", str(empty)], env=ENV, judge_transport=verdict_transport()) == 2
    assert cli.main(["calibrate", "--calibration-dir", str(calib)], env=ENV, judge_transport=verdict_transport()) == 2


def test_calibrate_max_usd_flag_overrides_the_env(tmp_path):
    calib = tmp_path / "calib"
    _labelled(calib)
    code = cli.main(
        ["calibrate", "claim_supported", "--max-usd", "0.0001", "--calibration-dir", str(calib), "--no-cache"],
        env=ENV,
        judge_transport=verdict_transport(),
    )
    assert code == 1  # the cap ran out: budget errors count as wrong answers


# --- gap 3: record-web -----------------------------------------------------------------------------------------------


class FakeWeb:
    def __init__(self):
        self.calls = []

    async def __call__(self, tool, args):
        self.calls.append((tool, args))
        if args.get("query") == "boom":
            return {"error": "provider down"}
        return {"url": args.get("url"), "results": [{"url": "https://r.test/1", "title": "t"}], "query": args.get("query")}


def test_record_web_writes_replayable_fixtures_keyed_by_request(tmp_path, capsys):
    web = FakeWeb()
    d = tmp_path / "web"
    code = cli.main(
        ["record-web", "--dir", str(d), "--search", "  Acme  PRICING ", "--url", "https://a.test/p"], env={}, web_call=web
    )
    assert code == 0 and len(web.calls) == 2
    key = replay.request_key("web_search", {"query": "acme pricing"})
    assert replay.read_fixture(d, key)["results"][0]["url"] == "https://r.test/1"
    assert (d / "index.json").is_file()
    assert "https://r.test/1" in replay.fixture_urls(d)


def test_record_web_refuses_to_overwrite_without_force(tmp_path, capsys):
    web, d = FakeWeb(), tmp_path / "web"
    args = ["record-web", "--dir", str(d), "--search", "acme", "--url", "https://a.test/p"]
    assert cli.main(args, env={}, web_call=web) == 0
    before = {p.name: p.read_text() for p in d.glob("*.json")}
    # one existing, one new: the whole batch is refused and nothing is called
    web2 = FakeWeb()
    assert cli.main([*args, "--search", "other"], env={}, web_call=web2) == 2
    assert web2.calls == [] and "--force" in capsys.readouterr().err
    assert {p.name: p.read_text() for p in d.glob("*.json")} == before
    assert cli.main([*args, "--force"], env={}, web_call=web2) == 0 and len(web2.calls) == 2


def test_record_web_caps_calls_and_skips_error_results(tmp_path, capsys):
    web, d = FakeWeb(), tmp_path / "web"
    args = ["record-web", "--dir", str(d), "--search", "a", "--search", "b"]
    assert cli.main(args, env={"EVAL_RECORD_MAX_CALLS": "1"}, web_call=web) == 2 and web.calls == []
    assert cli.main(["record-web", "--dir", str(d), "--search", "boom"], env={}, web_call=web) == 1
    assert not list(d.glob("*.json"))  # an error result is never stored
    assert cli.main(["record-web", "--dir", str(d)], env={}, web_call=web) == 2


def test_record_web_requests_file_and_bad_lines(tmp_path):
    f = tmp_path / "req.jsonl"
    f.write_text('{"tool": "web_search", "args": {"query": "q1"}}\n\n{"tool": "browse_url", "args": {"url": "https://u.test"}}\n')
    web, d = FakeWeb(), tmp_path / "web"
    assert cli.main(["record-web", "--dir", str(d), "--requests", str(f)], env={}, web_call=web) == 0
    assert len(web.calls) == 2
    f.write_text('{"tool": "nope", "args": {}}')
    assert cli.main(["record-web", "--dir", str(d), "--requests", str(f)], env={}, web_call=web) == 2
    with pytest.raises(web_record.RecordError):
        web_record.parse_requests(["not json"])
