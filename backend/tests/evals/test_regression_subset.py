"""U10 against the real fake-mode stack (Postgres, backend, a worker with a scripted model). Needs ONIVA_E2E=1.

- the `regression` tag passes, three runs in a row have the same shape per task (Gate A), and the strict comparator
  finds no regression against the committed baseline (what the `evals` CI job does);
- two faults injected through the scripted model are caught by the graders built for them (the stack half of the
  mutation meta-eval; `test_mutations.py` covers the rest offline).
"""

from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from evals import report as rpt
from evals.budget import BudgetMeter
from evals.loader import load_tasks, select_by_tags
from evals.runner import entry_kinds, run_on_stack
from evals.stack import eval_stack

pytestmark = [pytest.mark.e2e, pytest.mark.eval]

EVALS = Path(__file__).resolve().parents[2] / "evals"
K = 3


@pytest.fixture(scope="module")
def regression_tasks():
    return select_by_tags(load_tasks(EVALS / "tasks"), ["regression"])


@pytest.fixture(scope="module")
def stack():
    with eval_stack("fake", web="replay") as ew:
        yield ew


@pytest.fixture
async def http():
    async with httpx.AsyncClient(timeout=60) as client:
        yield client


async def test_regression_tag_is_stable_and_matches_the_committed_baseline(stack, http, regression_tasks):
    replay = [t for t in regression_tasks if t.environment.web == "replay"]
    assert replay, "no regression task uses replayed web"
    results = await run_on_stack(stack, replay, K, BudgetMeter(None), http)
    for t in replay:
        trials = [results[(t.id, i)] for i in range(K)]
        assert all(r.passed for r in trials), (t.id, [g for r in trials for g in r.grader_results if not g.passed])
        shapes = [entry_kinds(r.raw) for r in trials]
        assert shapes[0] == shapes[1] == shapes[2], f"{t.id}: {shapes}"
    current = rpt.build_report(results.values(), name="ci", suite=["regression"], mode="fake", k=K)
    baseline = rpt.load_report(EVALS / "baselines" / "regression-fake.json")
    scoped = {**baseline, "tasks": {tid: e for tid, e in baseline["tasks"].items() if tid in current["tasks"]}}
    diff = rpt.diff_reports(scoped, current, strict=True)
    assert not diff.regressed, rpt.render_diff_markdown(diff, baseline_name="baseline", current_name="ci")


async def test_faults_injected_through_the_scripted_model_are_caught(stack, http, regression_tasks):
    by_id = {t.id: t for t in regression_tasks}
    # citations to a source number the report does not list
    dw = replace(by_id["dw-fake-smoke"], fake={**by_id["dw-fake-smoke"].fake, "findings_md": "Vendor one lists a price [1][9]."})
    # the model answers from memory instead of reading the page it was asked to summarise
    chat = replace(
        by_id["chat-fake-browse"],
        fake={"rules": [{"when": {"role": "user", "includes": "pricing"}, "respond": {"steps": [{"type": "text", "text": "29 EUR"}]}}]},
    )
    results = await run_on_stack(stack, [dw, chat], 1, BudgetMeter(None), http)
    dw_failed = {g.grader_id for g in results[(dw.id, 0)].grader_results if g.gating and not g.passed}
    chat_failed = {g.grader_id for g in results[(chat.id, 0)].grader_results if g.gating and not g.passed}
    assert "citations-map" in dw_failed
    assert "read-the-page" in chat_failed
    assert not results[(dw.id, 0)].passed and not results[(chat.id, 0)].passed
