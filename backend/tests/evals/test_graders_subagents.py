"""U24: the subagent-contract graders over synthetic trace responses (offline)."""

import json
from typing import Any

import pytest

from evals.graders import GradeContext, grade
from evals.graders.subagents import classify_answer, parse_data, with_data
from evals.schema import GraderSpec
from evals.trace import flatten

SUMMARY = {"summary": "s", "learning_count": 1, "source_count": 1, "top_findings": ["f"], "sources": []}
VERDICT = {"unsupported_claims": [], "missing_citations": [], "broken_citations": []}
STEER = "Message from the orchestrator: Focus on the identity verification step only."


def call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"type": "tool_call", "id": call_id, "name": name, "arguments": arguments}


def assistant(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assistant", "payload": {"message": {"blocks": list(blocks)}, "usage": {}}}


def result(tool: str, call_id: str, content: str = "{}", *, status: str = "ok", details: Any = None) -> dict[str, Any]:
    return {
        "kind": "tool_result",
        "payload": {"toolCallId": call_id, "toolName": tool, "status": status, "isError": status != "ok",
                    "content": content, "details": details},
    }


def user(text: str) -> dict[str, Any]:
    return {"kind": "user", "payload": {"content": text}}


def saves(worker: str, urls: list[str], *, ok: bool = True) -> list[dict[str, Any]]:
    learnings = [{"fact": f"f{n}", "source_url": u} for n, u in enumerate(urls, 1)]
    return [
        user(f"WORKER {worker}: q"),
        assistant(call("dw_save_learnings", {"worker_id": worker, "learnings": learnings}, "s1")),
        result("dw_save_learnings", "s1", status="ok" if ok else "error"),
    ]


def worker_result(cid: str, conv: str, call_id: str, content: Any, *, status: str = "ok", extra: dict | None = None):
    text = content if isinstance(content, str) else json.dumps(content)
    return result("research_worker", call_id, text, status=status, details={"conversationId": conv, **(extra or {})})


def trace(root: list[dict], children: dict[str, list[dict]]) -> dict[str, Any]:
    return {
        "conversationId": "root",
        "entries": root,
        "children": {c: {"role": "research_worker", "entries": e} for c, e in children.items()},
    }


def ctx_of(raw: dict, **files: Any) -> GradeContext:
    return GradeContext(trace=flatten(raw), raw=raw, files=files)


def run(gtype: str, ctx: GradeContext, **params: Any) -> dict[str, Any]:
    return grade(GraderSpec(id=gtype, type=gtype, params=params), ctx)


def two_worker_raw(*, urls1, urls2, status2="ok", content2: Any = SUMMARY, saved2=True, extra2=None):
    root = [
        assistant(call("dw_start_round", {"round": 1, "workers": []}, "r1")),
        result("dw_start_round", "r1"),
        assistant(call("research_worker", {}, "a"), call("research_worker", {}, "b")),
        worker_result("a", "c1", "a", SUMMARY),
        worker_result("b", "c2", "b", content2, status=status2, extra=extra2),
    ]
    return trace(root, {"c1": saves("w1", urls1), "c2": saves("w2", urls2, ok=saved2)})


def test_data_roundtrip():
    detail = with_data("text", {"a": 1})
    assert parse_data(detail) == {"a": 1}
    assert parse_data("no data here") is None and parse_data("x || not json") is None


def test_classify_answer():
    assert classify_answer("research_worker", {"status": "ok", "content": json.dumps(SUMMARY)}) == "valid"
    assert classify_answer("research_worker", {"status": "ok", "content": "Done. Saved."}) == "malformed"
    assert classify_answer("research_worker", {"status": "ok", "content": '{"summary": "s"}'}) == "malformed"
    typed = {"status": "ok", "content": "{}", "details": {"result": SUMMARY}}
    assert classify_answer("research_worker", typed) == "typed"
    bad_typed = {"status": "ok", "content": "{}", "details": {"result": {"summary": "s"}}}
    assert classify_answer("research_worker", bad_typed) == "malformed"
    assert classify_answer("verifier", {"status": "ok", "content": json.dumps(VERDICT)}) == "valid"
    assert classify_answer("research_worker", {"status": "error", "content": "x", "details": {"reason": "no_result"}}) == "unanswered"


def test_worker_failure_rate_counts_errors_and_workers_that_saved_nothing():
    ok = run("worker_failure_rate", ctx_of(two_worker_raw(urls1=["u1"], urls2=["u2"])))
    assert ok["passed"] and parse_data(ok["detail"]) == {"workers": 2, "failed": 0}
    unanswered = run(
        "worker_failure_rate",
        ctx_of(two_worker_raw(urls1=["u1"], urls2=["u2"], status2="error", content2="no", extra2={"reason": "no_result"})),
        max_rate=0.5,
    )
    assert unanswered["passed"] and parse_data(unanswered["detail"]) == {"workers": 2, "failed": 1}
    assert "no_result" in unanswered["detail"]
    strict = run(
        "worker_failure_rate",
        ctx_of(two_worker_raw(urls1=["u1"], urls2=["u2"], saved2=False)),
    )
    assert not strict["passed"] and "saved no learnings" in strict["detail"]


def test_worker_failure_rate_marks_injected_failures_as_out_of_the_gate():
    raw = two_worker_raw(urls1=["u1"], urls2=["u2"], status2="error", content2="no", extra2={"reason": "no_result"})
    out = run("worker_failure_rate", ctx_of(raw), max_rate=0.5, gate=False)
    assert parse_data(out["detail"]) == {"workers": 2, "failed": 1, "gate": False}


def test_worker_failure_rate_fails_closed_without_raw_or_workers():
    no_raw = GradeContext(trace=flatten(two_worker_raw(urls1=["a"], urls2=["b"])))
    assert not run("worker_failure_rate", no_raw)["passed"]
    assert not run("worker_failure_rate", ctx_of(trace([assistant()], {})))["passed"]


def test_malformed_worker_answers_separates_malformed_unanswered_and_nudged():
    clean = run("malformed_worker_answers", ctx_of(two_worker_raw(urls1=["a"], urls2=["b"])))
    assert clean["passed"] and parse_data(clean["detail"])["valid"] == 2

    prose = run("malformed_worker_answers", ctx_of(two_worker_raw(urls1=["a"], urls2=["b"], content2="Done, saved.")))
    assert not prose["passed"] and parse_data(prose["detail"])["malformed"] == 1

    typed_extra = {"result": SUMMARY, "nudged": True}
    nudged = run("malformed_worker_answers", ctx_of(two_worker_raw(urls1=["a"], urls2=["b"], extra2=typed_extra)))
    data = parse_data(nudged["detail"])
    assert nudged["passed"] and data["typed"] == 1 and data["nudged"] == 1

    gone = run(
        "malformed_worker_answers",
        ctx_of(two_worker_raw(urls1=["a"], urls2=["b"], status2="error", content2="no result", extra2={"rawText": "x"})),
    )
    assert gone["passed"] and parse_data(gone["detail"])["unanswered"] == 1  # an error result is not a malformed answer


def test_cross_worker_url_overlap_per_round_and_across_rounds():
    same = ["https://x.test/a/", "https://x.test/b"]
    shared = run("cross_worker_url_overlap", ctx_of(two_worker_raw(urls1=same, urls2=["https://X.test/a", "https://x.test/c"])))
    data = parse_data(shared["detail"])
    assert data["urls"] == 4 and data["distinct"] == 3  # trailing slash and case do not hide a repeat
    assert shared["passed"]  # no threshold: a measurement
    assert not run(
        "cross_worker_url_overlap",
        ctx_of(two_worker_raw(urls1=same, urls2=["https://x.test/a", "https://x.test/c"])),
        max_overlap=0.2,
    )["passed"]
    disjoint = run("cross_worker_url_overlap", ctx_of(two_worker_raw(urls1=["a"], urls2=["b"])), max_overlap=0.0)
    assert disjoint["passed"]

    root = [
        assistant(call("dw_start_round", {"round": 1}, "r1")), result("dw_start_round", "r1"),
        worker_result("a", "c1", "a", SUMMARY),
        assistant(call("dw_start_round", {"round": 2}, "r2")), result("dw_start_round", "r2"),
        worker_result("b", "c2", "b", SUMMARY),
    ]
    raw = trace(root, {"c1": saves("w1", ["u1", "u2"]), "c2": saves("w3", ["u1", "u3"])})
    again = run("cross_worker_url_overlap", ctx_of(raw), max_repeat=0.2)
    data = parse_data(again["detail"])
    assert not again["passed"] and data["later_urls"] == 2 and data["repeated"] == 1 and data["urls"] == 0


def test_cross_worker_url_overlap_needs_urls():
    raw = trace([assistant(call("dw_start_round", {"round": 1}, "r")), result("dw_start_round", "r"),
                 worker_result("a", "c1", "a", SUMMARY)], {"c1": [user("WORKER w1: q")]})
    assert not run("cross_worker_url_overlap", ctx_of(raw))["passed"]


LEARNINGS = [{"fact": "f", "source_url": f"https://x.test/{n}", "source_type": "web"} for n in (1, 2)]
BODY = "Prices differ by tier and by contract length, and discounts apply to annual plans only. " * 4


def test_broken_citations_reads_report_and_refused_finalizes():
    report = f"## Findings\n\nTwo tiers [1][2].\n\n## Sources\n\n1. [a](https://x.test/1)\n2. [b](https://x.test/2)\n"
    clean = run("broken_citations", ctx_of(trace([], {}), **{"report.md": report, "learnings": LEARNINGS}))
    assert clean["passed"] and parse_data(clean["detail"]) == {"blocked": 0, "broken": 0, "sources": 2}

    broken = report.replace("[1][2]", "[1][9]")
    bad = run("broken_citations", ctx_of(trace([], {}), **{"report.md": broken, "learnings": LEARNINGS}))
    assert not bad["passed"] and parse_data(bad["detail"])["broken"] == 1

    refused = trace(
        [assistant(call("dw_finalize", {}, "f")), result("dw_finalize", "f", status="error", details={"error": "invalid_citations"})],
        {},
    )
    blocked = run("broken_citations", ctx_of(refused, **{"report.md": report, "learnings": LEARNINGS}))
    assert not blocked["passed"] and parse_data(blocked["detail"])["blocked"] == 1
    assert run("broken_citations", ctx_of(refused, **{"report.md": report, "learnings": LEARNINGS}), max_blocked=1)["passed"]


def test_broken_citations_and_uncited_sections_need_files():
    ctx = ctx_of(trace([], {}))
    assert not run("broken_citations", ctx)["passed"] and not run("uncited_sections", ctx)["passed"]


def test_uncited_sections_flags_long_factual_sections_only():
    report = f"## Findings\n\nTwo tiers [1].\n\n## Background\n\n{BODY}\n\n## Method\n\nShort note.\n"
    out = run("uncited_sections", ctx_of(trace([], {}), **{"report.md": report, "learnings": LEARNINGS}))
    assert not out["passed"] and parse_data(out["detail"]) == {"uncited": 1} and "Background" in out["detail"]
    fixed = report.replace(BODY.strip(), BODY.strip() + " [1]")
    assert run("uncited_sections", ctx_of(trace([], {}), **{"report.md": fixed, "learnings": LEARNINGS}))["passed"]


def steered_raw(*, steer_ok=True, delivered=True, follow=True, calls=True):
    child = [user("WORKER w2: ridgeway onboarding"),
             assistant(call("web_search", {"query": "ridgeway onboarding"}, "q1")), result("web_search", "q1")]
    if delivered:
        child.append(user(STEER))
    child.append(assistant(call("web_search", {"query": "ridgeway identity verification" if follow else "ridgeway fees"}, "q2")))
    root = [assistant(call("steer_worker", {"label": "w2", "message": "x"}, "st"))] if calls else []
    if calls:
        root.append(result("steer_worker", "st", status="ok" if steer_ok else "error"))
    return trace(root, {"c2": child})


@pytest.mark.parametrize(
    ("kwargs", "ok", "why"),
    [
        ({}, True, "received the steer and then worked on"),
        ({"calls": False, "delivered": False}, False, "never called steer_worker"),
        ({"steer_ok": False, "delivered": False}, False, "never reached"),
        ({"follow": False}, False, "do not mention"),
    ],
)
def test_steer_applied(kwargs, ok, why):
    out = run("steer_applied", ctx_of(steered_raw(**kwargs)), worker="w2", keywords=["Identity Verification"])
    assert out["passed"] is ok and why in out["detail"]


def test_steer_applied_needs_the_worker_and_keywords_and_a_conversation():
    with pytest.raises(ValueError, match="needs 'worker' and 'keywords'"):
        run("steer_applied", ctx_of(steered_raw()))
    other = run("steer_applied", ctx_of(steered_raw()), worker="w9", keywords=["x"])
    assert not other["passed"] and "no conversation for worker w9" in other["detail"]
