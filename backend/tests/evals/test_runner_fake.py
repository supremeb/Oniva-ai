"""U5: the runner against the real stack in fake mode (Postgres, backend, a real worker with a scripted model).

Skipped unless ONIVA_E2E=1 (see tests/evals/conftest.py). Nothing here reaches a provider.
`EVAL_RECORD_TRACES=<dir>` also writes each task's trimmed raw trace there (how the `trace_fake_*.json` fixtures
were recorded; see `record_name`).
"""

import json
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from evals.budget import BudgetMeter
from evals.fixture_trim import trim_trace
from evals.loader import load_task
from evals.runner import entry_kinds, run_on_stack, run_trial
from evals.schema import Budget
from evals.stack import eval_stack

pytestmark = pytest.mark.e2e

CORPUS = Path(__file__).resolve().parents[2] / "evals" / "tasks"
CASES = Path(__file__).parent / "tasks"
RECORD = {
    "chat-fake-browse": "trace_fake_chat.json",
    "dw-fake-smoke": "trace_fake_deep_work.json",
    "fail-limit-exceeded": "trace_fake_limit_exceeded.json",
    "fail-compaction": "trace_fake_compaction_failed.json",
    "fail-run-error": "trace_fake_run_error.json",
}


def record_name(task_id: str) -> str | None:
    return RECORD.get(task_id) if os.environ.get("EVAL_RECORD_TRACES") else None


@pytest.fixture(scope="module")
def stack():
    with eval_stack("fake", web="replay") as ew:
        yield ew


@pytest.fixture
async def http():
    async with httpx.AsyncClient(timeout=60) as client:
        yield client


def task(name: str):
    return load_task((CORPUS if (CORPUS / name).exists() else CASES) / name)


async def run(stack, http, name: str, index: int = 0):
    res = await run_trial(stack, task(name), index, http)
    if (out := record_name(res.trial.task_id)) and res.raw:
        Path(os.environ["EVAL_RECORD_TRACES"], out).write_text(json.dumps(trim_trace(res.raw), indent=1) + "\n")
    return res


async def test_chat_and_deep_work_tasks_complete(stack, http):
    for name in ("chat-fake-browse.yaml", "dw-fake-smoke.yaml"):
        res = await run(stack, http, name)
        assert res.status == "ok" and res.error is None, res.error
        assert res.fixture_misses == ()
        assert res.passed, [g for g in res.grader_results if not g.passed]
        assert res.trace is not None and len(res.trace.root.children) > 0
        assert res.raw and res.raw["entries"] and res.raw["submissions"]
        assert res.ledger and res.ledger["complete"], res.ledger
        assert res.cost_usd > 0  # priced by the backend ledger; the engine's usage documents carry no dollars
        assert set(res.phases) >= {"setup", "run", "trace"}
    # Deep Work: the workers' subtrees and the files the graders read came through
    dw = res
    assert sum(1 for s in dw.trace.walk() if s.kind == "subagent" and s.name == "research_worker") == 2
    assert {g.grader_id for g in dw.grader_results} >= {"citations-map", "urls-resolve", "tool-order", "workers-ok"}


async def test_repeated_fake_runs_have_identical_shape(stack, http):
    shapes = {}
    for name in ("chat-fake-browse.yaml", "dw-fake-smoke.yaml"):
        runs = [await run(stack, http, name, i) for i in range(3)]
        assert [r.passed for r in runs] == [True, True, True]
        shapes[name] = [entry_kinds(r.raw) for r in runs]
        assert shapes[name][0] == shapes[name][1] == shapes[name][2], name


async def test_forced_run_error_is_a_failed_trial_not_an_exception(stack, http):
    res = await run(stack, http, "fail-run-error.yaml")
    assert res.status == "run_error" and res.error_code == "invalid_request"
    assert not res.passed
    assert [g.passed for g in res.grader_results] == [False]  # no_run_error caught it
    unanswered = [s for s in res.raw["submissions"] if s["status"] == "unanswered"]
    assert unanswered and unanswered[0]["unanswered"]["reason"] == "model_error"


async def test_failures_the_graders_must_see(stack, http):
    """How limit_exceeded and compaction_failed really appear (the fixtures the grader tests use)."""
    limit = await run(stack, http, "fail-limit-exceeded.yaml")
    failed = {g.grader_id for g in limit.grader_results if not g.passed}
    assert "no-limit-exceeded" in failed and "no-run-error" not in failed

    compaction = await run(stack, http, "fail-compaction.yaml")
    failed = {g.grader_id for g in compaction.grader_results if not g.passed}
    assert "no-compaction-failure" in failed
    reasons = [s["unanswered"]["reason"] for s in compaction.raw["submissions"] if s["status"] == "unanswered"]
    assert reasons == ["compaction_failed"]
    assert not any("compaction_failed" in json.dumps(e["payload"]) for e in compaction.raw["entries"])


async def test_web_call_without_a_fixture_fails_the_trial(stack, http, tmp_path):
    (tmp_path / "t.yaml").write_text(
        """
id: chat-no-fixture
kind: chat
input:
  turns: ["Summarise https://example.com/unrecorded"]
  selections: { web_search: true }
fake:
  rules:
    - when: { role: user, includes: "unrecorded" }
      respond: { steps: [{ type: tool_call, name: browse_url, arguments: { url: "https://example.com/unrecorded" } }] }
    - when: { role: tool_result, tool: browse_url }
      respond: { steps: [{ type: text, text: "could not read it" }] }
"""
    )
    res = await run_trial(stack, load_task(tmp_path / "t.yaml"), 0, http)
    assert res.status == "fixture_miss" and len(res.fixture_misses) == 1
    assert not res.passed and res.error.startswith("fixture_miss")


async def test_budget_cap_skips_remaining_trials(stack, http):
    chat = replace(task("chat-fake-browse.yaml"), budget=Budget())
    meter = BudgetMeter(0.004)  # one chat trial costs about $0.003
    results = await run_on_stack(stack, [chat], 3, meter, http)
    statuses = [results[(chat.id, i)].status for i in range(3)]
    assert statuses == ["ok", "skipped_budget", "skipped_budget"]
    assert not results[(chat.id, 1)].passed and results[(chat.id, 1)].cost_usd == 0.0
    assert 0 < meter.spent <= 0.004

