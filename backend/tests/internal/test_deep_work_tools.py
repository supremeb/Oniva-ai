"""Deep Work U4 / R4-R7, R17, AE2, AE6: the dw_* gateway tools.

Calls go through the real invoke route (idempotency store, binding, outcome guard) on the gateway
fixture; run state is the SQLite-backed ``deep_work_runs`` of U3 and the workspace is a temp
``SessionStorage``.
"""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.internal_api.tool_specs import TOOL_SPECS
from app.internal_api.tools import build_manifest
from app.services import deep_work_state as dws
from app.services.deep_work import engine_tools
from app.services.deep_work.models import Learning
from app.services.deep_work.synthesis import build_sources_section
from app.services.session_storage import SessionStorage
from tests.internal.test_invoke import body, effects, factory, gw, headers, invoke, maker  # noqa: F401  (fixtures)

FIXTURE = Path(__file__).resolve().parents[1] / "contracts" / "fixtures" / "deep_work_success.json"
WRITES = ("dw_start_round", "dw_save_learnings", "dw_record_reflection", "dw_write_section", "dw_finalize")
READS = ("dw_get_learnings", "dw_get_report")
_calls = itertools.count(1)


def learning(n, url=None, title=None, confidence=0.8):
    return {
        "fact": f"Finding number {n}. More detail follows.", "source_url": url or f"https://example.com/{n}",
        "confidence": confidence, **({"source_title": title} if title else {}),
    }


def workers(*ids):
    return [{"worker_id": w, "sub_query": f"question for {w}"} for w in ids]


@pytest.fixture
async def dw(gw, tmp_path, monkeypatch):
    storage = SessionStorage(base_path=tmp_path / "sessions", checkpointing_enabled=False)
    monkeypatch.setattr(engine_tools, "state_factory", lambda tenant_id: gw.factory)
    monkeypatch.setattr(engine_tools, "_storage", lambda: storage)
    caps = SimpleNamespace(
        DEEP_WORK_MAX_WORKERS=10, DEEP_WORK_MAX_REFLECTION_ROUNDS=3,
        DEEP_WORK_MAX_DURATION_SEC=1800, DEEP_WORK_MAX_LEARNINGS_PER_WORKER=30,
    )
    monkeypatch.setattr(engine_tools, "get_settings", lambda: caps)
    run = await dws.create_run(
        gw.factory, tenant_id=gw.tenant_a, project_id=gw.project_a, user_id=gw.user_a,
        conversation_id=gw.conv_a, query="q",
    )
    await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, plan={"title": "Contract Report"})
    gw.storage, gw.caps, gw.run_id = storage, caps, run.id

    async def call(name, args=None, key=None, tenant="a"):
        r = await invoke(gw, name, call=key or f"k{next(_calls)}", args=args or {}, tenant=tenant)
        assert r.status_code == 200, r.text
        j = r.json()
        j["data"] = json.loads(j["content"]) if not j["isError"] else None
        return j

    async def read(path):
        return await storage.read(str(gw.tenant_a), str(run.id), f"workspace/{path}")

    async def state():
        return await dws.load_run(gw.factory, gw.tenant_a, gw.conv_a)

    async def research(*ids, round=1, save=True):
        assert not (await call("dw_start_round", {"round": round, "workers": workers(*ids)}))["isError"]
        if save:
            for n, w in enumerate(ids):
                assert not (await call("dw_save_learnings", {"worker_id": w, "learnings": [learning(f"{w}{n}")]}))["isError"]

    gw.call, gw.read, gw.state, gw.research = call, read, state, research
    return gw


# ---- dw_start_round -------------------------------------------------------------


async def test_start_round_registers_workers_and_writes_plan_and_todo(dw):
    r = await dw.call("dw_start_round", {"round": 1, "workers": workers("w1", "w2")})
    assert not r["isError"] and r["details"]["workspace"] == ["plan.md", "todo.md"]
    s = await dw.state()
    assert (s.round, s.phase) == (1, "research")
    assert [w["worker_id"] for w in s.rounds[0]["registered"]] == ["w1", "w2"]
    plan, todo = await dw.read("plan.md"), await dw.read("todo.md")
    assert "# Contract Report" in plan and "`w1`: question for w1" in plan
    assert "- [ ] (round 1) w2: question for w2" in todo


async def test_eleventh_and_twelfth_workers_are_rejected_AE2(dw):
    ids = [f"w{i}" for i in range(12)]
    r = await dw.call("dw_start_round", {"round": 1, "workers": workers(*ids)})
    assert r["isError"] and r["details"]["error"] == "worker_cap"
    assert "at most 10" in r["content"] and "drop or merge 2" in r["content"]
    assert (await dw.state()).rounds == []
    ok = await dw.call("dw_start_round", {"round": 1, "workers": workers(*ids[:10])})
    assert not ok["isError"]


async def test_round_past_the_reflection_cap_is_rejected_AE2(dw):
    dw.caps.DEEP_WORK_MAX_REFLECTION_ROUNDS = 1
    await dw.research("a", round=1)
    await dw.research("b", round=2)
    r = await dw.call("dw_start_round", {"round": 3, "workers": workers("c")})
    assert r["isError"] and r["details"]["error"] == "round_cap" and "proceed to synthesis" in r["content"]
    assert (await dw.state()).round == 2


async def test_non_consecutive_round_is_rejected(dw):
    r = await dw.call("dw_start_round", {"round": 2, "workers": workers("a")})
    assert r["isError"] and r["details"] == {"error": "round_order", "next_round": 1}
    await dw.research("a", round=1, save=False)
    r = await dw.call("dw_start_round", {"round": 3, "workers": workers("b")})
    assert r["isError"] and r["details"]["next_round"] == 2


async def test_replay_of_the_same_round_is_accepted_and_unchanged(dw):
    first = await dw.call("dw_start_round", {"round": 1, "workers": workers("w1", "w2")})
    before = await dw.state()
    plan = await dw.read("plan.md")
    again = await dw.call("dw_start_round", {"round": 1, "workers": workers("w1", "w2")})
    other = await dw.call("dw_start_round", {"round": 1, "workers": workers("zz")})  # a different set does not replace it
    after = await dw.state()
    assert not again["isError"] and again["details"]["replay"] and not other["isError"]
    assert other["data"]["workers"] == first["data"]["workers"]
    assert after.rounds == before.rounds and (after.round, after.phase) == (before.round, before.phase)
    assert await dw.read("plan.md") == plan


async def test_worker_ids_are_unique_and_safe(dw):
    assert (await dw.call("dw_start_round", {"round": 1, "workers": workers("a", "a")}))["details"]["error"] == "duplicate_worker"
    assert (await dw.call("dw_start_round", {"round": 1, "workers": workers("../x")}))["details"]["error"] == "invalid_worker"
    assert (await dw.call("dw_start_round", {"round": 1, "workers": []}))["details"]["error"] == "invalid_worker"
    await dw.research("a", round=1, save=False)
    assert (await dw.call("dw_start_round", {"round": 2, "workers": workers("a")}))["details"]["error"] == "duplicate_worker"


async def test_a_spent_time_budget_rejects_a_new_round(dw):
    dw.caps.DEEP_WORK_MAX_DURATION_SEC = -1
    r = await dw.call("dw_start_round", {"round": 1, "workers": workers("a")})
    assert r["isError"] and r["details"]["error"] == "deadline" and "proceed to synthesis" in r["content"]


# ---- dw_save_learnings ----------------------------------------------------------


async def test_save_learnings_writes_file_marks_worker_saved_and_reports_open_workers(dw):
    await dw.research("w1", "w2", save=False)
    r = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1), learning(2, url="https://example.com/1")]})
    assert not r["isError"] and r["details"]["workspace"] == ["learnings/w1.json", "todo.md"]
    assert r["data"]["learning_count"] == 2 and r["data"]["source_count"] == 1 and r["data"]["workers_still_open"] == ["w2"]
    saved = json.loads(await dw.read("learnings/w1.json"))
    assert saved["worker_id"] == "w1" and saved["learnings"][0]["sub_query"] == "question for w1"
    assert "- [x] (round 1) w1" in await dw.read("todo.md")
    assert list((await dw.state()).rounds[0]["workers"]) == ["w1"]


async def test_save_learnings_rejects_unknown_workers_and_invalid_learnings(dw):
    await dw.research("w1", save=False)
    unknown = await dw.call("dw_save_learnings", {"worker_id": "nope", "learnings": []})
    assert unknown["isError"] and unknown["details"]["error"] == "unknown_worker"
    bad = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1), {"fact": "x", "source_url": "u", "confidence": 3}]})
    assert bad["isError"] and bad["details"] == {"error": "invalid_learning", "index": 1}
    assert (await dw.state()).rounds[0]["workers"] == {}  # nothing partially saved


async def test_learnings_over_the_cap_are_truncated_visibly(dw):
    dw.caps.DEEP_WORK_MAX_LEARNINGS_PER_WORKER = 3
    await dw.research("w1", save=False)
    r = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(n) for n in range(5)]})
    assert not r["isError"] and r["details"]["truncated"] is True and r["details"]["original"] == 5 and r["details"]["kept"] == 3
    assert r["data"]["learning_count"] == 3
    assert len(json.loads(await dw.read("learnings/w1.json"))["learnings"]) == 3


async def test_same_idempotency_key_replays_the_stored_result_and_changes_nothing(dw):
    await dw.research("w1", save=False)
    args = {"worker_id": "w1", "learnings": [learning(1)]}
    first = await dw.call("dw_save_learnings", args, key="same")
    state, file = await dw.state(), await dw.read("learnings/w1.json")
    second = await dw.call("dw_save_learnings", args, key="same")
    assert second == first
    assert (await dw.state()).rounds == state.rounds and await dw.read("learnings/w1.json") == file


async def test_every_write_tool_is_replay_safe_across_different_keys(dw):
    steps = [
        ("dw_start_round", {"round": 1, "workers": workers("w1")}),
        ("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1, title="One")]}),
        ("dw_record_reflection", {"round": 1, "score": 80, "gaps": ["g"], "should_continue": False}),
        ("dw_write_section", {"heading": "Market", "markdown": "Body [1]."}),
        ("dw_finalize", {}),
    ]
    for name, args in steps:
        first = await dw.call(name, args)
        assert not first["isError"], (name, first)
        snapshot = (await dw.state(), {p: await dw.read(p) for p in ("plan.md", "todo.md", "report.md", "learnings/w1.json")})
        again = await dw.call(name, args)  # a re-driven call under a fresh key
        assert not again["isError"], (name, again)
        after = (await dw.state(), {p: await dw.read(p) for p in snapshot[1]})
        assert after[1] == snapshot[1], name
        for field in ("rounds", "reflections", "artifacts", "round", "phase", "status"):
            assert getattr(after[0], field) == getattr(snapshot[0], field), (name, field)


# ---- reflection ------------------------------------------------------------------


async def test_reflection_is_stored_per_round_and_replaced_on_repeat(dw):
    await dw.research("w1")
    r = await dw.call("dw_record_reflection", {
        "round": 1, "score": 60, "gaps": ["pricing"], "should_continue": True,
        "gap_tasks": [{"worker_id": "gap-1", "sub_query": "pricing"}],
    })
    assert not r["isError"] and r["data"]["next"] == "dw_start_round"
    await dw.call("dw_record_reflection", {"round": 1, "score": 90, "gaps": [], "should_continue": False})
    refl = (await dw.state()).reflections
    assert [(x["round"], x["score"]) for x in refl] == [(1, 90)]


async def test_reflection_cap_and_unknown_round(dw):
    dw.caps.DEEP_WORK_MAX_REFLECTION_ROUNDS = 1
    await dw.research("a", round=1)
    await dw.research("b", round=2)
    assert not (await dw.call("dw_record_reflection", {"round": 1, "score": 50, "gaps": [], "should_continue": True}))["isError"]
    over = await dw.call("dw_record_reflection", {"round": 2, "score": 50, "gaps": [], "should_continue": True})
    assert over["isError"] and over["details"]["error"] == "reflection_cap" and "synthesis" in over["content"]
    unknown = await dw.call("dw_record_reflection", {"round": 9, "score": 50, "gaps": [], "should_continue": False})
    assert unknown["details"]["error"] == "unknown_round"
    assert [x["round"] for x in (await dw.state()).reflections] == [1]


# ---- dw_write_section ------------------------------------------------------------


async def test_write_section_before_workers_are_saved_returns_the_instructive_error(dw):
    await dw.research("w1", "w2", save=False)
    await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1)]})
    r = await dw.call("dw_write_section", {"heading": "Market", "markdown": "Body."})
    assert r["isError"] and r["details"]["error"] == "round_open" and r["details"]["open_workers"] == ["w2"]
    assert "w2" in r["content"] and "failed_workers" in r["content"]
    assert await dw.read("report.md") == ""
    none = await dw.call("dw_get_report")
    assert none["details"]["error"] == "no_report"


async def test_a_failed_worker_reported_by_the_reflector_unblocks_sections(dw):
    await dw.research("w1", "w2", save=False)
    await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1)]})
    r = await dw.call("dw_record_reflection", {"round": 1, "score": 40, "gaps": [], "should_continue": False, "failed_workers": ["w2"]})
    assert not r["isError"] and r["data"]["workers_still_open"] == []
    assert not (await dw.call("dw_write_section", {"heading": "Market", "markdown": "Body."}))["isError"]
    unknown = await dw.call("dw_record_reflection", {"round": 1, "score": 40, "gaps": [], "should_continue": False, "failed_workers": ["ghost"]})
    assert unknown["details"]["error"] == "unknown_worker"


async def test_write_section_replaces_by_heading_and_keeps_order(dw):
    await dw.research("w1")
    await dw.call("dw_write_section", {"heading": "Market", "markdown": "first"})
    await dw.call("dw_write_section", {"heading": "## Players", "markdown": "second"})
    r = await dw.call("dw_write_section", {"heading": "Market", "markdown": "first, revised"})
    assert r["data"] == {"heading": "Market", "replaced": True, "sections": ["Market", "Players"]}
    assert r["details"]["workspace"] == ["report.md"]
    assert await dw.read("report.md") == "# Contract Report\n\n## Market\n\nfirst, revised\n\n## Players\n\nsecond\n"
    assert (await dw.state()).phase == "synthesize"


async def test_write_section_rejects_sources_and_nested_level_two_headings(dw):
    await dw.research("w1")
    assert (await dw.call("dw_write_section", {"heading": "Sources", "markdown": "x"}))["details"]["error"] == "reserved_heading"
    nested = await dw.call("dw_write_section", {"heading": "A", "markdown": "intro\n## B\nmore"})
    assert nested["details"]["error"] == "invalid_section"
    fenced = await dw.call("dw_write_section", {"heading": "A", "markdown": "```\n## comment\n```"})
    assert not fenced["isError"]
    assert (await dw.call("dw_write_section", {"heading": "Z", "markdown": "  "}))["details"]["error"] == "invalid_section"


# ---- dw_finalize / AE6 -----------------------------------------------------------


async def test_finalize_builds_sources_from_saved_learnings_regardless_of_sections_AE6(dw):
    await dw.research("w1", "w2", save=False)
    await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [
        learning(1, url="https://example.com/market-size", title="Source for market size"),
        learning(2, url="https://example.com/market-size", title="Duplicate"),
    ]})
    await dw.call("dw_save_learnings", {"worker_id": "w2", "learnings": [
        learning(3, url="https://example.com/pricing-details", title="Source for pricing details"),
    ]})
    await dw.call("dw_write_section", {"heading": "Market", "markdown": "Section body text [1].\n\n[Fake](https://evil.example/x)"})
    r = await dw.call("dw_finalize")
    assert not r["isError"] and r["details"]["workspace"] == ["report.md"]
    report = await dw.read("report.md")
    fixture = json.loads(FIXTURE.read_text())["report"]
    legacy_sources = fixture[fixture.index("## Sources"):]
    assert report.endswith("\n\n" + legacy_sources + "\n")  # the legacy golden Sources block
    assert report.count("## Sources") == 1 and report.count("](https://example.com/market-size)") == 1
    assert r["data"]["report_length"] == len(report) and r["data"]["artifacts"] == []
    s = await dw.state()
    assert (s.status, s.phase) == ("completed", "done")


async def test_sources_block_matches_the_pure_builder(dw):
    items = [Learning(**learning(n, title=f"T{n}")) for n in (1, 2)] + [Learning(**learning(3, url="https://example.com/1"))]
    assert build_sources_section(items) == "\n## Sources\n1. [T1](https://example.com/1)\n2. [T2](https://example.com/2)"


async def test_finalize_needs_sections_and_closed_rounds_then_is_stable(dw):
    assert (await dw.call("dw_finalize"))["details"]["error"] == "no_research"
    await dw.research("w1", "w2", save=False)
    await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1)]})
    assert (await dw.call("dw_finalize"))["details"]["error"] == "round_open"
    assert (await dw.call("dw_finalize", {"failed_workers": ["w2"]}))["details"]["error"] == "no_sections"
    await dw.call("dw_write_section", {"heading": "A", "markdown": "text"})
    first = await dw.call("dw_finalize")
    assert not first["isError"]
    report = await dw.read("report.md")
    second = await dw.call("dw_finalize")  # stored result, not a rebuild
    assert not second["isError"] and second["data"]["report_length"] == first["data"]["report_length"]
    assert second["details"]["replay"] is True and await dw.read("report.md") == report


# ---- terminal runs ---------------------------------------------------------------


@pytest.mark.parametrize("status", ["cancelled", "failed"])
async def test_a_terminal_run_refuses_every_write(dw, status):
    await dw.research("w1")
    await dw.call("dw_write_section", {"heading": "A", "markdown": "text"})
    await dws.update_run(dw.factory, dw.tenant_a, dw.conv_a, status=status)
    before = (await dw.state(), await dw.read("report.md"), await dw.read("learnings/w1.json"))
    attempts = {
        "dw_start_round": {"round": 2, "workers": workers("late")},
        "dw_save_learnings": {"worker_id": "w1", "learnings": [learning(99)]},
        "dw_record_reflection": {"round": 1, "score": 10, "gaps": [], "should_continue": False},
        "dw_write_section": {"heading": "B", "markdown": "late"},
        "dw_finalize": {},
    }
    for name, args in attempts.items():
        r = await dw.call(name, args)
        assert r["isError"] and r["details"] == {"error": "run_terminal", "status": status}, name
    after = (await dw.state(), await dw.read("report.md"), await dw.read("learnings/w1.json"))
    assert after == before
    assert not (await dw.call("dw_get_report"))["isError"]  # reads still work
    assert not (await dw.call("dw_get_learnings"))["isError"]


async def test_a_completed_run_refuses_further_writes_but_finalize_returns_the_stored_result(dw):
    await dw.research("w1")
    await dw.call("dw_write_section", {"heading": "A", "markdown": "text"})
    await dw.call("dw_finalize")
    late = await dw.call("dw_write_section", {"heading": "B", "markdown": "late"})
    assert late["isError"] and late["details"]["status"] == "completed"
    assert (await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": []}))["isError"]
    assert not (await dw.call("dw_finalize"))["isError"]


async def test_tools_error_cleanly_when_the_conversation_has_no_run(dw):
    # Tenant B has a valid conversation but no Deep Work run.
    for name, args in (("dw_start_round", {"round": 1, "workers": workers("a")}), ("dw_finalize", {}), ("dw_get_report", {})):
        r = await dw.call(name, args, tenant="b")
        assert r["isError"] and r["details"]["error"] == "no_run"


# ---- reads -----------------------------------------------------------------------


async def test_get_learnings_filters_by_worker_and_respects_the_budget(dw):
    await dw.research("w1", "w2")
    everything = await dw.call("dw_get_learnings")
    assert everything["data"]["total"] == 2 and everything["data"]["returned"] == 2
    only = await dw.call("dw_get_learnings", {"worker_ids": ["w2"]})
    assert only["data"]["total"] == 1 and only["data"]["learnings"][0]["fact"].startswith("Finding number w21")


# ---- tenant isolation (R17) -------------------------------------------------------


async def test_tenant_b_cannot_read_or_write_tenant_a_run(dw):
    await dw.research("w1")
    await dw.call("dw_write_section", {"heading": "A", "markdown": "secret finding"})
    # B naming A's conversation is refused at the binding, before any tool runs.
    b = body(dw, tenant="b")
    b["conversationId"] = str(dw.conv_a)
    for name in ("dw_get_report", "dw_get_learnings"):
        b["toolCallId"] = f"x-{name}"
        r = await dw.client.post(f"/internal/tools/{name}/invoke", json={**b, "args": {}}, headers=headers(call=f"x-{name}"))
        assert r.status_code == 404 and r.json() == {"error": "conversation_not_found"}
    # B's own conversation reaches only B's (absent) run.
    mine = await dw.call("dw_get_report", tenant="b")
    assert mine["details"]["error"] == "no_run" and "secret" not in mine["content"]
    assert (await dws.load_run(dw.factory, dw.tenant_b, dw.conv_a)) is None
    state = await dw.state()
    assert state.status == "running" and len(state.rounds) == 1


# ---- specs -----------------------------------------------------------------------


def test_specs_classify_writes_as_idempotent_replace_by_key_and_reads_as_none():
    for name in WRITES:
        spec = TOOL_SPECS[name]
        assert (spec.side_effect, spec.idempotent, spec.requires_approval, spec.needs_db, spec.needs_keys) == ("write", True, False, False, ())
    for name in READS:
        assert (TOOL_SPECS[name].side_effect, TOOL_SPECS[name].idempotent) == ("none", True)


def test_tools_are_in_the_manifest_with_their_specs(gw):
    manifest = {t.name: t for t in build_manifest()}
    for name in WRITES + READS:
        tool = manifest[name]
        assert tool.parameters["additionalProperties"] is False
        assert (tool.sideEffect, tool.idempotent) == (TOOL_SPECS[name].side_effect, TOOL_SPECS[name].idempotent)
    assert set(manifest["dw_start_round"].parameters["required"]) == {"round", "workers"}


async def test_model_knowledge_learning_needs_no_url_and_is_labelled(dw):
    mk = {"fact": "From memory.", "source_type": "model_knowledge", "confidence": 0.4}
    assert Learning(**mk).source_url is None
    out = build_sources_section([Learning(**mk), Learning(**learning(1, title="T1"))])
    assert "1. [T1](https://example.com/1)" in out and "model's own knowledge" in out and "None" not in out
    assert "model's own knowledge" not in build_sources_section([Learning(**learning(1))])


def test_non_model_knowledge_without_url_is_rejected():
    import pytest as _pt
    from pydantic import ValidationError as _VE
    for t in ("web", "knowledge_base", "connector"):
        with _pt.raises(_VE):
            Learning(fact="x", source_type=t, confidence=0.5)


async def test_save_learnings_tool_accepts_model_knowledge_without_url_only(dw):
    await dw.research("w1", save=False)
    nourl = {"fact": "x", "confidence": 0.5}
    bad = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [nourl]})
    assert bad["isError"] and bad["details"]["error"] == "invalid_learning"
    ok = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [{**nourl, "source_type": "model_knowledge"}]})
    assert not ok["isError"] and ok["data"]["learning_count"] == 1 and ok["data"]["source_count"] == 0
