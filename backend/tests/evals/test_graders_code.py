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


def test_no_compaction_failure_and_allowed():
    raw = synthetic([{"id": "u", "conversationId": "conv-root", "kind": "unanswered", "payload": {"reason": "compaction_failed"}, "createdAt": 1}])
    assert code.no_compaction_failure(ctx_of(raw), {})["passed"] is False
    assert code.no_compaction_failure(ctx_of(raw), {"allow": True})["passed"] is True
    assert code.no_compaction_failure(ctx_of(load("trace_chat.json")), {})["passed"] is True


def test_no_limit_exceeded_in_tool_results():
    raw = synthetic([tool_result("c", "web_search", status="error", details={"error": "limit_exceeded"})])
    out = code.no_limit_exceeded(ctx_of(raw), {})
    assert out["passed"] is False and "limit_exceeded" in out["detail"]
    assert code.no_limit_exceeded(ctx_of(load("trace_deep_work.json")), {})["passed"] is True


# --- tool error rate ------------------------------------------------------


def test_tool_error_rate():
    raw = synthetic(
        [
            assistant(1, [call("a", "t1"), call("b", "t2"), call("c", "t3"), call("d", "t4")]),
            tool_result("a", "t1"),
            tool_result("b", "t2"),
            tool_result("c", "t3"),
            tool_result("d", "t4", status="error"),
        ]
    )
    ctx = ctx_of(raw)
    assert code.tool_error_rate(ctx, {"max_rate": 0.25})["passed"] is True
    assert code.tool_error_rate(ctx, {"max_rate": 0.2})["passed"] is False


def test_tool_error_rate_no_tools():
    assert code.tool_error_rate(ctx_of(synthetic([])), {"max_rate": 0.0})["passed"] is True


# --- deep work -------------------------------------------------------------


def dw_ok() -> GradeContext:
    raw = load("trace_deep_work.json")
    return ctx_of(
        raw,
        **{
            "report.md": "Finding one [1]. Finding two [2].",
            "learnings": [
                {"fact": "a", "source_url": "https://a.example/x", "source_type": "web"},
                {"fact": "b", "source_url": "https://b.example", "source_type": "web"},
            ],
            "fixture_urls": ["https://a.example/x", "https://b.example/"],
        },
    )


def test_dw_clean_trace_passes_every_check():
    ctx = dw_ok()
    for fn, params in (
        (deep_work.tool_order, {}),
        (deep_work.no_failed_workers, {}),
        (deep_work.citation_map, {}),
        (deep_work.learning_sources, {}),
        (deep_work.url_resolves, {}),
        (deep_work.rounds_within_plan, {"max_rounds": 3}),
    ):
        out = fn(ctx, params)
        assert out["passed"] is True, (fn.__name__, out)


@pytest.mark.parametrize("sig", ["round_order", "round_open", "no_sections"])
def test_dw_tool_order_errors(sig):
    raw = load("trace_deep_work.json")
    raw["entries"].append(tool_result("zz", "dw_start_round", status="error", details={"error": sig}))
    out = deep_work.tool_order(ctx_of(raw), {})
    assert out["passed"] is False and sig in out["detail"]


def test_dw_failed_worker_status():
    raw = load("trace_deep_work.json")
    for e in raw["entries"]:
        if e["kind"] == "tool_result" and e["payload"]["toolCallId"] == "tc3":
            e["payload"]["status"] = "error"
    out = deep_work.no_failed_workers(ctx_of(raw), {})
    assert out["passed"] is False


def test_dw_missing_worker_save():
    raw = load("trace_deep_work.json")
    w2 = raw["children"]["conv-w2"]
    w2["entries"] = [
        e
        for e in w2["entries"]
        if not (e["kind"] == "tool_result" and e["payload"]["toolName"] == "dw_save_learning")
    ]
    for e in w2["entries"]:
        if e["kind"] == "assistant":
            e["payload"]["message"]["blocks"] = [
                b for b in e["payload"]["message"]["blocks"] if b.get("name") != "dw_save_learning"
            ]
    out = deep_work.no_failed_workers(ctx_of(raw), {})
    assert out["passed"] is False and "save" in out["detail"]


def test_dw_failed_workers_argument():
    raw = load("trace_deep_work.json")
    raw["entries"].append(assistant(9, [call("fin", "dw_finalize", failed_workers=["w2"])]))
    out = deep_work.no_failed_workers(ctx_of(raw), {})
    assert out["passed"] is False and "w2" in out["detail"]


def test_dw_citation_out_of_range():
    ctx = dw_ok()
    bad = GradeContext(trace=ctx.trace, raw=ctx.raw, files={**ctx.files, "report.md": "Claim [9]. Other [1]."})
    out = deep_work.citation_map(bad, {})
    assert out["passed"] is False and "9" in out["detail"]


def test_dw_citation_ignores_markdown_links_and_needs_report():
    ctx = dw_ok()
    links = GradeContext(trace=ctx.trace, raw=ctx.raw, files={**ctx.files, "report.md": "See [docs](https://x.y) [1]."})
    assert deep_work.citation_map(links, {})["passed"] is True
    missing = GradeContext(trace=ctx.trace, raw=ctx.raw, files={"learnings": []})
    assert deep_work.citation_map(missing, {})["passed"] is False


def test_dw_learning_without_url():
    ctx = dw_ok()
    learnings = [*ctx.files["learnings"], {"fact": "c", "source_type": "web"}]
    bad = GradeContext(trace=ctx.trace, raw=ctx.raw, files={**ctx.files, "learnings": learnings})
    assert deep_work.learning_sources(bad, {})["passed"] is False
    learnings[-1] = {"fact": "c", "source_type": "model_knowledge"}
    ok = GradeContext(trace=ctx.trace, raw=ctx.raw, files={**ctx.files, "learnings": learnings})
    assert deep_work.learning_sources(ok, {})["passed"] is True


def test_dw_url_not_in_fixtures():
    ctx = dw_ok()
    learnings = [*ctx.files["learnings"], {"fact": "c", "source_url": "https://made-up.example/", "source_type": "web"}]
    bad = GradeContext(trace=ctx.trace, raw=ctx.raw, files={**ctx.files, "learnings": learnings})
    out = deep_work.url_resolves(bad, {})
    assert out["passed"] is False and "made-up" in out["detail"]


def test_dw_rounds_within_plan():
    raw = synthetic(
        [
            assistant(1, [call("r1", "dw_start_round", round=1), call("r2", "dw_start_round", round=2)]),
            tool_result("r1", "dw_start_round"),
            tool_result("r2", "dw_start_round"),
        ]
    )
    assert deep_work.rounds_within_plan(ctx_of(raw), {"max_rounds": 2})["passed"] is True
    assert deep_work.rounds_within_plan(ctx_of(raw), {"max_rounds": 1})["passed"] is False


def test_dispatch_covers_all_code_types():
    ctx = dw_ok()
    for t, params in (
        ("tool_order", {}),
        ("no_failed_workers", {}),
        ("citation_map", {}),
        ("url_resolves", {}),
        ("rounds", {"max": 5}),
        ("learning_sources", {}),
        ("no_run_error", {}),
        ("tool_error_rate", {"max_rate": 0.5}),
    ):
        assert grade(GraderSpec(id=t, type=t, params=params), ctx)["passed"] is True, t
