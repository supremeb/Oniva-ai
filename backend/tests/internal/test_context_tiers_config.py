"""U16 / R14, R27: tier defaults, per-model windows, grants, Deep Work config, recitation.

The ``off`` goldens in ``tests/internal/golden/context_tiers_off.json`` were captured from the code as it
was before U16 (regenerate only deliberately: ``UPDATE_GOLDEN=1``). With ``ENGINE_CONTEXT_TIERS=off`` and
no tenant flag, chat and Deep Work configs and the dw_* tool results must equal them byte for byte.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.tenant import Tenant
from app.services import engine_deep_work_agent as edw
from app.services import feature_flags as ff
from app.services.deep_work import engine_tools
from app.services.deep_work.models import DeepWorkPlan, WorkerTask
from app.services.engine_chat_agent import (
    ChatInputs,
    Selections,
    build_engine_chat_agent,
    compaction_settings,
    compose_agent_config,
)
from tests.internal.test_deep_work_tools import dw, learning  # noqa: F401  (fixture)
from tests.internal.test_invoke import effects, factory, gw, headers, invoke, maker  # noqa: F401  (fixtures)

GOLDEN = Path(__file__).parent / "golden" / "context_tiers_off.json"
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
MANIFEST = {
    "web_search", "browse_url", "read_file", "write_file", "execute_python", "search_knowledge",
    "list_available_connectors", "get_connector_schema", "invoke_connector_tool", "summarize_document",
    "update_todo",
}
MODELS = edw.DeepWorkModels(high="m/high", mid="m/mid", low="m/low", worker="m/worker")
EXCLUDE = ["dw_get_learnings", "dw_get_report", "ask_user", "update_todo", "present_plan", "complete_task"]


def _inputs(**kw) -> ChatInputs:
    base = dict(base_prompt="Custom prompt.", agent_skills=["summarize_document"],
                accessible_knowledge_base_ids={"kb1"}, valid_connector_ids={"c1"})
    base.update(kw)
    return ChatInputs(**base)


def _plan(deliverable=None) -> DeepWorkPlan:
    return DeepWorkPlan(title="T", deliverable=deliverable, sub_queries=[
        WorkerTask(worker_id=f"w{i}", sub_query=f"q{i}", tools=["web_search"]) for i in (1, 2)])


def _dw_inputs(**kw) -> edw.ExecuteInputs:
    return edw.ExecuteInputs(plan=kw.pop("plan", _plan()), models=MODELS, **kw)


def chat_cases(**extra):
    out = {}
    for name, sel, kw in (
        ("plain", Selections(), {}),
        ("web_kb_connector", Selections(web_search=True, knowledge_base_ids=["kb1"], connector_ids=["c1"]), {}),
        ("with_files", Selections(skill_ids=["read_file", "write_file"]), {}),
        ("worker_role", Selections(), {"agent_role": "worker"}),
    ):
        out[name] = compose_agent_config(_inputs(), sel, MANIFEST, NOW, **kw, **extra).agent
    return out


def dw_cases(**extra):
    return {
        "minimal": edw.compose_execute_config(_dw_inputs(**extra), NOW),
        "full": edw.compose_execute_config(_dw_inputs(
            plan=_plan("pdf_report"), has_search=True, knowledge_base_ids=["kb1"], connector_ids=["c1"],
            sandbox_enabled=True, has_files=True, run_id="r1", max_rounds=60, deadline_ms=123, **extra), NOW),
        "no_files_no_search": edw.compose_execute_config(_dw_inputs(has_files=False, has_search=False, **extra), NOW),
    }


def _norm(obj):
    return json.loads(json.dumps(obj, sort_keys=True))


def _update_golden(key, value):
    golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    golden[key] = value
    GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")


# --- goldens (tiers off) --------------------------------------------------------------------


def test_chat_and_deep_work_configs_equal_the_pre_u16_goldens_when_tiers_are_off():
    now = {"chat": _norm(chat_cases()), "deep_work": _norm(dw_cases())}
    if os.environ.get("UPDATE_GOLDEN"):
        _update_golden("chat", now["chat"])
        _update_golden("deep_work", now["deep_work"])
    golden = json.loads(GOLDEN.read_text())
    assert now["chat"] == golden["chat"]
    assert now["deep_work"] == golden["deep_work"]
    # byte-identical serialisation, not just equal values
    assert json.dumps(now["chat"], sort_keys=True) == json.dumps(golden["chat"], sort_keys=True)
    assert json.dumps(now["deep_work"], sort_keys=True) == json.dumps(golden["deep_work"], sort_keys=True)


def test_explicit_off_equals_the_default():
    assert chat_cases(tiers="off") == chat_cases()
    assert dw_cases(tiers="off") == dw_cases()
    assert compaction_settings(tiers="off") == compaction_settings()


async def test_dw_tool_results_equal_the_pre_u16_goldens_when_tiers_are_off(dw):
    await dw.research("w1", "w2", save=False)
    save = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1), learning(2)]})
    await dw.call("dw_save_learnings", {"worker_id": "w2", "learnings": [learning(3)]})
    refl = await dw.call("dw_record_reflection", {"round": 1, "score": 80, "gaps": ["g"], "should_continue": False})
    now = {"dw_save_learnings": save["content"], "dw_record_reflection": refl["content"]}
    if os.environ.get("UPDATE_GOLDEN"):
        _update_golden("tools", now)
    assert now == json.loads(GOLDEN.read_text())["tools"]
    assert "todo" not in json.loads(save["content"]) and "todo" not in json.loads(refl["content"])


# --- flag resolution -----------------------------------------------------------------------


def _tenant(features=None):
    return Tenant(name="t", slug="t", settings={"features": features} if features is not None else {})


def _s(value="off"):
    return SimpleNamespace(ENGINE_CONTEXT_TIERS=value)


def test_flag_is_registered_and_defaults_off():
    assert ff.ENGINE_CONTEXT_TIERS_FLAG == "ENABLE_ENGINE_CONTEXT_TIERS"
    assert ff.ENGINE_CONTEXT_TIERS_FLAG in ff.FLAG_METADATA
    assert ff.get_feature(_tenant(), "ENABLE_ENGINE_CONTEXT_TIERS") is False
    assert ff.context_tiers_for(None, _s()) == "off" and ff.context_tiers_for(_tenant(), _s()) == "off"


@pytest.mark.parametrize("value", ["offload", "offload+clear", "full"])
def test_global_value_applies_without_a_tenant_flag(value):
    assert ff.context_tiers_for(_tenant(), _s(value)) == value
    assert ff.context_tiers_for(None, _s(value)) == value


def test_tenant_flag_overrides_global_off_and_global_on():
    on = _tenant({"ENABLE_ENGINE_CONTEXT_TIERS": True})
    assert ff.context_tiers_for(on, _s("off")) == "full"
    assert ff.context_tiers_for(_tenant({"ENABLE_ENGINE_CONTEXT_TIERS": False}), _s("full")) == "off"


@pytest.mark.parametrize("bad", ["", "ON", "tier1", "offload+clear+full"])
def test_unknown_global_value_means_off(bad):
    assert ff.context_tiers_for(_tenant(), _s(bad)) == "off"


def test_settings_default_is_off():
    from app.config import Settings

    assert Settings(_env_file=None, SECRET_KEY="x" * 40).ENGINE_CONTEXT_TIERS == "off"


# --- compaction block per tier ---------------------------------------------------------------

BASE = compaction_settings()


def test_offload_tier_adds_only_offload_tokens():
    assert compaction_settings(tiers="offload") == {**BASE, "offloadTokens": 8000}


def test_offload_clear_tier_adds_clearing_knobs_at_half_the_window():
    c = compaction_settings(tiers="offload+clear")
    assert c == {**BASE, "offloadTokens": 8000, "clearTriggerTokens": 100_000, "keepToolResults": 5,
                 "clearAtLeastTokens": 8000, "excludeTools": EXCLUDE}
    assert c["excludeTools"] is not compaction_settings(tiers="offload+clear")["excludeTools"]  # no shared list


def test_full_tier_adds_structured_summary_and_archive():
    c = compaction_settings(tiers="full")
    assert c == {**compaction_settings(tiers="offload+clear"), "structuredSummary": True, "archiveSummarized": True}


def test_clear_trigger_uses_the_smallest_known_window_and_windows_are_emitted():
    c = compaction_settings(tiers="offload+clear", context_windows={"a": 128_000, "b": 1_000_000})
    assert c["contextWindows"] == {"a": 128_000, "b": 1_000_000} and c["clearTriggerTokens"] == 64_000
    assert "contextWindow" not in c


def test_pinned_window_sets_the_clear_trigger():
    assert compaction_settings(100_000, tiers="offload+clear")["clearTriggerTokens"] == 50_000


def test_unknown_tier_is_treated_as_off():
    assert compaction_settings(tiers="bogus") == BASE


def test_emitted_keys_are_all_engine_schema_fields():
    allowed = {"enabled", "contextWindow", "contextWindows", "reserveTokens", "backgroundMargin", "keepRecentTokens",
               "offloadTokens", "clearTriggerTokens", "keepToolResults", "clearAtLeastTokens", "excludeTools",
               "structuredSummary", "archiveSummarized"}
    schema = (Path(__file__).resolve().parents[3] / "engine" / "src" / "agent" / "prompt.ts").read_text()
    for key in allowed:
        assert f"{key}:" in schema, key
    assert set(compaction_settings(tiers="full", context_windows={"a": 1})) <= allowed


# --- chat config ---------------------------------------------------------------------------


def test_chat_tiers_on_grants_read_file_and_update_todo_and_lists_them():
    off = chat_cases()["plain"]
    on = chat_cases(tiers="offload")["plain"]
    assert "read_file" not in off["tools"] and "update_todo" not in off["tools"]
    assert {"read_file", "update_todo", "ask_user"} <= set(on["tools"])
    assert on["tools"] == sorted(on["tools"])
    block = on["systemPrompt"].split("Your capabilities")[1]
    assert "read_file" in block and "update_todo" in block and "tool_results/" in block
    assert "tool_results/" not in off["systemPrompt"]
    assert on["compaction"]["offloadTokens"] == 8000 and "clearTriggerTokens" not in on["compaction"]


def test_chat_worker_role_gets_read_file_but_not_update_todo():
    on = chat_cases(tiers="full")["worker_role"]
    assert "read_file" in on["tools"] and "update_todo" not in on["tools"] and "ask_user" not in on["tools"]


def test_chat_grants_only_what_the_manifest_serves():
    built = compose_agent_config(_inputs(), Selections(), {"summarize_document"}, NOW, tiers="full")
    assert "read_file" not in built.agent["tools"] and "update_todo" not in built.agent["tools"]


def test_chat_context_windows_reach_the_block():
    built = compose_agent_config(_inputs(), Selections(), MANIFEST, NOW, tiers="offload+clear",
                                 context_windows={"m/x": 64_000})
    assert built.agent["compaction"]["contextWindows"] == {"m/x": 64_000}
    assert built.agent["compaction"]["clearTriggerTokens"] == 32_000


async def test_build_engine_chat_agent_resolves_tiers_from_the_tenant_flag(monkeypatch):
    from app.services import engine_chat_agent as eca
    from tests.internal.test_engine_conversations import TENANT_A, FakeDB, user

    async def loader(db, **kw):
        return _inputs()

    class TierDB(FakeDB):
        async def get(self, model, key):
            if model is Tenant:
                return Tenant(id=key, name="t", slug="t", settings={"features": {"ENABLE_ENGINE_CONTEXT_TIERS": True}})
            return None

    async def windows(db, tenant_id):
        return {"m/x": 128_000}

    monkeypatch.setattr(eca, "resolve_context_windows", windows)
    pid = "00000000-0000-0000-0000-000000000001"
    built = await build_engine_chat_agent(
        TierDB(), tenant_id=TENANT_A, user=user(), project_id=pid,
        selections=Selections(), loader=loader, manifest=MANIFEST, now=NOW)
    assert built.agent["compaction"]["structuredSummary"] is True
    assert built.agent["compaction"]["contextWindows"] == {"m/x": 128_000}
    assert "read_file" in built.agent["tools"]
    off = await build_engine_chat_agent(
        None, tenant_id=TENANT_A, user=user(), project_id=pid,
        selections=Selections(), loader=loader, manifest=MANIFEST, now=NOW)
    assert "offloadTokens" not in off.agent["compaction"] and "read_file" not in off.agent["tools"]


async def test_resolve_context_windows_maps_tier_models_to_windows(monkeypatch):
    from app.config.llm_pricing import context_window_for
    from app.services import engine_chat_agent as eca
    from app.services import llm_gateway as gw_

    async def tier(db, tenant_id, t, agent_config=None):
        return {"high": "anthropic/claude-opus-5.5", "mid": "openai/gpt-4o", "low": "openai/gpt-4o"}[t]

    monkeypatch.setattr(gw_, "resolve_model_for_tier", tier)
    out = await eca.resolve_context_windows(object(), "t")
    assert out["openai/gpt-4o"] == context_window_for("openai/gpt-4o")
    assert out["anthropic/claude-opus-5.5"] == context_window_for("anthropic/claude-opus-5.5")


async def test_resolve_context_windows_failure_yields_no_windows(monkeypatch):
    from app.services import engine_chat_agent as eca
    from app.services import llm_gateway as gw_

    async def boom(*a, **k):
        raise RuntimeError("no provider")

    monkeypatch.setattr(gw_, "resolve_model_for_tier", boom)
    assert await eca.resolve_context_windows(object(), "t") == {}


# --- Deep Work config ----------------------------------------------------------------------


def test_deep_work_orchestrator_and_workers_get_the_tier_block():
    cfg = edw.compose_execute_config(_dw_inputs(plan=_plan("pdf_report"), sandbox_enabled=True, tiers="full",
                                                context_windows={"m/high": 200_000}), NOW)
    block = cfg["compaction"]
    assert block["structuredSummary"] and block["archiveSummarized"] and block["contextWindows"] == {"m/high": 200_000}
    assert block["clearTriggerTokens"] == 100_000 and block["excludeTools"][: len(EXCLUDE)] == EXCLUDE
    child = {k: v for k, v in block.items() if k != "excludeTools"}
    for name in ("research_worker", "reflector", "output_agent"):
        spec = cfg["metadata"]["subagents"][name]
        sub = spec["compaction"]
        # A child without read_file cannot follow an offload pointer: its own tools are excluded as well.
        extra = [] if "read_file" in spec["tools"] else [t for t in spec["tools"] if t not in EXCLUDE]
        assert sub["excludeTools"] == EXCLUDE + extra
        assert {k: v for k, v in sub.items() if k != "excludeTools"} == child


def test_deep_work_off_has_no_subagent_compaction():
    cfg = edw.compose_execute_config(_dw_inputs(plan=_plan("pdf_report"), sandbox_enabled=True), NOW)
    assert all("compaction" not in s for s in cfg["metadata"]["subagents"].values())


def test_read_file_in_worker_tools_with_tiers_on_even_without_files():
    assert "read_file" not in edw.worker_tools(_dw_inputs())
    assert "read_file" in edw.worker_tools(_dw_inputs(tiers="offload"))
    assert "read_file" in edw.worker_tools(_dw_inputs(has_files=True))
    assert "read_file" not in edw.worker_tools(_dw_inputs(tiers="off"))


def test_worker_prompt_mentions_saved_outputs_only_with_tiers_on():
    off = edw.worker_prompt(_dw_inputs(has_files=True), NOW)
    on = edw.worker_prompt(_dw_inputs(has_files=True, tiers="offload"), NOW)
    assert "tool_results/" not in off and "tool_results/" in on
    no_files = edw.worker_prompt(_dw_inputs(tiers="offload", has_search=False), NOW)
    assert "NO web search, knowledge base or file access" in no_files  # read_file alone is not a gathering tool


def test_orchestrator_prompt_has_the_after_summary_note_only_with_tiers_on():
    off = edw.compose_execute_config(_dw_inputs(), NOW)["systemPrompt"]
    on = edw.compose_execute_config(_dw_inputs(tiers="full"), NOW)["systemPrompt"]
    note = "summary of the earlier conversation"
    assert note not in off and note in on and "dw_get_learnings" in on.split(note)[1][:200]


# --- recitation ----------------------------------------------------------------------------


async def _tier_tenant(dw, features):
    async with dw.factory() as db:
        t = await db.get(Tenant, dw.tenant_a)
        t.settings = {"features": features}
        await db.commit()


async def test_recitation_todo_is_returned_with_the_flag_on(dw):
    await _tier_tenant(dw, {"ENABLE_ENGINE_CONTEXT_TIERS": True})
    await dw.research("w1", "w2", save=False)
    r = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1)]})
    base_keys = {"worker_id", "round", "learning_count", "source_count", "workers_still_open", "status"}
    assert base_keys <= set(r["data"])  # existing keys unchanged
    assert r["data"]["todo"] == await dw.read("todo.md")
    assert "- [x] (round 1) w1" in r["data"]["todo"] and "- [ ] (round 1) w2" in r["data"]["todo"]
    await dw.call("dw_save_learnings", {"worker_id": "w2", "learnings": [learning(2)]})
    refl = await dw.call("dw_record_reflection", {"round": 1, "score": 80, "gaps": [], "should_continue": False})
    assert refl["data"]["todo"] == await dw.read("todo.md")
    assert {"round", "recorded", "next", "workers_still_open", "status"} <= set(refl["data"])


async def test_recitation_follows_the_global_setting_and_tenant_opt_out(dw, monkeypatch):
    monkeypatch.setattr(ff, "get_settings", lambda: _s("offload"))
    await dw.research("w1", save=False)
    on = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1)]})
    assert "todo" in on["data"]
    await _tier_tenant(dw, {"ENABLE_ENGINE_CONTEXT_TIERS": False})
    off = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1)]})
    assert "todo" not in off["data"]


async def test_recitation_lookup_failure_means_off(dw, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("flag lookup failed")

    monkeypatch.setattr(engine_tools, "context_tiers_for", boom)
    await dw.research("w1", save=False)
    r = await dw.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1)]})
    assert not r["isError"] and "todo" not in r["data"]


@pytest.mark.parametrize("tiers", ["offload", "offload+clear", "full"])
@pytest.mark.parametrize("sandbox", [False, True])
def test_orchestrator_tool_results_are_never_offloaded_without_read_file(tiers, sandbox):
    """The orchestrator cannot follow an offload pointer: every tool it can call is excluded or it has read_file."""
    cfg = edw.compose_execute_config(
        _dw_inputs(plan=_plan("pdf_report" if sandbox else None), sandbox_enabled=sandbox, tiers=tiers), NOW)
    excluded = set(cfg["compaction"].get("excludeTools", []))
    assert "read_file" in cfg["tools"] or set(cfg["tools"]) <= excluded
