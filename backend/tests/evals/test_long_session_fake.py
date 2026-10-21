"""U17 mechanics through the real fake-mode stack (Postgres, backend, worker with a scripted model). Needs ONIVA_E2E=1.

One long-session task, shortened to 40 turns, runs in the three arms (`off`, `offload+clear`, `full`). Fake mode checks
the mechanics only, as the plan says: with the tiers on the context per turn is smaller, oversized tool results are
offloaded (preview and pointer), the Tier 1 watermark advances, and nothing above `offloadTokens` stays verbatim.
Quality (probe accuracy against a real model) is the live gate's job.
"""

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest

from evals import arms
from evals.budget import BudgetMeter
from evals.graders.context import text_tokens
from evals.loader import load_tasks
from evals.runner import run_suite

pytestmark = [pytest.mark.e2e, pytest.mark.eval]

EVALS = Path(__file__).resolve().parents[2] / "evals"
TURNS = 40


@pytest.fixture(scope="module")
def short_task():
    task = next(t for t in load_tasks(EVALS / "tasks") if t.id == "ls-research-notes")
    return replace(
        task,
        input={**task.input, "turns": TURNS, "probe_at": [0.6, 1.0], "recap_every": 8, "chat_every": 0, "oversize_every": 3},
    )


@pytest.fixture(scope="module")
def results(short_task):
    out = asyncio.run(run_suite([short_task], mode="fake", k=1, meter=BudgetMeter(None)))
    by_arm = {arms.split_arm(r.trial.task_id)[1]: r for r in out}
    if os.environ.get("EVAL_PRINT_METRICS"):  # a dev aid: pytest -s shows the arm numbers
        for arm, r in by_arm.items():
            print(arm, {k: v for k, v in (r.metrics or {}).items() if k != "series"})
    return by_arm


def test_every_arm_ran_and_passed(results):
    assert set(results) == set(arms.ARMS)
    for arm, r in results.items():
        assert r.status == "ok", (arm, r.error)
        assert not r.fixture_misses, arm
        failed = [(g.grader_id, g.detail) for g in r.grader_results if g.gating and not g.passed]
        assert not failed, (arm, failed)
        assert r.metrics is not None and r.metrics["tiers"] == arm


def test_baseline_arm_keeps_everything_verbatim(results):
    m = results["off"].metrics
    assert m["offloaded_results"] == 0 and m["watermark_advances"] == 0 and m["stub_results_last_turn"] == 0
    assert m["max_stored_result_tokens"] > 8000, "the oversized pages should be stored in full without tiers"


@pytest.mark.parametrize("arm", ["offload+clear", "full"])
def test_tiers_offload_clear_and_shrink_the_context(results, arm):
    base, m = results["off"].metrics, results[arm].metrics
    assert m["offloaded_results"] > 0
    assert m["max_stored_result_tokens"] <= 8000, "no tool result above offloadTokens may stay verbatim"
    assert m["watermark_advances"] >= 2, "the clearing watermark should move forward as results age"
    assert m["final_watermark"] and m["cleared_tokens"] > 0
    assert m["stub_results_last_turn"] > 0
    assert m["token_source"] == "estimate" and base["token_source"] == "estimate"
    assert m["mean_tokens_after"] < 0.5 * base["mean_tokens_after"], (m["mean_tokens_after"], base["mean_tokens_after"])


def test_offloaded_results_carry_a_preview_and_a_pointer(results):
    entries = results["offload+clear"].raw["entries"]
    offloaded = [
        e["payload"] for e in entries
        if e["kind"] == "tool_result" and isinstance((e["payload"].get("details") or {}).get("offloaded"), dict)
    ]
    assert offloaded
    for p in offloaded:
        text = p["content"] if isinstance(p["content"], str) else "\n".join(c["text"] for c in p["content"])
        assert "[full output (~" in text and p["details"]["offloaded"]["path"] in text
        assert text_tokens(text) < 1000, "an offloaded result keeps a short preview, not the page"


def test_probes_are_answered_in_every_arm(results):
    for arm, r in results.items():
        assert r.metrics["probes"]["fact"][0] == r.metrics["probes"]["fact"][1] > 0, arm
        assert r.metrics["probes"]["id"][0] == r.metrics["probes"]["id"][1] > 0, arm


def test_gate_b_reads_the_fake_report(results):
    from evals import gate_b
    from evals import report as rpt

    report = rpt.build_report(list(results.values()), name="fake-gate-b", suite=["long_session"], mode="fake", k=1)
    verdicts = {v.arm: v for v in gate_b.evaluate(report, regressions={"offload+clear": False, "full": False})}
    assert set(verdicts) == {"offload+clear", "full"}
    for arm, v in verdicts.items():
        status = {c.id: c.status for c in v.criteria}
        assert status["tokens"] == status["verbatim"] == status["probe-fact"] == status["probe-id"] == "pass", (arm, status)
        assert status["cache"] == "n/a", "fake usage is declared, so cache share cannot be judged"
    text = gate_b.render_markdown(report, list(verdicts.values()))
    assert "fake mode: the mechanics only" in text


def test_no_compaction_or_overflow_in_the_short_session(results):
    for arm, r in results.items():
        assert r.metrics["compaction_failed"] == 0 and r.metrics["overflow_failures"] == 0, arm
