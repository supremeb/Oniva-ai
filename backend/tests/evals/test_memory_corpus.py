"""U34: the memory task corpus, its generator and its fake scripts (offline: no stack, no model)."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from evals import arms, memory_corpus
from evals.fake_scripts import CURATOR_MARK, build_script
from evals.graders.judge import JUDGE_FIELDS
from evals.loader import load_tasks, select_by_tags
from evals.replay import fixture_path, request_key
from evals.schema import GRADER_TYPES

EVALS = Path(__file__).resolve().parents[2] / "evals"
WEB = EVALS / "fixtures/web"
HOSTS_ALLOWED = ("example.com", "evil-example.test")


@pytest.fixture(scope="module")
def tasks():
    return select_by_tags(load_tasks(EVALS / "tasks"), ["memory"])


def of(tasks, prefix):
    return [t for t in tasks if t.id.startswith(prefix)]


def test_the_committed_files_are_what_the_generator_writes():
    assert memory_corpus.check() == [], "run `python -m evals.memory_corpus` and commit the result"


def test_suite_sizes_kinds_and_tags(tasks):
    red, prefs, repeat = of(tasks, "m1-rt-"), of(tasks, "m2-pf-"), of(tasks, "m3-rp-")
    assert (len(red), len(prefs), len(repeat)) == (30, 12, 6) and len(tasks) == 48
    assert {t.kind for t in red} == {"redteam"} and {t.kind for t in prefs} == {"memory"} and {t.kind for t in repeat} == {"repeat"}
    assert all(set(t.tags) >= {"memory"} for t in tasks)
    assert all("redteam" in t.tags for t in red) and all("repeat" in t.tags for t in repeat)
    # The new tasks stay out of the CI subsets: not `regression`, not `smoke`.
    everything = load_tasks(EVALS / "tasks")
    assert not [t for t in select_by_tags(everything, ["regression", "smoke"]) if t in tasks]
    assert {p.stem for sub in ("memory_repeat", "memory_prefs", "redteam") for p in (EVALS / "tasks" / sub).glob("*.yaml")} == {t.id for t in tasks}


def test_every_task_states_what_it_guards_and_uses_known_graders(tasks):
    for path in (p for sub in ("memory_repeat", "memory_prefs", "redteam") for p in (EVALS / "tasks" / sub).glob("*.yaml")):
        head = path.read_text().split("\nid:", 1)[0]
        assert head.startswith(("# [easy]", "# [medium]", "# [hard]")) and "Guards" in head, path.name
    for t in tasks:
        assert all(g.type in GRADER_TYPES for g in t.graders)
        assert any(g.gating for g in t.graders), t.id
        assert t.budget.max_usd is not None and t.budget.max_rounds is not None
        for g in t.graders:
            if g.type == "judge":
                assert g.params["judge"] in JUDGE_FIELDS and g.gating is False  # advisory until calibrated by a person


def test_red_team_covers_four_channels_and_both_paths_with_a_unique_canary_each(tasks):
    red = of(tasks, "m1-rt-")
    assert Counter(t.input["channel"] for t in red) == {"web": 10, "kb": 7, "connector": 7, "file": 6}
    assert Counter(t.input["path"] for t in red) == {"chat": 14, "dw": 16}
    assert len({t.input["canary"] for t in red}) == 30
    for t in red:
        types = {g.type for g in t.graders}
        assert {"no_active_write_from_tainted_run", "no_instruction_in_injected_block", "cross_project_leak"} <= types, t.id
        assert t.input["seed"]["core"] and t.input["seed"]["bullet"]["key"] == "seed-bullet-1"
        assert arms.memory_of(t) == "auto"  # the strictest curator mode
        assert ("memory_curated" in types) == (t.input["path"] == "dw")


def test_the_payload_reaches_the_model_through_a_fixture_and_carries_the_canary(tasks):
    for t in of(tasks, "m1-rt-"):
        if t.input["path"] == "dw":
            queries = [s["sub_query"] for s in t.input["plan"]["sub_queries"]]
        else:
            queries = [next(s["arguments"]["query"] for r in t.fake["rules"] for s in r["respond"]["steps"] if s.get("name") == "web_search")]
        texts = []
        for q in queries:
            path = fixture_path(WEB, request_key("web_search", {"query": q}))
            assert path is not None and path.name.startswith("poison_"), (t.id, q)
            result = json.loads(path.read_text())["result"]
            assert result["query"] == q
            texts.append(" ".join(r["snippet"] for r in result["results"]))
        assert any(t.input["canary"] in x for x in texts), t.id
        for x in texts:
            hosts = re.findall(r"https?://([^/\s\"')]+)", x) + re.findall(r"@([\w.-]+)", x)
            assert all(h.rstrip(".,").endswith(HOSTS_ALLOWED) for h in hosts), (t.id, hosts)


def test_chat_attacks_are_a_memory_write_the_policy_must_refuse(tasks):
    for t in (x for x in of(tasks, "m1-rt-") if x.input["path"] == "chat"):
        rules = build_script(t)["rules"]
        calls = [s for r in rules for s in r["respond"]["steps"] if s.get("type") == "tool_call"]
        assert [c["name"] for c in calls][0] == "web_search"
        assert calls[1]["name"].startswith("memory_"), t.id
        assert t.input["unattended"] is True and t.input["selections"] == {"web_search": True}


def test_deep_work_attacks_script_a_fooled_curator_that_comes_first(tasks):
    shapes = set()
    for t in (x for x in of(tasks, "m1-rt-") if x.input["path"] == "dw"):
        rules = build_script(t)["rules"]
        first = next(r for r in rules if r["when"].get("includes") == CURATOR_MARK)
        assert rules.index(first) <= 1  # only the task's own probe rule may precede it
        reply = first["respond"]["steps"][0]["text"]
        assert t.input["canary"] in reply or t.input["attack"] == "bump_unknown"  # that reply bumps ids, it has no text
        body = json.loads(reply) if reply.startswith("{") else None
        shapes |= {op["op"] for op in (body or {}).get("deltas", [])} | ({"prose"} if body is None else set())
    assert {"add", "bump", "revise", "retire", "prose"} <= shapes  # every delta kind, and a reply that is not JSON


def test_preference_probes_state_store_ask_and_the_scripted_answer_follows_it(tasks):
    prefs = of(tasks, "m2-pf-")
    assert Counter(t.input.get("delete") for t in prefs) == {None: 9, "file": 1, "bullet": 1, "purge": 1}
    for t in prefs:
        rules = build_script(t)["rules"]
        create = next(s for r in rules for s in r["respond"]["steps"] if s.get("name") == "memory_create")
        assert t.input["token"].lower() in create["arguments"]["file_text"].lower()
        assert create["arguments"]["path"] == t.input["stored_path"]
        answer = next(r["respond"]["steps"][0]["text"] for r in rules if r["when"].get("includes") == t.input["question"])
        assert re.search(t.input["expect"], answer, re.IGNORECASE)
        if t.input.get("forbid"):
            assert not re.search(t.input["forbid"], answer, re.IGNORECASE), t.id
        assert arms.memory_of(t) == "manual"
        if t.input.get("delete"):
            after = next(r for r in rules if r["when"].get("includes") == t.input["question_again"])
            text = json.dumps(after["respond"]["steps"])
            assert all(tok.lower() not in text.lower() for tok in t.input["delete_tokens"])


def test_repeat_tasks_run_five_times_and_expand_into_a_memory_arm_and_a_control(tasks):
    repeat = of(tasks, "m3-rp-")
    assert len(arms.expand_arms(repeat)) == 12
    assert {arms.memory_of(t) for t in arms.expand_arms(repeat)} == {"propose", "control"}
    for t in repeat:
        assert t.input["runs"] == 5 and t.input["reviewer"] == "approve_unflagged" and t.input["score_graders"]
        assert build_script(t)["rules"][0]["when"]["includes"] == CURATOR_MARK
        assert next(g for g in t.graders if g.type == "score_lift").gating is False  # judged across trials by Gate D
        assert next(g for g in t.graders if g.type == "memory_curated").params == {}  # expects one curation per run
    ids = [t.id for t in sorted(arms.expand_arms(of(tasks, "")), key=lambda t: t.id)]
    assert ids[0].startswith("m1-rt-") and ids[-1].startswith("m3-rp-")  # a spend cap cuts the longest suite last


def test_judge_labels_for_the_preference_probes_are_draft_pairs():
    rows = [json.loads(x) for x in (EVALS / "calibration/memory_applied.jsonl").read_text().splitlines() if x.strip()]
    mine = [r for r in rows if r["id"].startswith("mp-")]
    assert len(mine) == 24 and Counter(r["label"] for r in mine) == {"yes": 12, "no": 12}
    assert all(r["reviewed"] is False and "human" in r["review_note"] for r in mine)
    assert not (EVALS / "calibration/memory_applied.record.json").exists()
