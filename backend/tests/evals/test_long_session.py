"""Long-session suite (U17): turn plan, generated fixtures, fake script, arms, Gate B and the CLI. Offline."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from evals import arms, cli, gate_b, long_session, replay
from evals import report as rpt
from evals.fake_scripts import ScriptError, build_script
from evals.graders.context import text_tokens
from evals.loader import load_tasks, select_by_tags
from evals.runner import default_timeout
from evals.schema import Task, Trial, TrialResult

EVALS = Path(__file__).resolve().parents[2] / "evals"


@pytest.fixture(scope="module")
def tasks():
    return load_tasks(EVALS / "tasks")


@pytest.fixture(scope="module")
def sessions(tasks):
    return select_by_tags(tasks, ["long_session"])


def small(task: Task, **input_) -> Task:
    return replace(task, input={**task.input, **input_})


# --- the suite -----------------------------------------------------------------------------------------------------


def test_six_long_sessions_of_sixty_to_one_hundred_fifty_turns(sessions):
    assert len(sessions) == 6
    assert all(60 <= long_session.spec_turns(t) <= 150 for t in sessions)
    assert sum(1 for t in sessions if t.input.get("noisy")) == 1
    for t in sessions:
        kinds = {f["kind"] for f in t.input["facts"]}
        assert kinds == {"fact", "id"}, f"{t.id} needs early facts and early ids"
        assert "regression" not in t.tags


def test_plan_plants_early_probes_late_and_ends_on_a_probe_round(sessions):
    for t in sessions:
        turns, probes = long_session.plan(t)
        n, facts = long_session.spec_turns(t), len(t.input["facts"])
        assert [x.n for x in turns] == list(range(1, n + 1))
        assert [x.kind for x in turns[:facts]] == ["plant"] * facts
        assert max(p.turn for p in probes) == n
        assert min(p.turn for p in probes) > facts + 3
        assert {p.turn for p in probes}.isdisjoint(range(1, facts + 1))
        assert len(probes) % facts == 0 and len(probes) >= 2 * facts
        markers = [long_session.MARKER_FMT.format(n=x.n) for x in turns]
        assert all(m in x.text for m, x in zip(markers, turns, strict=True))
        assert any(x.kind == "work" for x in turns) and any(x.kind == "recap" for x in turns)
        assert long_session.plan(t) == (turns, probes), "the plan is deterministic"


def test_short_sessions_still_get_two_probe_rounds(sessions):
    t = small(sessions[0], turns=24, probe_at=[0.5, 0.8, 1.0])
    turns, probes = long_session.plan(t)
    assert len(turns) == 24 and max(p.turn for p in probes) == 24
    assert len({p.turn for p in probes}) == len(probes)
    assert len(probes) >= 2 * len(t.input["facts"])


def test_bad_specs_are_refused(sessions):
    with pytest.raises(long_session.SpecError, match="at least 8"):
        long_session.plan(small(sessions[0], turns=5))
    with pytest.raises(long_session.SpecError, match="input.facts"):
        long_session.plan(small(sessions[0], facts=[]))
    with pytest.raises(long_session.SpecError, match="duplicate"):
        f = sessions[0].input["facts"][0]
        long_session.plan(small(sessions[0], facts=[f, f]))
    with pytest.raises(ScriptError):
        build_script(small(sessions[0], facts=[]))


# --- generated fixtures --------------------------------------------------------------------------------------------


def test_generated_pages_are_sized_replayable_and_identical_across_arms(sessions, tmp_path):
    t = small(sessions[0], turns=40)
    n = long_session.write_fixtures(t, tmp_path / "a")
    work = [x for x in long_session.plan(t)[0] if x.kind == "work"]
    assert n == 2 * len(work)
    sizes = []
    for x in work:
        page = replay.read_fixture(tmp_path / "a", replay.request_key("browse_url", {"url": x.url}))
        search = replay.read_fixture(tmp_path / "a", replay.request_key("web_search", {"query": x.query}))
        assert page["content"].startswith(long_session.PAGE_MARK_FMT.format(n=x.n))
        assert x.url == search["results"][0]["url"] and long_session.PAGE_MARK_FMT.format(n=x.n) in search["results"][0]["snippet"]
        sizes.append(text_tokens(page["content"]))
    assert any(s > 8000 for s in sizes), "some pages must exceed the offload threshold"
    assert any(s < 8000 for s in sizes), "and some must not"
    long_session.write_fixtures(arms.with_tiers(t, "full", suffix=True), tmp_path / "b")
    assert {p.name: p.read_bytes() for p in (tmp_path / "a").glob("*.json")} == {
        p.name: p.read_bytes() for p in (tmp_path / "b").glob("*.json")
    }


def test_replay_dir_copies_the_base_fixtures_and_adds_the_pages(sessions, tmp_path):
    t = small(sessions[0], turns=24)
    base = tmp_path / "base"
    replay.write_fixture(base, "k" * 64, "web_search", {"query": "q"}, {"results": []})
    with long_session.replay_dir_for([arms.with_tiers(t, "off", suffix=True), arms.with_tiers(t, "full", suffix=True)], base) as d:
        index = json.loads((d / "index.json").read_text())
        assert "k" * 64 in index and len(index) > 10
        assert not (base / "index.json").read_text().count("datasheet")
    assert not d.exists()


# --- fake script ---------------------------------------------------------------------------------------------------


def test_fake_script_answers_every_turn_and_probes_with_the_planted_value(sessions):
    t = small(sessions[0], turns=24)
    turns, probes = long_session.plan(t)
    rules = build_script(t)["rules"]
    for turn in turns:
        marker = long_session.MARKER_FMT.format(n=turn.n)
        matching = [r for r in rules if r["when"].get("role") == "user" and r["when"]["includes"] == marker]
        assert len(matching) == 1, turn
        steps = matching[0]["respond"]["steps"]
        if turn.kind == "work":
            assert [s["name"] for s in steps] == ["web_search", "browse_url"]
            assert any(r["when"].get("includes") == long_session.PAGE_MARK_FMT.format(n=turn.n) for r in rules)
        if turn.kind == "probe":
            expect = next(p.expect[0] for p in probes if p.turn == turn.n)
            assert expect in steps[0]["text"]
    ramp = [r["respond"]["usage"]["inputTokens"] for r in rules if r["when"].get("role") == "user"]
    assert ramp == sorted(ramp), "declared usage ramps up with the session"


# --- arms ----------------------------------------------------------------------------------------------------------


def test_long_sessions_expand_into_one_task_per_arm(sessions, tasks):
    expanded = arms.expand_arms(tasks)
    repeat = [t for t in tasks if t.kind == "repeat"]  # U34: memory repeat tasks also run once per (memory) arm
    assert len(expanded) == len(tasks) + len(sessions) * (len(arms.ARMS) - 1) + len(repeat) * (len(arms.REPEAT_ARMS) - 1)
    ids = {t.id for t in expanded}
    assert {f"ls-support-triage@{a}" for a in arms.ARMS} <= ids and "ls-support-triage" not in ids
    assert "chat-fake-browse" in ids
    base, arm = arms.split_arm("ls-support-triage@offload+clear")
    assert (base, arm) == ("ls-support-triage", "offload+clear") and arms.split_arm("chat-x") == ("chat-x", None)
    pinned = arms.with_tiers(sessions[0], "full")
    assert arms.expand_arms([pinned]) == [pinned], "a task that pins its tier runs once"


def test_arm_environment_only_shrinks_thresholds_in_fake_mode():
    assert arms.stack_env(None, "fake") == {}
    assert arms.stack_env("off", "fake") == {"ENGINE_CONTEXT_TIERS": "off"}
    assert arms.stack_env("offload", "fake") == {"ENGINE_CONTEXT_TIERS": "offload"}
    fake = arms.stack_env("full", "fake")
    assert fake["ENGINE_CONTEXT_TIERS"] == "full" and float(fake["ENGINE_CONTEXT_CLEAR_FRACTION"]) < 0.1
    assert arms.stack_env("full", "live") == {"ENGINE_CONTEXT_TIERS": "full"}, "live measures the production defaults"
    with pytest.raises(ValueError):
        arms.stack_env("everything", "fake")


def test_long_sessions_get_a_timeout_that_scales_with_their_turns(sessions, tasks):
    chat = next(t for t in tasks if t.kind == "chat")
    assert default_timeout(chat, "fake") == 180 and default_timeout(chat, "live") == 900
    longest = max(sessions, key=long_session.spec_turns)
    assert default_timeout(longest, "live") > 10_000 and default_timeout(longest, "fake") > 1_000


# --- Gate B --------------------------------------------------------------------------------------------------------


def trial(arm_metrics: dict, *, passed=True) -> TrialResult:
    return TrialResult(trial=Trial("x", 0, "live"), status="ok", metrics=arm_metrics)


def metrics(**over):
    base = {
        "tiers": "off", "noisy": False, "mean_tokens_after": 100_000.0, "token_source": "usage",
        "prompt_tokens_after": 1_000_000, "cache_read_share_after": 0.8, "compactions": 4, "compaction_failed": 0,
        "overflow_failures": 0, "clears": 0, "rereads": 0, "max_stored_result_tokens": 20_000,
        "probes": {"fact": [10, 10], "id": [10, 10]},
    }
    return {**base, **over}


def make_report(arm_metrics: dict[str, dict], *, cost=None, mode="live") -> dict:
    """A report with one task per arm (`<task>@<arm>`), one trial each."""
    results = []
    for arm, m in arm_metrics.items():
        base_id = "ls-a"
        results.append(
            TrialResult(trial=Trial(f"{base_id}@{arm}", 0, mode), status="ok", metrics={**m, "tiers": arm},
                        trace=None, error=None)
        )
    report = rpt.build_report(results, name="t", suite=["long_session"], mode=mode, k=1)
    for tid, entry in report["tasks"].items():
        arm = arms.split_arm(tid)[1]
        entry["cost_usd"] = (cost or {}).get(arm, 1.0)
        entry["passes"] = 1
    return report


GOOD = metrics(mean_tokens_after=40_000.0, compactions=1, clears=50, rereads=2, max_stored_result_tokens=2_000,
               cache_read_share_after=0.78)


def by_id(verdict):
    return {c.id: c for c in verdict.criteria}


def test_gate_b_passes_an_arm_that_meets_every_criterion():
    rep = make_report({"off": metrics(), "offload+clear": GOOD})
    (v,) = gate_b.evaluate(rep, regressions={"offload+clear": False})
    assert v.decision == "pass", [c for c in v.criteria if c.status != "pass"]
    assert by_id(v)["tokens"].detail.startswith("60.0% lower")


def test_gate_b_names_each_failing_criterion():
    bad = metrics(mean_tokens_after=70_000.0, compactions=3, compaction_failed=1, overflow_failures=2, clears=10, rereads=3,
                  max_stored_result_tokens=9_000, cache_read_share_after=0.6, probes={"fact": [9, 10], "id": [10, 10]})
    rep = make_report({"off": metrics(), "full": bad}, cost={"off": 1.0, "full": 1.5})
    (v,) = gate_b.evaluate(rep, regressions={"full": True})
    failed = {c.id for c in v.failed}
    assert failed == {"tokens", "probe-fact", "compactions", "compaction-failed", "overflow", "cache", "reread", "verbatim",
                      "regression", "cost"}
    assert v.decision == "fail"


def test_gate_b_probe_margin_is_two_points():
    pooled = lambda hit: metrics(mean_tokens_after=1.0, probes={"fact": [hit, 100], "id": [100, 100]})  # noqa: E731
    ok = gate_b.evaluate(make_report({"off": pooled(100), "full": pooled(98)}))[0]
    assert by_id(ok)["probe-fact"].status == "pass"
    worse = gate_b.evaluate(make_report({"off": pooled(100), "full": pooled(97)}))[0]
    assert by_id(worse)["probe-fact"].status == "fail"


def test_gate_b_leaves_noisy_tasks_out_and_marks_unmeasurable_criteria():
    rep = make_report({"off": metrics(noisy=True), "full": GOOD})
    (v,) = gate_b.evaluate(rep)
    assert v.decision == "fail" and by_id(v)["data"].status == "fail"
    fake = make_report(
        {"off": metrics(token_source="estimate", compactions=0, cache_read_share_after=0.0),
         "offload+clear": metrics(token_source="estimate", mean_tokens_after=10_000.0, compactions=0, clears=0,
                                  cache_read_share_after=0.0, max_stored_result_tokens=100)},
        mode="fake",
    )
    (v,) = gate_b.evaluate(fake, regressions={"offload+clear": False})
    c = by_id(v)
    assert c["compactions"].status == "n/a" and c["cache"].status == "n/a" and c["reread"].status == "n/a"
    assert v.decision == "pass"


def test_gate_b_is_incomplete_until_the_regression_run_is_supplied():
    (v,) = gate_b.evaluate(make_report({"off": metrics(), "offload+clear": GOOD}))
    assert v.decision == "incomplete" and by_id(v)["regression"].status == "pending"
    text = gate_b.render_markdown(make_report({"off": metrics(), "offload+clear": GOOD}), [v])
    assert "INCOMPLETE" in text and "nothing yet" in text


def test_gate_b_cli_exit_codes(tmp_path, capsys):
    rep = make_report({"off": metrics(), "offload+clear": GOOD})
    path = tmp_path / "r.json"
    rpt.write_report(rep, path)
    assert cli.main(["gate-b", str(path), "--baselines-dir", str(tmp_path)], env={}) == 3
    # a regression run per arm, compared with a regression baseline
    reg = rpt.build_report([], name="reg", suite=["regression"], mode="live", k=3)
    reg_path = tmp_path / "reg.json"
    rpt.write_report(reg, reg_path)
    code = cli.main(["gate-b", str(path), "--regression", f"offload+clear={reg_path}", "--regression-baseline", str(reg_path),
                     "--baselines-dir", str(tmp_path)], env={})
    assert code == 0
    assert cli.main(["gate-b", str(path), "--regression", f"offload+clear={reg_path}", "--baselines-dir", str(tmp_path)], env={}) == 2
    bad = make_report({"off": metrics(), "offload+clear": metrics(mean_tokens_after=90_000.0)})
    rpt.write_report(bad, path)
    assert cli.main(["gate-b", str(path), "--baselines-dir", str(tmp_path)], env={}) == 1
    out = capsys.readouterr().out
    assert "Gate B" in out and "ARM `OFFLOAD+CLEAR`".lower() in out.lower()
    plain = make_report({"off": metrics()})
    rpt.write_report(plain, path)
    assert cli.main(["gate-b", str(path), "--baselines-dir", str(tmp_path)], env={}) == 2


def test_run_dry_run_counts_every_arm(tmp_path, capsys):
    dirs = ["--tasks-dir", str(EVALS / "tasks"), "--results-dir", str(tmp_path), "--baselines-dir", str(tmp_path)]
    assert cli.main(["run", "--suite", "long_session", "-k", "3", "--dry-run", *dirs], env={}) == 0
    out = capsys.readouterr().out
    assert "18 task(s) x k=3" in out and "ls-incident-log@full" in out
    assert "estimated cost: $" in out
    # 3 arms x (1.5 + 1.5 + 2.0 + 1.0 + 0.75 + 1.5) USD task caps x k=3: a ceiling, not the expected spend
    assert "$74.25" in out
    assert cli.main(["run", "--suite", "long_session", "--tiers", "full", "-k", "1", "--dry-run", *dirs], env={}) == 0
    assert "6 task(s) x k=1" in capsys.readouterr().out
