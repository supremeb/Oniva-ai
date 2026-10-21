import copy
import json
from pathlib import Path

import pytest

from evals.graders import GradeContext, grade, grade_all
from evals.graders import code, deep_work
from evals.schema import GRADER_TYPES, GraderSpec
from evals.trace import flatten

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def ctx_of(raw: dict, **files) -> GradeContext:
    return GradeContext(trace=flatten(raw), raw=raw, files=files)


def tool_result(call_id: str, name: str, *, status="ok", details=None, conv="conv-root") -> dict:
    return {
        "id": f"x-{call_id}",
        "conversationId": conv,
        "kind": "tool_result",
        "payload": {
            "toolCallId": call_id,
            "toolName": name,
            "status": status,
            "content": "",
            "isError": status != "ok",
            "details": details or {},
        },
        "createdAt": 1760000100000,
    }


def assistant(round_no: int, blocks: list[dict], usage: dict | None = None) -> dict:
    return {
        "id": f"a{round_no}",
        "conversationId": "conv-root",
        "kind": "assistant",
        "payload": {
            "round": round_no,
            "usage": usage or {"inputTokens": 10, "outputTokens": 1, "cost": {"total": 0.001}},
            "message": {"model": "fake-1", "blocks": blocks},
        },
        "createdAt": 1760000100000 + round_no,
    }


def call(call_id: str, name: str, **args) -> dict:
    return {"type": "tool_call", "id": call_id, "name": name, "arguments": args}


def synthetic(entries: list[dict], usage: dict | None = None, **extra) -> dict:
    return {
        "conversationId": "conv-root",
        "entries": entries,
        "children": {},
        "usage": usage or {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0, "costUsd": 0},
        "taint": {"tainted": False},
        "compactions": [],
        "truncated": False,
        "nextCursor": None,
        **extra,
    }


# --- shape ---------------------------------------------------------------


def test_grader_types_extended_additively():
    for old in ("tool_order", "budget", "rounds", "cache_hit_ratio", "trajectory_match", "judge"):
        assert old in GRADER_TYPES
    for new in ("no_run_error", "no_compaction_failure", "no_limit_exceeded", "tool_error_rate", "learning_sources"):
        assert new in GRADER_TYPES


def test_result_shape_and_dispatch():
    ctx = ctx_of(load("trace_chat.json"))
    out = grade(GraderSpec(id="b1", type="budget", params={"max_usd": 1.0}), ctx)
    assert set(out) == {"id", "passed", "score", "detail"}
    assert out["id"] == "b1" and out["passed"] is True


def test_judge_is_not_a_code_grader():
    with pytest.raises(ValueError):
        grade(GraderSpec(id="j", type="judge"), ctx_of(load("trace_chat.json")))


def test_grade_all_skips_judges():
    specs = (GraderSpec(id="b", type="budget", params={"max_usd": 1}), GraderSpec(id="j", type="judge"))
    assert [r["id"] for r in grade_all(specs, ctx_of(load("trace_chat.json")))] == ["b"]


# --- budget: cost includes compaction requests ---------------------------


def test_budget_uses_usage_by_role_not_model_spans():
    raw = load("trace_chat.json")
    ctx = ctx_of(raw)
    # model spans sum to 0.009 here, but a compaction request cost more: make the usage doc larger.
    raw2 = copy.deepcopy(raw)
    raw2["usage"]["costUsd"] = 0.5
    over = code.budget(ctx_of(raw2), {"max_usd": 0.1})
    assert over["passed"] is False and "0.5" in over["detail"]
    assert code.budget(ctx, {"max_usd": 0.1})["passed"] is True


def test_budget_tokens_and_rounds():
    ctx = ctx_of(load("trace_chat.json"))
    # prompt tokens = 850 + 500 + 300
    assert code.budget(ctx, {"max_input_tokens": 1650})["passed"] is True
    assert code.budget(ctx, {"max_input_tokens": 1649})["passed"] is False
    assert code.budget(ctx, {"max_rounds": 2})["passed"] is True
    assert code.budget(ctx, {"max_rounds": 1})["passed"] is False


def test_budget_without_limits_is_a_config_error():
    with pytest.raises(ValueError):
        code.budget(ctx_of(load("trace_chat.json")), {})


def test_rounds_grader_counts_root_model_spans_only():
    ctx = ctx_of(load("trace_deep_work.json"))  # 4 root assistant entries, workers have their own
    assert code.rounds(ctx, {"max": 4})["passed"] is True
    assert code.rounds(ctx, {"max": 3})["passed"] is False


# --- cache share ---------------------------------------------------------


def test_cache_hit_ratio_floor():
    ctx = ctx_of(load("trace_chat.json"))  # 500 / 1650
    assert code.cache_hit_ratio(ctx, {"min_share": 0.3})["passed"] is True
    assert code.cache_hit_ratio(ctx, {"min_share": 0.4})["passed"] is False


def test_cache_hit_ratio_zero_input_tokens():
    ctx = ctx_of(synthetic([]))
    assert code.cache_hit_ratio(ctx, {"min_share": 0.0})["passed"] is True
    assert code.cache_hit_ratio(ctx, {"min_share": 0.1})["passed"] is False


# --- run_error / compaction / limit --------------------------------------


def test_no_run_error():
    raw = synthetic([assistant(1, [])])
    assert code.no_run_error(ctx_of(raw), {})["passed"] is True
    raw["entries"].append({"id": "r", "conversationId": "conv-root", "kind": "run_error", "payload": {"error": {"message": "boom"}}, "createdAt": 1})
    out = code.no_run_error(ctx_of(raw), {})
    assert out["passed"] is False and "boom" in out["detail"]


def test_raw_required_fails_closed():
    out = code.no_run_error(GradeContext(trace=flatten(load("trace_chat.json"))), {})
    assert out["passed"] is False and "raw" in out["detail"]


def test_no_compaction_failure_reads_the_submissions():
    """compaction_failed is a submission's `unanswered` reason; it is in no entry (see test_graders_recorded)."""
    raw = synthetic([assistant(1, [])], submissions=[{"id": "s", "requestId": "r", "status": "done", "unanswered": None}])
    assert code.no_compaction_failure(ctx_of(raw), {})["passed"] is True
    raw["submissions"].append(
        {"id": "s2", "requestId": "r2", "status": "unanswered", "unanswered": {"reason": "compaction_failed", "message": "x"}}
    )
    out = code.no_compaction_failure(ctx_of(raw), {})
    assert out["passed"] is False and "r2" in out["detail"]
    assert code.no_compaction_failure(ctx_of(raw), {"allow": True})["passed"] is True
    other = synthetic([], submissions=[{"id": "s", "requestId": "r", "status": "unanswered", "unanswered": {"reason": "model_error", "message": "x"}}])
    assert code.no_compaction_failure(ctx_of(other), {})["passed"] is True


def test_no_compaction_failure_sees_a_failed_child():
    raw = synthetic([], submissions=[])
    raw["children"] = {
        "conv-kid": {
            "role": "research_worker",
            "entries": [],
            "submissions": [{"id": "k", "requestId": "k1", "status": "unanswered", "unanswered": {"reason": "compaction_failed", "message": None}}],
            "usage": {"total": {}},
        }
    }
    assert code.no_compaction_failure(ctx_of(raw), {})["passed"] is False


def test_no_compaction_failure_fails_closed_without_submissions():
    """A trace recorded before the route reported submissions cannot show it, so the check must not pass."""
    for raw in (load("trace_chat.json"), synthetic([])):
        out = code.no_compaction_failure(ctx_of(raw), {})
        assert out["passed"] is False and "submissions" in out["detail"]


def test_no_limit_exceeded_is_the_tool_result_status():
    raw = synthetic([tool_result("c", "web_search", status="limit_exceeded")])
    out = code.no_limit_exceeded(ctx_of(raw), {})
    assert out["passed"] is False and "web_search" in out["detail"]
    # an ordinary tool error that merely mentions the word is not a limit result
    quiet = synthetic([tool_result("c", "web_search", status="error", details={"error": "limit_exceeded"})])
    assert code.no_limit_exceeded(ctx_of(quiet), {})["passed"] is True
    assert code.no_limit_exceeded(ctx_of(load("trace_deep_work.json")), {})["passed"] is True
