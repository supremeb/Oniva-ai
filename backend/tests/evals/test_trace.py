import copy
import json
from pathlib import Path

import httpx
import pytest

from app.services.tool_policy import canonical_args_hash
from evals.engine_api import EngineApi, EngineApiError
from evals.schema import Usage
from evals.trace import (
    TraceTruncated,
    cache_read_share,
    fetch_trace,
    flatten,
    total_usage,
)

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def names(span, kind=None):
    return [(c.kind, c.name) for c in span.children if kind is None or c.kind == kind]


def test_deep_work_fixture_tree():
    trace = flatten(load("trace_deep_work.json"))
    root = trace.root
    assert root.kind == "subagent" and root.name == "root"
    assert names(root) == [
        ("model", "fake-1"),
        ("tool", "dw_set_plan"),
        ("subagent", "research_worker"),
        ("subagent", "research_worker"),
        ("model", "fake-1"),
        ("subagent", "reflector"),
        ("model", "fake-1"),
        ("tool", "dw_finalize"),
        ("model", "fake-1"),
    ]
    workers = [c for c in root.children if c.name == "research_worker"]
    assert len(workers) == 2
    for w in workers:
        assert names(w) == [
            ("model", "fake-1"),
            ("tool", "web_search"),
            ("model", "fake-1"),
            ("tool", "dw_save_learning"),
            ("model", "fake-1"),
        ]
    reflector = next(c for c in root.children if c.name == "reflector")
    assert names(reflector, "tool") == [("tool", "dw_record_reflection")]
    # 4 + 2*3 + 2 model spans... counted by kind across the whole tree
    spans = list(trace.walk())
    assert sum(s.kind == "subagent" for s in spans) == 1 + 3
    assert sum(s.kind == "tool" for s in spans) == 2 + 2 * 2 + 1
    assert sum(s.kind == "model" for s in spans) == 4 + 2 * 3 + 2
    assert all(s.status == "ok" for s in spans)
    assert trace.taint is True


def test_args_hash_matches_canonical_form_across_runs():
    trace = flatten(load("trace_deep_work.json"))
    plan = next(s for s in trace.walk() if s.name == "dw_set_plan")
    assert plan.args_hash == canonical_args_hash({"plan": ["a", "b"]})
    # same call, different key order in the recorded JSON: same hash
    data = load("trace_deep_work.json")
    call = data["entries"][2]["payload"]["message"]["blocks"][0]
    call["arguments"] = {"plan": call["arguments"]["plan"]}
    again = next(s for s in flatten(data).walk() if s.name == "dw_set_plan")
    assert again.args_hash == plan.args_hash


def test_chat_fixture_compaction_and_taint():
    trace = flatten(load("trace_chat.json"))
    assert names(trace.root) == [
        ("model", "fake-1"),
        ("tool", "browse_url"),
        ("compaction", "compaction"),
        ("model", "fake-1"),
    ]
    assert trace.compactions == (
        {"entryId": "e005", "conversationId": "conv-chat", "summaryChars": 43},
    )
    assert trace.taint is True
    assert set(trace.usage_by_role) == {"root"}


def test_untainted_when_taint_document_clean():
    data = load("trace_chat.json")
    data["taint"] = {"tainted": False, "total": 0, "sources": []}
    assert flatten(data).taint is False


def test_orphan_result_ignored_and_unpaired_call_interrupted():
    data = load("trace_chat.json")
    entries = data["entries"]
    # orphan: a result whose call was compacted away
    orphan = copy.deepcopy(entries[3])
    orphan["payload"]["toolCallId"] = "gone"
    orphan["id"] = "e900"
    entries.append(orphan)
    # unpaired: drop the real result of b1
    data["entries"] = [e for e in entries if not (e["kind"] == "tool_result" and e["payload"]["toolCallId"] == "b1")]
    trace = flatten(data)
    tools = [s for s in trace.walk() if s.kind == "tool"]
    assert [(s.name, s.status) for s in tools] == [("browse_url", "interrupted")]


def test_failed_tool_status_passes_through():
    data = load("trace_chat.json")
    for e in data["entries"]:
        if e["kind"] == "tool_result":
            e["payload"]["status"] = "error"
    tool = next(s for s in flatten(data).walk() if s.kind == "tool")
    assert tool.status == "error"


def test_subagent_result_without_child_is_a_plain_tool_span():
    data = load("trace_deep_work.json")
    del data["children"]["conv-w1"]
    root = flatten(data).root
    assert [c.kind for c in root.children if c.name == "research_worker"] == ["tool", "subagent"]


def test_usage_rollup_does_not_double_count():
    data = load("trace_deep_work.json")
    trace = flatten(data)
    by = trace.usage_by_role
    assert set(by) == {"root", "research_worker", "reflector"}
    assert by["root"] == Usage(6900, 650, 3800, 500, 0.048)
    # two workers, each 1500 / 100 / 800 / 200
    assert by["research_worker"] == Usage(3000, 200, 1600, 400, 0.018)
    assert by["reflector"] == Usage(1300, 90, 300, 300, 0.007)
    grand = total_usage(trace)
    assert grand.input_tokens == 6900 + 3000 + 1300
    assert grand.cost_usd == pytest.approx(0.048 + 0.018 + 0.007)


def test_cache_read_share():
    trace = flatten(load("trace_deep_work.json"))
    total = total_usage(trace)
    expect = total.cache_read_tokens / (
        total.input_tokens + total.cache_read_tokens + total.cache_write_tokens
    )
    assert cache_read_share(trace) == pytest.approx(expect)
    empty = load("trace_chat.json")
    empty["usage"] = {k: 0 for k in empty["usage"]} | {"costUsd": None}
    assert cache_read_share(flatten(empty)) == 0.0


def test_truncated_response_raises():
    data = load("trace_deep_work.json")
    data["truncated"] = True
    data["nextCursor"] = "abc"
    with pytest.raises(TraceTruncated):
        flatten(data)


def _api(handler) -> EngineApi:
    return EngineApi(
        "http://engine.test", "tok", tenant_id="ten", project_id="proj",
        transport=httpx.MockTransport(handler),
    )


async def test_fetch_trace_sends_binding_headers_and_flattens():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["h"] = dict(req.headers)
        return httpx.Response(200, json=load("trace_chat.json"))

    api = _api(handler)
    trace = await fetch_trace(api, "conv-chat")
    assert seen["path"] == "/v1/conversations/conv-chat/trace"
    assert seen["h"]["authorization"] == "Bearer tok"
    assert seen["h"]["x-oniva-tenant"] == "ten" and seen["h"]["x-oniva-project"] == "proj"
    assert len(list(trace.walk())) == 5
    await api.aclose()


async def test_fetch_trace_follows_cursor_and_merges_pages():
    full = load("trace_deep_work.json")
    root_entries = full["entries"]
    w1 = full["children"]["conv-w1"]
    page1 = copy.deepcopy(full)
    page1["entries"] = root_entries
    page1["children"] = {}
    page1["truncated"], page1["nextCursor"] = True, "cur1"
    page2 = copy.deepcopy(full)
    page2["entries"] = []
    page2["children"] = {k: v for k, v in full["children"].items()}
    page2["children"]["conv-w1"] = w1
    page2["truncated"], page2["nextCursor"] = False, None
    cursors = []

    def handler(req: httpx.Request) -> httpx.Response:
        c = req.url.params.get("cursor")
        cursors.append(c)
        return httpx.Response(200, json=page1 if c is None else page2)

    api = _api(handler)
    merged = flatten(await fetch_trace(api, "conv-root", raw=True))
    assert cursors == [None, "cur1"]
    assert len([s for s in merged.walk() if s.kind == "subagent"]) == 4
    await api.aclose()


async def test_fetch_trace_raises_when_page_budget_exhausted():
    data = load("trace_deep_work.json")
    data["truncated"], data["nextCursor"] = True, "again"
    api = _api(lambda req: httpx.Response(200, json=data))
    with pytest.raises(TraceTruncated):
        await fetch_trace(api, "conv-root", max_pages=3)
    await api.aclose()


async def test_engine_error_surfaces_status():
    api = _api(lambda req: httpx.Response(404, json={"error": "unknown_conversation"}))
    with pytest.raises(EngineApiError) as ei:
        await fetch_trace(api, "nope")
    assert ei.value.status == 404 and ei.value.error == "unknown_conversation"
    await api.aclose()
