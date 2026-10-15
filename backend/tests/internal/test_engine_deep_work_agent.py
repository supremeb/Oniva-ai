"""U6: Deep Work engine agent configs, prompts and plan repair (no database, no engine)."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import engine_deep_work_agent as edw
from app.services.deep_work.models import DeepWorkPlan, WorkerTask
from app.services.engine_deep_work_agent import (
    DeepWorkModels,
    ExecuteInputs,
    PlanInvalid,
    compose_execute_config,
    dominant_task,
    intake_agent_config,
    parse_plan,
    plan_agent_config,
    plan_with_repair,
)

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
MODELS = DeepWorkModels(high="m/high", mid="m/mid", low="m/low", worker="m/worker")


def plan(deliverable=None, n=2, **kw):
    return DeepWorkPlan(
        title="T", deliverable=deliverable,
        sub_queries=[WorkerTask(worker_id=f"w{i}", sub_query=f"q{i}", tools=["web_search"], **kw)
                     for i in range(1, n + 1)])


def inputs(**kw):
    base = dict(plan=plan(), models=MODELS)
    base.update(kw)
    return ExecuteInputs(**base)


def test_intake_and_plan_have_no_tools_and_legacy_prompts():
    intake = intake_agent_config(MODELS, NOW)
    pl = plan_agent_config(MODELS, NOW)
    assert intake["tools"] == [] and pl["tools"] == []
    assert "research intake assistant" in intake["systemPrompt"]
    assert "research orchestrator" in pl["systemPrompt"]
    assert intake["model"] == "m/mid" and pl["model"] == "m/high"
    assert "api_key" not in json.dumps(intake).lower()


def test_execute_config_shape_full_tenant():
    cfg = compose_execute_config(inputs(
        plan=plan(deliverable="pdf_report"), has_search=True, knowledge_base_ids=["kb1"],
        connector_ids=["c1"], sandbox_enabled=True, run_id="r1", max_rounds=60, deadline_ms=123), NOW)
    assert cfg["tools"] == sorted([
        "dw_start_round", "dw_get_learnings", "dw_write_section", "dw_finalize", "dw_get_report",
        "research_worker", "reflector", "output_agent"])
    assert cfg["maxRounds"] == 60 and cfg["deadlineMs"] == 123
    subs = cfg["metadata"]["subagents"]
    assert set(subs) == {"research_worker", "reflector", "output_agent"}
    assert subs["research_worker"]["model"] == "m/worker"
    assert "dw_save_learnings" in subs["research_worker"]["tools"]
    assert {"web_search", "browse_url", "read_file", "search_knowledge", "shell_exec"} <= set(subs["research_worker"]["tools"])
    assert set(subs["reflector"]["tools"]) == {"dw_get_learnings", "dw_record_reflection"}
    assert "inspect_artifact" in subs["output_agent"]["tools"]
    for s in subs.values():
        assert set(s) == {"model", "tools", "systemPrompt"} and s["systemPrompt"]
    assert cfg["metadata"]["knowledgeBaseIds"] == ["kb1"]
    assert "Approved plan" in cfg["systemPrompt"] and '"w1"' in cfg["systemPrompt"]
    assert "{{CURRENT_DATE}}" not in json.dumps(cfg)
    assert "October 15, 2026" in subs["research_worker"]["systemPrompt"]


def test_orchestrator_has_only_dw_and_subagent_tools():
    cfg = compose_execute_config(inputs(has_search=True, sandbox_enabled=True), NOW)
    assert all(t.startswith("dw_") or t in edw.SUBAGENT_TOOLS for t in cfg["tools"])


def test_no_search_no_kb_no_sandbox():
    cfg = compose_execute_config(inputs(plan=plan(deliverable="pdf_report")), NOW)
    w = cfg["metadata"]["subagents"]["research_worker"]["tools"]
    assert "web_search" not in w and "browse_url" not in w
    assert "search_knowledge" not in w and "shell_exec" not in w
    assert "dw_save_learnings" in w
    # sandbox off: no output_agent even though the plan has a deliverable
    assert "output_agent" not in cfg["tools"] and "output_agent" not in cfg["metadata"]["subagents"]
    assert "skip step 7" in cfg["systemPrompt"]


def test_kb_and_connectors_add_tools_and_sandbox_without_deliverable_has_no_output_agent():
    cfg = compose_execute_config(inputs(
        has_search=True, knowledge_base_ids=["k"], connector_ids=["c"], sandbox_enabled=True), NOW)
    w = cfg["metadata"]["subagents"]["research_worker"]["tools"]
    assert "search_knowledge" in w and "invoke_connector_tool" in w and "dw_browser_navigate" in w
    assert "output_agent" not in cfg["tools"]


def test_config_ignores_client_supplied_prompt_and_tools():
    # The builders accept no client prompt/tool arguments at all.
    import inspect
    for fn in (compose_execute_config, intake_agent_config, plan_agent_config):
        params = inspect.signature(fn).parameters
        assert not {"system_prompt", "systemPrompt", "tools", "prompt"} & set(params)
    assert not {"system_prompt", "tools"} & set(ExecuteInputs.__dataclass_fields__)


def test_prompts_contract():
    loop = edw.load_prompt("orchestrator_loop.md")
    for name in ("dw_start_round", "research_worker", "reflector", "dw_get_learnings",
                 "dw_write_section", "output_agent", "dw_finalize"):
        assert name in loop
    assert loop.index("dw_start_round") < loop.index("reflector") < loop.index("dw_finalize")
    worker = edw.load_prompt("worker_engine.md")
    assert "dw_save_learnings" in worker and "at most 20" in worker and "learning_count" in worker
    refl = edw.load_prompt("reflection_engine.md")
    assert "dw_record_reflection" in refl and '"continuing"' in refl


def test_legacy_prompts_unchanged():
    assert "Return ONLY a JSON object" in edw.load_prompt("reflection.md")
    assert "shell_exec" in edw.load_prompt("worker.md")


@pytest.mark.parametrize("types,expected", [
    ([("research", None)] * 2, ("research", None)),
    ([("analysis", "chart"), ("research", None), ("analysis", "chart")], ("analysis", "chart")),
    ([("code_gen", None), ("extraction", None)], ("code_gen", None)),  # tie: first seen
])
def test_dominant_task_table(types, expected):
    p = DeepWorkPlan(sub_queries=[
        WorkerTask(worker_id=f"w{i}", sub_query="q", tools=[], task_type=t, output_type=o)
        for i, (t, o) in enumerate(types)])
    assert dominant_task(p) == expected


@pytest.mark.asyncio
async def test_resolve_models_uses_tier_and_policy(monkeypatch):
    from app.services import llm_gateway as gw
    calls = []

    async def tier(db, tid, t, cfg=None):
        calls.append(("tier", t, cfg))
        return f"tier/{t}"

    async def policy(db, tid, task_type, output_type=None, agent_config=None):
        calls.append(("policy", task_type, output_type))
        return f"policy/{task_type}/{output_type}"

    monkeypatch.setattr(gw, "resolve_model_for_tier", tier)
    monkeypatch.setattr(gw, "resolve_model_for_policy", policy)
    p = DeepWorkPlan(sub_queries=[WorkerTask(worker_id="w1", sub_query="q", tools=[],
                                             task_type="analysis", output_type="chart")])
    cfg = {"deep_work_models": {"high": "x"}}
    m = await edw.resolve_deep_work_models(None, "t", p, cfg)
    assert (m.high, m.mid, m.low) == ("tier/high", "tier/mid", "tier/low")
    assert m.worker == "policy/analysis/chart"
    assert ("tier", "high", cfg) in calls
    m2 = await edw.resolve_deep_work_models(None, "t", None, None)
    assert m2.worker == "tier/mid"


VALID = json.dumps({"title": "T", "sub_queries": [{"worker_id": "w1", "sub_query": "q", "tools": []}]})


def test_parse_plan_variants():
    assert parse_plan(VALID).title == "T"
    assert parse_plan("Here:\n```json\n" + VALID + "\n```").sub_queries[0].worker_id == "w1"
    with pytest.raises(Exception):
        parse_plan("nope")


@pytest.mark.asyncio
async def test_valid_plan_needs_no_repair():
    async def repair(msg):
        raise AssertionError("no repair expected")
    assert (await plan_with_repair(VALID, repair)).title == "T"


@pytest.mark.asyncio
async def test_invalid_then_repaired_submits_exactly_once():
    sent = []

    async def repair(msg):
        sent.append(msg)
        return VALID
    p = await plan_with_repair("not json", repair)
    assert p.title == "T" and len(sent) == 1


@pytest.mark.asyncio
async def test_invalid_after_repair_raises_with_text_and_one_submission():
    sent = []

    async def repair(msg):
        sent.append(msg)
        return "still broken"
    with pytest.raises(PlanInvalid) as ei:
        await plan_with_repair("{bad", repair)
    assert ei.value.text == "still broken" and len(sent) == 1


def test_round_and_deadline_helpers():
    assert edw.default_max_rounds(2) > edw.default_max_rounds(0)
    assert edw.default_deadline_ms(10, now_ms=1000) == 11000


def test_execute_content_is_the_plan():
    assert json.loads(edw.execute_content(plan()).split("\n\n", 1)[1])["title"] == "T"
