"""U24: the dw_subagents task suite, its two fake scripts and the arm switch (offline: no stack, no model)."""

import json
from pathlib import Path

import pytest

from evals import cli
from evals.fake_scripts import build_script
from evals.graders.judge import JUDGE_FIELDS
from evals.loader import load_tasks, select_by_tags
from evals.stack import ARMS, arm_env, eval_stack, resolve_arm

EVALS = Path(__file__).resolve().parents[2] / "evals"
SUITE_DIR = EVALS / "tasks" / "dw_subagents"


@pytest.fixture(scope="module")
def tasks():
    return load_tasks(SUITE_DIR)


def calls(script: dict, name: str) -> list[dict]:
    return [
        s for r in script["rules"] for s in r["respond"]["steps"] if s.get("type") == "tool_call" and s["name"] == name
    ]


def test_the_suite_has_ten_deep_work_tasks_outside_the_ci_subsets(tasks):
    assert len(tasks) == 10 and {t.kind for t in tasks} == {"deep_work"}
    assert all(t.tags == ("dw_subagents",) for t in tasks)  # not `regression`, not `smoke`: CI speed
    assert len(select_by_tags(tasks, ["dw_subagents"])) == 10
    assert not [t for t in select_by_tags(load_tasks(EVALS / "tasks"), ["regression", "smoke"]) if t.id.startswith("dws-")]
    assert {p.stem for p in SUITE_DIR.glob("*.yaml")} == {t.id for t in tasks}


def test_the_suite_covers_the_five_failure_modes(tasks):
    ids = " ".join(t.id for t in tasks)
    for mode in ("malformed", "overlap", "bait", "steer", "gap-round"):
        assert mode in ids, mode
    assert sum("malformed" in t.id for t in tasks) == 3 and sum("steer" in t.id for t in tasks) == 2


def test_every_task_states_what_it_guards_and_gates_on_code(tasks):
    for path in SUITE_DIR.glob("*.yaml"):
        text = path.read_text()
        assert text.startswith(("# [easy]", "# [medium]", "# [hard]")) and "Guards" in text.split("\nid:", 1)[0], path.name
    for t in tasks:
        gating = [g for g in t.graders if g.type != "judge" and g.gating]
        assert gating, t.id
        assert any(g.type == "malformed_worker_answers" for g in t.graders), t.id
        for g in t.graders:
            if g.type == "judge":
                assert g.params["judge"] in JUDGE_FIELDS
        assert t.budget.max_usd is not None and t.budget.max_rounds is not None


def test_steer_tasks_name_a_planned_worker_and_seed_the_live_driver(tasks):
    for t in (t for t in tasks if "steer" in t.id):
        steer = t.fake["scenario"]["steer"]
        planned = {s["worker_id"] for s in t.input["plan"]["sub_queries"]}
        assert steer["worker"] in planned
        assert t.input["steer"]["message"] == steer["message"]  # the live driver sends this text as the user's steer
        grader = next(g for g in t.graders if g.type == "steer_applied")
        assert grader.params["worker"] == steer["worker"]
        assert all(k.lower() in steer["query"].lower() for k in grader.params["keywords"])


def test_baseline_script_uses_no_v2_tool_and_v2_script_uses_them(tasks):
    for t in tasks:
        base, v2 = build_script(t, "baseline"), build_script(t, "v2")
        for name in ("complete_task", "verifier", "steer_worker"):
            assert not calls(base, name), (t.id, name)
        assert calls(v2, "complete_task") and calls(v2, "verifier"), t.id
        assert all("label" not in c["arguments"] for c in calls(base, "research_worker")), t.id
        assert all(c["arguments"].get("label") for c in calls(v2, "research_worker")), t.id
        assert bool(calls(v2, "steer_worker")) == ("steer" in t.id), t.id
        assert build_script(t) == base  # the arm defaults to the baseline


def test_malformed_workers_end_in_text_only_where_the_task_says_so(tasks):
    by_id = {t.id: t for t in tasks}
    nudge_texts = {}
    for tid in ("dws-malformed-one-worker", "dws-malformed-no-recovery", "dws-malformed-two-workers"):
        script = build_script(by_id[tid], "v2")
        reminder = next(r for r in script["rules"] if r["when"].get("includes", "").endswith("complete_task tool now"))
        nudge_texts[tid] = [s["type"] for s in reminder["respond"]["steps"]]
        prose = by_id[tid].fake["scenario"]["prose"]
        text_endings = [
            r for r in script["rules"]
            if r["when"].get("tool") == "dw_save_learnings" and r["respond"]["steps"][0]["type"] == "text"
        ]
        assert len(text_endings) == len(prose), tid
    assert nudge_texts["dws-malformed-one-worker"] == ["tool_call"]  # the reminder works
    assert nudge_texts["dws-malformed-no-recovery"] == ["text"]  # the reminder is ignored
    partial = [
        r for r in build_script(by_id["dws-malformed-two-workers"], "baseline")["rules"]
        if r["respond"]["steps"][0].get("type") == "text" and "partial answer" in r["respond"]["steps"][0]["text"]
    ]
    assert len(partial) == 1 and "learning_count" not in json.loads(partial[0]["respond"]["steps"][0]["text"])


def test_overlap_and_gap_tasks_script_different_sources_per_arm(tasks):
    by_id = {t.id: t for t in tasks}

    def saved_urls(script):
        return [
            [x["source_url"] for x in c["arguments"]["learnings"]] for c in calls(script, "dw_save_learnings")
        ]

    for tid in ("dws-overlapping-subqueries", "dws-overlap-four-markets", "dws-fork-gap-round"):
        base = saved_urls(build_script(by_id[tid], "baseline"))
        v2 = saved_urls(build_script(by_id[tid], "v2"))
        flat = lambda groups: [u for g in groups for u in g]  # noqa: E731
        assert len(set(flat(base))) < len(flat(base)), tid  # a source is saved twice
        assert len(set(flat(v2))) == len(flat(v2)), tid  # every source once


def test_a_gap_round_script_runs_a_second_round_with_a_third_worker(tasks):
    gap = next(t for t in tasks if t.id == "dws-fork-gap-round")
    rounds = [c["arguments"]["round"] for c in calls(build_script(gap, "v2"), "dw_start_round")]
    assert rounds == [1, 2]
    reflections = {c["arguments"]["round"]: c["arguments"] for c in calls(build_script(gap, "v2"), "dw_record_reflection")}
    assert reflections[1]["should_continue"] is True and reflections[2]["should_continue"] is False


def test_arm_selection(monkeypatch):
    assert ARMS == ("baseline", "v2")
    monkeypatch.delenv("EVAL_ARM", raising=False)
    assert resolve_arm() == "baseline" and resolve_arm("v2") == "v2"
    assert resolve_arm(None, {"EVAL_ARM": "v2"}) == "v2"
    with pytest.raises(ValueError, match="unknown arm"):
        resolve_arm("v3")
    assert arm_env("baseline", "fake") == ({}, {})
    backend, worker = arm_env("v2", "fake")
    assert backend["ENABLE_ENGINE_SUBAGENT_V2"] == "true" and "fork" not in backend["ENGINE_SUBAGENT_V2_SWITCHES"]
    assert worker == {"ENGINE_COMPLETE_TASK": "1", "ENGINE_STEER_WORKER": "1"}
    live, _ = arm_env("v2", "live")
    assert set(live["ENGINE_SUBAGENT_V2_SWITCHES"].split(",")) == {"typed_results", "verifier", "steer_worker", "fork"}
    with pytest.raises(ValueError, match="unknown arm"), eval_stack("fake", arm="v3"):
        pass  # refused before anything boots


def test_the_plans_live_gate_command_parses_and_estimates_without_spending(capsys):
    argv = ["run", "--suite", "dw_subagents", "--mode", "live", "-k", "3", "--max-usd", "30"]
    assert cli.main([*argv, "--dry-run"], env={}) == 0
    out = capsys.readouterr().out
    assert "10 task(s) x k=3 in live mode" in out and "estimated cost: $" in out
    assert cli.main([*argv, "--arm", "v2", "--dry-run"], env={}) == 0
    assert cli.main(argv, env={}) == 2  # no EVAL_OPENROUTER_API_KEY: refuses before booting anything
    assert "EVAL_OPENROUTER_API_KEY" in capsys.readouterr().err
    assert cli.main([*argv, "--dry-run"], env={"EVAL_ARM": "bogus"}) == 2
