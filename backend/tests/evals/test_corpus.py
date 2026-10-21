"""The seed corpus (U9): every task loads, every web call it replays has a fixture, the judge label sets are sound.

Offline: nothing here starts the stack or calls a model.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from evals import calibrate as cal
from evals.graders.judge import JUDGE_FIELDS
from evals.loader import load_tasks, select_by_tags
from evals.replay import request_key
from evals.schema import Task

EVALS = Path(__file__).resolve().parents[2] / "evals"
WEB = EVALS / "fixtures/web"
CAL = EVALS / "calibration"
WEB_TOOLS = ("web_search", "browse_url")
LATER_SUITES = {"dw_subagents"}  # folder names and tags of suites added after the seed corpus


@pytest.fixture(scope="module")
def tasks() -> list[Task]:
    return load_tasks(EVALS / "tasks")


@pytest.fixture(scope="module")
def index() -> dict:
    return json.loads((WEB / "index.json").read_text())


def replayed_requests(task: Task) -> list[tuple[str, dict]]:
    """Every web call a task makes in fake mode, as (tool, args)."""
    calls: list[tuple[str, dict]] = []
    if task.kind == "deep_work":  # the fake Deep Work script has each worker search its sub_query once
        for sub in task.input["plan"]["sub_queries"]:
            calls.append(("web_search", {"query": sub["sub_query"]}))
        scenario = task.fake.get("scenario") or {}  # U24: a steered worker and a gap-round worker search once more
        for extra in (scenario.get("steer"), scenario.get("gap")):
            if extra:
                calls.append(("web_search", {"query": extra.get("query") or extra["sub_query"]}))
    for rule in task.fake.get("rules") or []:
        for step in rule["respond"]["steps"]:
            if step.get("type") == "tool_call" and step["name"] in WEB_TOOLS:
                calls.append((step["name"], step["arguments"]))
    return calls


def test_corpus_size_and_mix(tasks):
    # The two U5 smoke tasks stay at the folder root (other tests load them by that path).
    # Later phases add their own suites in their own folders (U24: dw_subagents); this test pins the seed corpus.
    by_folder = Counter(p.parent.name for p in (EVALS / "tasks").rglob("*.yaml") if p.parent.name not in LATER_SUITES)
    assert by_folder == {"deep_work": 12, "chat": 8, "smoke": 2, "tasks": 2}
    tasks = [t for t in tasks if not set(t.tags) & LATER_SUITES]
    assert len(tasks) == 24
    assert Counter(t.kind for t in tasks) == {"deep_work": 13, "chat": 11}
    smoke = select_by_tags(tasks, ["smoke"])
    assert len(smoke) == 4 and all("regression" in t.tags for t in smoke)
    assert len(select_by_tags(tasks, ["regression"])) >= 8


def test_every_task_documents_its_failure_mode_and_level(tasks):
    for path in (EVALS / "tasks").rglob("*.yaml"):
        head = path.read_text().split("\n", 1)[0]
        if path.parent.name == "tasks":
            continue  # the two U5 smoke tasks predate the convention and carry their own comment
        assert head.startswith(("# [easy]", "# [medium]", "# [hard]")), path.name
        assert "Guards" in path.read_text().split("\nid:", 1)[0], path.name


def test_difficulty_is_spread(tasks):
    levels = Counter(
        p.read_text().split("\n", 1)[0].split("]")[0].lstrip("# [")
        for p in (EVALS / "tasks").rglob("*.yaml")
        if p.read_text().startswith("# [")
    )
    assert {"easy", "medium", "hard"} <= set(levels)


def test_tasks_have_gating_code_graders_and_valid_judges(tasks):
    for t in tasks:
        gating_code = [g for g in t.graders if g.type != "judge" and g.gating]
        assert gating_code, f"{t.id} has no gating code grader"
        for g in t.graders:
            if g.type == "judge":
                assert g.params.get("judge") in JUDGE_FIELDS, f"{t.id}/{g.id}"
        assert t.budget.max_usd is not None and t.budget.max_rounds is not None, t.id


def test_deep_work_plans_match_the_fake_script(tasks):
    from evals.fake_scripts import build_script

    for t in tasks:
        if t.kind == "deep_work":
            assert build_script(t)["rules"], t.id
            ids = [s["worker_id"] for s in t.input["plan"]["sub_queries"]]
            assert len(ids) == len(set(ids)) >= 2
            assert t.fake.get("findings_md"), t.id


def test_chat_tasks_script_every_turn(tasks):
    from evals.fake_scripts import build_script

    for t in tasks:
        if t.kind == "chat":
            rules = build_script(t)["rules"]
            for turn in t.input["turns"]:
                text = turn["text"] if isinstance(turn, dict) else turn
                assert any(r["when"].get("role") == "user" and r["when"]["includes"] in text for r in rules), (t.id, text)


def test_every_replayed_request_has_a_fixture(tasks, index):
    missing = []
    for t in tasks:
        if t.environment.web != "replay":
            continue
        for tool, args in replayed_requests(t):
            key = request_key(tool, args)
            if key not in index or not (WEB / f"{key}.json").is_file():
                missing.append((t.id, tool, args))
    assert not missing, f"no fixture for {missing}"


def test_fixtures_are_well_formed_and_used(tasks, index):
    used = {request_key(tool, args) for t in tasks for tool, args in replayed_requests(t)}
    files = {p.stem for p in WEB.glob("*.json") if p.name != "index.json"}
    assert files == set(index), "index.json and the fixture files disagree"
    assert files == used, f"unused fixtures: {sorted(files - used)}"
    for key, meta in index.items():
        data = json.loads((WEB / f"{key}.json").read_text())
        assert data["key"] == key and data["tool"] == meta["tool"]
        assert request_key(data["tool"], data["args"]) == key, "file name is not the request hash"
        assert isinstance(data["result"], dict)


def test_deep_work_fixtures_serve_the_urls_the_scripted_learnings_cite(tasks):
    """`url_resolves` checks learning URLs against fixture URLs; the fake script cites example.com/<worker>/1..2."""
    from evals.replay import fixture_urls

    known = set(fixture_urls(WEB))
    for t in tasks:
        if t.kind == "deep_work":
            for sub in t.input["plan"]["sub_queries"]:
                for n in (1, 2):
                    assert f"https://example.com/{sub['worker_id']}/{n}" in known, (t.id, sub["worker_id"])


def test_search_results_echo_their_query(tasks):
    """The fake script matches a worker's search result on `"query": "<sub_query>"`, so the fixture must echo it."""
    for t in tasks:
        if t.kind == "deep_work":
            for sub in t.input["plan"]["sub_queries"]:
                key = request_key("web_search", {"query": sub["sub_query"]})
                assert json.loads((WEB / f"{key}.json").read_text())["result"]["query"] == sub["sub_query"]


def test_referenced_kb_and_file_inputs_exist(tasks):
    for t in tasks:
        refs = list(t.input.get("kb") or []) + [f["source"] for f in t.input.get("files") or []]
        for ref in refs:
            assert (EVALS / ref).is_file(), (t.id, ref)


def test_task_ids_match_file_names():
    from evals.loader import load_task

    for path in (EVALS / "tasks").rglob("*.yaml"):
        assert load_task(path).id == path.stem


# --- judge calibration labels ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("judge", sorted(JUDGE_FIELDS))
def test_calibration_set_is_complete_balanced_and_marked_draft(judge):
    examples = cal.load_examples(judge, CAL)  # validates label and input fields
    yes = [e for e in examples if e.label == "yes"]
    no = [e for e in examples if e.label == "no"]
    assert len(yes) >= cal.MIN_PER_CLASS and len(no) >= cal.MIN_PER_CLASS
    assert len(yes) == len(no)
    rows = [json.loads(line) for line in (CAL / f"{judge}.jsonl").read_text().splitlines() if line.strip()]
    # Draft labels stay unreviewed until a person flips them; this test is the tripwire to update then.
    assert all(r["reviewed"] is False and "human" in r["review_note"] for r in rows)
    assert not any(e.reviewed for e in examples)
    assert len({json.dumps(e.input, sort_keys=True) for e in examples}) == len(examples), "duplicate inputs"
    assert not (CAL / f"{judge}.record.json").exists(), "a calibration record must come from reviewed labels"


def test_calibration_readme_explains_the_review_requirement():
    text = (CAL / "README.md").read_text()
    assert "human review" in text and "reviewed" in text and "--allow-unreviewed" in text


def test_calibrate_refuses_unreviewed_labels(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("EVAL_MAX_USD", "1")
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "stub-model")

    async def never(_call):  # pragma: no cover - must not be reached
        raise AssertionError("the judge model was called on unreviewed labels")

    (tmp_path / "claim_supported.jsonl").write_text(
        (CAL / "claim_supported.jsonl").read_text()
    )
    code = cal.main(["claim_supported"], transport=never, calibration_dir=tmp_path)
    assert code == 2
    assert "human review" in capsys.readouterr().err
