"""Context graders and per-trial metrics (U17), over hand-built raw traces. Offline."""

import pytest

from evals.graders import GradeContext, grade
from evals.graders.context import (
    EVAL_CONTEXT_KEY,
    clear_stub,
    normalize_answer,
    probe_outcomes,
    rebuild_context,
    reread_stats,
    text_tokens,
    trial_metrics,
    turn_series,
)
from evals.schema import GraderSpec, Span, Trace


class Builder:
    """Raw trace entries in the engine's shape, with sequential ids."""

    def __init__(self):
        self.entries = []
        self.samples = []
        self.probes = []
        self.n = 0

    def add(self, kind, payload):
        eid = f"e{len(self.entries) + 1:03d}"
        self.entries.append({"id": eid, "kind": kind, "payload": payload, "createdAt": len(self.entries)})
        return eid

    def turn(self, text, *, calls=(), answer="ok", usage=None, mark=None, cleared=0):
        """One turn: user, then per call an assistant round and its result, then the final answer."""
        self.n += 1
        rid = f"r{self.n}"
        self.add("user", {"requestId": rid, "content": text})
        for i, (name, args, content, details) in enumerate(calls):
            cid = f"c{self.n}-{i}"
            self.add("assistant", {"message": {"blocks": [{"type": "tool_call", "id": cid, "name": name, "arguments": args}]},
                                   "usage": usage or {}})
            self.add("tool_result", {"toolCallId": cid, "toolName": name, "content": content, "details": details or {}})
        self.add("assistant", {"message": {"blocks": [{"type": "text", "text": answer}]}, "usage": usage or {}})
        self.samples.append({"n": self.n, "requestId": rid, "clearedThroughEntryId": mark, "clearedTokens": cleared})
        return rid

    def raw(self, tiers="full", **extra):
        ec = {"tiers": tiers, "turns": self.n, "samples": self.samples, "probes": self.probes, **extra}
        return {"entries": self.entries, "submissions": [], EVAL_CONTEXT_KEY: ec}


def page(tokens):
    return "x" * (tokens * 4)


def ctx(raw):
    return GradeContext(trace=Trace(root=Span(kind="subagent", name="root")), raw=raw)


def run(type_, raw, **params):
    return grade(GraderSpec(id="g", type=type_, params=params), ctx(raw))


def test_clear_stub_matches_the_engine_wording():
    assert clear_stub("browse_url", 120, {}) == "[cleared: browse_url result, ~120 tokens. Re-run the tool.]"
    off = {"offloaded": {"path": "tool_results/c1.txt", "bytes": 5}}
    assert clear_stub("browse_url", 120, off) == (
        "[cleared: browse_url result, ~120 tokens. Re-run the tool, or read tool_results/c1.txt if it was saved.]"
    )


def test_rebuild_context_replaces_results_up_to_the_watermark_with_stubs():
    b = Builder()
    b.turn("first", calls=[("browse_url", {"url": "a"}, page(1000), None)])
    b.turn("second", calls=[("browse_url", {"url": "b"}, page(1000), None)])
    first_result = next(e["id"] for e in b.entries if e["kind"] == "tool_result")
    plain = rebuild_context(b.entries, None)
    cleared = rebuild_context(b.entries, first_result)
    assert plain["stubs"] == 0 and cleared["stubs"] == 1
    assert plain["verbatim_max"] == 1000
    assert plain["tokens"] - cleared["tokens"] == 1000 - text_tokens(clear_stub("browse_url", 1000, {}))


def test_excluded_tools_are_never_stubbed_and_orphan_results_are_dropped():
    b = Builder()
    b.turn("t", calls=[("update_todo", {}, page(500), None), ("browse_url", {}, page(500), None)])
    last = [e["id"] for e in b.entries if e["kind"] == "tool_result"][-1]
    out = rebuild_context(b.entries, last)
    assert out["stubs"] == 1  # browse_url cleared, update_todo kept verbatim
    b.add("tool_result", {"toolCallId": "nobody", "toolName": "browse_url", "content": page(9000)})
    assert rebuild_context(b.entries, None)["tokens"] == rebuild_context(b.entries[:-1], None)["tokens"]


def test_rebuild_context_starts_from_the_newest_compaction():
    b = Builder()
    b.turn("old", calls=[("browse_url", {}, page(4000), None)])
    first_kept = b.turn("kept")
    b.add("compaction", {"summary": "s" * 400, "firstKeptEntryId": next(e["id"] for e in b.entries if e["payload"].get("requestId") == first_kept)})
    out = rebuild_context(b.entries, None)
    assert out["tokens"] < 500, "the old 4000-token page is behind the summary"


def test_turn_series_uses_measured_usage_when_it_exists_and_the_estimate_otherwise():
    b = Builder()
    b.turn("a", calls=[("browse_url", {}, page(300), None)], usage={"inputTokens": 50, "cacheReadTokens": 900, "cacheWriteTokens": 10})
    raw = b.raw()
    assert turn_series(raw)[0]["tokens"] == 960 and turn_series(raw)[0]["source"] == "usage"
    est = turn_series(raw, source="estimate")[0]
    assert est["source"] == "estimate" and 300 < est["tokens"] < 400
    b2 = Builder()
    b2.turn("a", calls=[("browse_url", {}, page(300), None)])
    assert turn_series(b2.raw())[0]["source"] == "estimate"  # nothing measured: auto falls back


def test_reread_after_clear_counts_only_repeats_of_cleared_calls():
    b = Builder()
    b.turn("1", calls=[("browse_url", {"url": "a"}, page(10), None)])
    cleared_through = next(e["id"] for e in b.entries if e["kind"] == "tool_result")
    # turn 2: the watermark has passed turn 1's result; the model repeats it (a reread) and opens a new page
    b.turn("2", calls=[("browse_url", {"url": "a"}, page(10), None), ("browse_url", {"url": "c"}, page(10), None)], mark=cleared_through)
    b.samples[0]["clearedThroughEntryId"] = None
    stats = reread_stats(b.raw())
    assert stats == {"clears": 1, "rereads": 1}
    # a repeat before anything was cleared is not a reread
    b2 = Builder()
    b2.turn("1", calls=[("browse_url", {"url": "a"}, page(10), None)])
    b2.turn("2", calls=[("browse_url", {"url": "a"}, page(10), None)])
    assert reread_stats(b2.raw()) == {"clears": 0, "rereads": 0}


def test_probe_answers_are_matched_after_normalising_numbers():
    assert normalize_answer("18,500 Euros") == "18500 euros"
    b = Builder()
    rid = b.turn("What is the cap?", answer="It is 18500 euros.")
    rid2 = b.turn("Who?", answer="I do not remember.")
    b.probes = [
        {"id": "cap", "kind": "fact", "turn": 1, "requestId": rid, "expect": ["18,500"]},
        {"id": "who", "kind": "id", "turn": 2, "requestId": rid2, "expect": ["TKT-1"]},
    ]
    rows = {r["id"]: r["hit"] for r in probe_outcomes(b.raw())}
    assert rows == {"cap": True, "who": False}
    assert run("probe_recall", b.raw(), kind="id")["passed"]  # no floor given: it only reports
    assert run("probe_recall", b.raw(), kind="fact", min_rate=1.0)["passed"]
    assert not run("probe_recall", b.raw(), kind="id", min_rate=1.0)["passed"]
    assert "missed who@2" in run("probe_recall", b.raw(), kind="all", min_rate=1.0)["detail"]


def test_no_verbatim_oversize_applies_only_with_tiers_on():
    b = Builder()
    b.turn("t", calls=[("browse_url", {}, page(9000), None)])
    assert run("no_verbatim_oversize", b.raw(tiers="off"))["passed"]
    out = run("no_verbatim_oversize", b.raw(tiers="full"))
    assert not out["passed"] and "9000" in out["detail"]
    ok = Builder()
    ok.turn("t", calls=[("browse_url", {}, page(500), {"offloaded": {"path": "p"}})])
    assert run("no_verbatim_oversize", ok.raw(tiers="full"))["passed"]


def test_graders_fail_closed_without_the_drivers_context():
    plain = {"entries": [], "submissions": []}
    for t in ("context_tokens", "probe_recall", "reread_after_clear", "no_verbatim_oversize"):
        out = run(t, plain)
        assert not out["passed"] and "evalContext" in out["detail"], t
    assert not run("context_tokens", None)["passed"]


def test_context_tokens_gates_only_with_a_cap():
    b = Builder()
    for i in range(33):
        b.turn(f"t{i}", calls=[("browse_url", {"url": str(i)}, page(100), None)])
    out = run("context_tokens", b.raw())
    assert out["passed"] and "after turn 30" in out["detail"]
    assert not run("context_tokens", b.raw(), max_mean=10)["passed"]
    short = Builder()
    short.turn("only")
    assert not run("context_tokens", short.raw())["passed"]


def test_trial_metrics_summarise_the_session():
    b = Builder()
    for i in range(33):
        b.turn(f"t{i}", calls=[("browse_url", {"url": str(i)}, page(100), {"offloaded": {"path": f"p{i}"}} if i == 3 else None)],
               mark=None if i < 10 else "e010", cleared=0 if i < 10 else 400)
    b.add("compaction", {"summary": "s", "firstKeptEntryId": None})
    raw = b.raw(noisy=True)
    raw["submissions"] = [{"status": "unanswered", "unanswered": {"reason": "compaction_failed"}}]
    b.add("run_error", {"error": {"kind": "context_overflow"}})
    raw = {**raw, "entries": b.entries}
    m = trial_metrics(raw)
    assert m["noisy"] and m["turns"] == 33 and m["compactions"] == 1
    assert m["compaction_failed"] == 1 and m["overflow_failures"] == 1
    assert m["offloaded_results"] == 1 and m["watermark_advances"] == 1 and m["final_watermark"] == "e010"
    assert m["cleared_tokens"] == 400 and m["probes"] == {}
    assert trial_metrics({"entries": []}) is None
