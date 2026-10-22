"""U24: the subagent-contract suite through the real fake stack, baseline arm (contract v2 off) against the v2 arm.

Skipped unless ONIVA_E2E=1. Nothing here reaches a provider. The scripted model plays the same scenario twice (see
`tests.e2e.dw_script.subagent_rules`); what differs is the stack: with the v2 flag the real backend builds the v2
Deep Work config and the real engine validates typed results, nudges a worker that answered in text, runs the
verifier and delivers `steer_worker` messages. The graders must tell the two apart on these tasks.

`EVAL_DW_SUBAGENTS_ALL=1` runs all ten tasks per arm (about ten minutes) instead of the CI subset.
"""

import os
from pathlib import Path

import httpx
import pytest

from evals.budget import BudgetMeter
from evals.graders.subagents import parse_data
from evals.loader import load_tasks
from evals.runner import run_on_stack
from evals.stack import eval_stack

pytestmark = pytest.mark.e2e

TASKS_DIR = Path(__file__).resolve().parents[2] / "evals" / "tasks" / "dw_subagents"
CI_SUBSET = (
    "dws-malformed-one-worker",
    "dws-malformed-no-recovery",
    "dws-overlapping-subqueries",
    "dws-bait-broken-index",
    "dws-steer-focus-worker",
)


def selected():
    tasks = load_tasks(TASKS_DIR)
    if os.environ.get("EVAL_DW_SUBAGENTS_ALL") == "1":
        return tasks
    return [t for t in tasks if t.id in CI_SUBSET]


async def run_arm(arm: str, tasks):
    async with httpx.AsyncClient(timeout=60) as http:
        with eval_stack("fake", web="replay", arm=arm) as ew:
            return await run_on_stack(ew, tasks, 1, BudgetMeter(None), http)


@pytest.fixture(scope="module")
async def arms():
    tasks = selected()
    return {arm: await run_arm(arm, tasks) for arm in ("baseline", "v2")}


def grader(results, task_id, grader_id):
    res = results[(task_id, 0)]
    assert res.status == "ok", (task_id, res.status, res.error)
    return next(g for g in res.grader_results if g.grader_id == grader_id)


def data(results, task_id, grader_id):
    return parse_data(grader(results, task_id, grader_id).detail)


def failed_ids(results, task_id):
    return {g.grader_id for g in results[(task_id, 0)].grader_results if g.gating and not g.passed}


def test_every_trial_runs_to_the_end_in_both_arms(arms):
    for arm, results in arms.items():
        for (tid, _), res in results.items():
            assert res.status == "ok" and res.error is None, (arm, tid, res.status, res.error)
            assert res.fixture_misses == (), (arm, tid)
            assert res.trace is not None and res.raw and "report.md" not in (res.error or "")


def test_a_malformed_worker_reaches_the_orchestrator_only_without_the_contract(arms):
    base, v2 = arms["baseline"], arms["v2"]
    tid = "dws-malformed-one-worker"
    assert data(base, tid, "malformed")["malformed"] == 1 and "malformed" in failed_ids(base, tid)
    after = data(v2, tid, "malformed")
    assert after["malformed"] == 0 and after["nudged"] == 1 and after["typed"] == after["answers"]
    assert failed_ids(v2, tid) == set()
    # the child that answered in text was asked once for complete_task and then ended with a typed result
    reminded = [
        e for c in v2[(tid, 0)].raw["children"].values() for e in c["entries"]
        if e["kind"] == "user" and "complete_task" in str(e["payload"].get("content"))
    ]
    assert len(reminded) == 1


def test_a_worker_that_ignores_the_reminder_becomes_an_error_result_with_its_raw_text(arms):
    tid = "dws-malformed-no-recovery"
    assert data(arms["baseline"], tid, "malformed")["malformed"] == 1
    after = data(arms["v2"], tid, "malformed")
    assert after["malformed"] == 0 and after["unanswered"] == 1  # the orchestrator was told, not misled
    failures = data(arms["v2"], tid, "workers-ok")
    assert failures == {"workers": 2, "failed": 1, "gate": False}  # counted, but kept out of Gate C's rate
    details = [
        e["payload"]["details"] for e in arms["v2"][(tid, 0)].raw["entries"]
        if e["kind"] == "tool_result" and e["payload"].get("toolName") == "research_worker" and e["payload"]["status"] != "ok"
    ]
    assert len(details) == 1 and details[0]["reason"] == "no_result" and "Plain-text recap" in details[0]["rawText"]


def test_overlapping_subqueries_share_sources_only_without_the_contract(arms):
    tid = "dws-overlapping-subqueries"
    before, after = data(arms["baseline"], tid, "url-overlap"), data(arms["v2"], tid, "url-overlap")
    assert before["urls"] - before["distinct"] == 3 and "url-overlap" in failed_ids(arms["baseline"], tid)
    assert after["urls"] == after["distinct"] and failed_ids(arms["v2"], tid) == set()


def test_the_verifier_repairs_a_draft_before_the_first_finalize(arms):
    tid = "dws-bait-broken-index"
    before, after = data(arms["baseline"], tid, "citations-clean"), data(arms["v2"], tid, "citations-clean")
    assert before["blocked"] == 1 and before["broken"] == 0  # production refused the first finalize
    assert after == {"blocked": 0, "broken": 0, "sources": 4}
    verifier = [
        e for e in arms["v2"][(tid, 0)].raw["entries"]
        if e["kind"] == "tool_result" and e["payload"].get("toolName") == "verifier"
    ]
    assert len(verifier) == 1 and verifier[0]["payload"]["details"]["result"]["broken_citations"]
    assert not any(s.name == "verifier" for s in arms["baseline"][(tid, 0)].trace.walk())


def test_a_steer_reaches_the_worker_only_with_steer_worker(arms):
    tid = "dws-steer-focus-worker"
    assert not grader(arms["baseline"], tid, "steered").passed
    assert "never called steer_worker" in grader(arms["baseline"], tid, "steered").detail
    won = data(arms["v2"], tid, "steered")
    assert won == {"changed": True, "delivered": True, "steer_calls": 1, "steer_ok": 1}


def test_gate_c_tells_the_arms_apart_from_their_reports(arms):
    from evals import gate_c
    from evals.report import build_report

    reports = {
        arm: build_report([r for r in results.values()], name=arm, suite=["dw_subagents"], mode="fake", k=1)
        for arm, results in arms.items()
    }
    base, v2 = gate_c.measure(reports["baseline"]), gate_c.measure(reports["v2"])
    verdicts = {c.name: c.passed for c in gate_c.evaluate(base, v2)}
    assert v2.malformed == 0 < base.malformed and v2.nudged >= 1
    assert base.overlap > 0.3 and v2.overlap == 0
    assert base.blocked_finalizes == 1 and v2.blocked_finalizes == 0
    assert (base.steer_trials, base.steer_applied, v2.steer_trials, v2.steer_applied) == (1, 0, 1, 1)
    assert verdicts["failed-worker rate"] is True
    assert verdicts["malformed worker answers reaching the orchestrator"] is True
    assert verdicts["cross-worker source-URL overlap per round"] is True
    assert verdicts["broken citations in the final report"] is True
    assert verdicts["steered worker's behaviour changes"] is True
    assert verdicts["unsupported-claim rate (judge)"] is None  # fake mode runs no judge
