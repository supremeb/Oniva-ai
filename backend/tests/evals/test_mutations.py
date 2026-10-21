"""U10 mutation meta-eval, offline: every injected fault must flip the grader built to catch it.

Clean samples are the recorded fake-mode traces in `fixtures/` graded by the *real* regression task specs.
"""

import json
from pathlib import Path

import pytest

from evals import mutate
from evals.loader import load_task
from evals.mutate import MUTATIONS, Sample, run_meta_eval

pytestmark = pytest.mark.eval

FIX = Path(__file__).parent / "fixtures"
TASKS = Path(__file__).resolve().parents[2] / "evals" / "tasks"
URLS = [f"https://vendor{n}.example.com/pricing" for n in range(1, 5)]


def _raw(name: str) -> dict:
    return json.loads((FIX / name).read_text())


@pytest.fixture(scope="module")
def tasks():
    return {t: load_task(TASKS / f"{t}.yaml") for t in ("chat-fake-browse", "dw-fake-smoke")}


@pytest.fixture(scope="module")
def samples():
    learnings = [
        {"fact": f"Vendor {n} lists a price.", "source_url": u, "source_type": "web", "confidence": 0.9, "sub_query": "q"}
        for n, u in enumerate(URLS, 1)
    ]
    return {
        "chat-fake-browse": Sample(_raw("trace_fake_chat.json"), cost_usd=0.003),
        "dw-fake-smoke": Sample(
            _raw("trace_fake_deep_work.json"),
            {
                "report.md": "Vendor one lists two price points [1][2]. Vendor two lists two more [3][4].",
                "learnings": learnings,
                "fixture_urls": list(URLS),
            },
            cost_usd=0.005,
        ),
    }


def test_the_corpus_tasks_used_here_are_regression_tagged(tasks):
    assert all("regression" in t.tags for t in tasks.values())


def test_clean_samples_pass_every_code_grader(tasks, samples):
    clean, _ = run_meta_eval(tasks, samples, ())
    assert clean == {"chat-fake-browse": (), "dw-fake-smoke": ()}


def test_every_mutation_is_caught(tasks, samples):
    _, results = run_meta_eval(tasks, samples)
    table = mutate.render_table(results)
    assert len(results) == len(MUTATIONS) >= 10
    missed = [r for r in results if not r.caught]
    assert not missed, "graders that cannot catch their mutation:\n" + table


def test_mutations_name_graders_that_exist_in_their_task(tasks):
    for m in MUTATIONS:
        ids = {g.id for g in tasks[m.task_id].graders if g.gating}
        assert set(m.must_fail) <= ids, m.id


def test_a_mutation_is_not_caught_when_its_grader_is_removed(tasks, samples):
    """The meta-eval itself must be able to fail: a task without the citation grader misses the citation fault."""
    from dataclasses import replace

    blind = replace(tasks["dw-fake-smoke"], graders=tuple(g for g in tasks["dw-fake-smoke"].graders if g.type != "citation_map"))
    _, results = run_meta_eval({**tasks, "dw-fake-smoke": blind}, samples, [m for m in MUTATIONS if m.id == "drop-citation-target"])
    assert not results[0].caught and results[0].missed == ("citations-map",)


def test_mutations_do_not_touch_the_clean_sample(tasks, samples):
    before = json.dumps(samples["dw-fake-smoke"].raw, sort_keys=True), json.dumps(samples["dw-fake-smoke"].files, sort_keys=True)
    run_meta_eval(tasks, samples)
    after = json.dumps(samples["dw-fake-smoke"].raw, sort_keys=True), json.dumps(samples["dw-fake-smoke"].files, sort_keys=True)
    assert before == after
