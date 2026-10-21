"""The code graders against traces recorded from real fake-mode runs through the stack (tests/evals/test_runner_fake.py).

The synthetic fixtures of U3/U6 were hand-built, and the matchers for `compaction_failed` and `limit_exceeded` were
written from guesses about them. These traces are what the engine really emits:

- a tool call over the per-round cap is a `tool_result` entry with payload `status: "limit_exceeded"`;
- a failed compaction leaves a `run_error` entry carrying the *summary request's* provider error, and nothing named
  `compaction_failed` anywhere in the entries; the reason is on the submission (`submissions[].unanswered.reason`).
"""

import json
from pathlib import Path

import pytest

from evals.graders import GradeContext, code, deep_work, trajectory
from evals.trace import flatten

FIX = Path(__file__).parent / "fixtures"


def raw_of(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def ctx_of(name: str, **files) -> GradeContext:
    raw = raw_of(name)
    return GradeContext(trace=flatten(raw), raw=raw, files=files)


def passes(fn, name: str, params: dict | None = None) -> bool:
    return fn(ctx_of(name), params or {})["passed"]


def test_clean_chat_run_passes_every_health_check():
    for fn in (code.no_run_error, code.no_compaction_failure, code.no_limit_exceeded):
        assert passes(fn, "trace_fake_chat.json"), fn.__name__
    assert code.tool_error_rate(ctx_of("trace_fake_chat.json"), {"max_rate": 0.0})["passed"]
    assert trajectory.trajectory_match(ctx_of("trace_fake_chat.json"), {"mode": "exact", "expected": ["browse_url"]})["passed"]


def test_clean_deep_work_run_passes_its_checks():
    for fn in (code.no_run_error, code.no_compaction_failure, code.no_limit_exceeded, deep_work.tool_order, deep_work.no_failed_workers):
        assert passes(fn, "trace_fake_deep_work.json"), fn.__name__
    workers = [s for s in flatten(raw_of("trace_fake_deep_work.json")).walk() if s.name == "research_worker"]
    assert len(workers) == 2


def test_limit_exceeded_is_the_status_of_the_33rd_tool_result():
    raw = raw_of("trace_fake_limit_exceeded.json")
    refused = [e for e in raw["entries"] if e["kind"] == "tool_result" and e["payload"]["status"] == "limit_exceeded"]
    assert len(refused) == 1 and refused[0]["payload"]["isError"] is True
    # there is no reason / error / code key to find it by, which is what the first matcher looked for
    assert not {"reason", "error", "code", "errorCode"} & set(refused[0]["payload"])

    out = code.no_limit_exceeded(ctx_of("trace_fake_limit_exceeded.json"), {})
    assert out["passed"] is False and "browse_url" in out["detail"]
    assert passes(code.no_run_error, "trace_fake_limit_exceeded.json")  # the run itself went on and answered
    assert passes(code.no_compaction_failure, "trace_fake_limit_exceeded.json")
    assert code.tool_error_rate(ctx_of("trace_fake_limit_exceeded.json"), {"max_rate": 0.0})["passed"] is False


def test_compaction_failed_is_not_in_the_entries_only_on_the_submission():
    raw = raw_of("trace_fake_compaction_failed.json")
    assert "compaction_failed" not in json.dumps(raw["entries"])
    errors = [e for e in raw["entries"] if e["kind"] == "run_error"]
    assert len(errors) == 1 and errors[0]["payload"]["error"]["kind"] == "invalid_request"  # the provider's error
    unanswered = [s for s in raw["submissions"] if s["status"] == "unanswered"]
    assert [s["unanswered"]["reason"] for s in unanswered] == ["compaction_failed"]

    out = code.no_compaction_failure(ctx_of("trace_fake_compaction_failed.json"), {})
    assert out["passed"] is False and unanswered[0]["requestId"] in out["detail"]
    assert passes(code.no_compaction_failure, "trace_fake_compaction_failed.json", {"allow": True})
    assert not passes(code.no_run_error, "trace_fake_compaction_failed.json")
    assert passes(code.no_limit_exceeded, "trace_fake_compaction_failed.json")


def test_an_ordinary_model_error_is_not_a_compaction_failure():
    raw = raw_of("trace_fake_run_error.json")
    assert [s["unanswered"]["reason"] for s in raw["submissions"] if s["status"] == "unanswered"] == ["model_error"]
    assert not passes(code.no_run_error, "trace_fake_run_error.json")
    assert passes(code.no_compaction_failure, "trace_fake_run_error.json")


@pytest.mark.parametrize(
    "name",
    [
        "trace_fake_chat.json",
        "trace_fake_deep_work.json",
        "trace_fake_limit_exceeded.json",
        "trace_fake_compaction_failed.json",
        "trace_fake_run_error.json",
    ],
)
def test_recorded_traces_flatten_and_carry_tree_and_usage(name):
    raw = raw_of(name)
    flatten(raw)
    assert raw["entries"]
    assert raw["submissions"] and raw["usage"]["requests"] >= 1
    assert raw["truncated"] is False
