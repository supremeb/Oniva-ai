"""U22 / R16-R20, R27: Deep Work subagent contract v2 (typed workers, labels, context, steer, verifier).

Flag off: ``compose_execute_config`` equals the pre-U22 golden byte for byte (the same golden as the
context tiers test; it was captured before U16 and v2 must not move it). Flag on: the config names the
new tools, schemas and the verifier; the prompts only gain text.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.tenant import Tenant
from app.services import engine_deep_work_agent as edw
from app.services import feature_flags as ff
from app.services.deep_work.models import DeepWorkPlan, WorkerTask

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
MODELS = edw.DeepWorkModels(high="m/high", mid="m/mid", low="m/low", worker="m/worker")
GOLDEN = Path(__file__).parent / "golden" / "context_tiers_off.json"
ALL = frozenset({"typed_results", "verifier", "steer_worker", "fork"})
ENGINE_SRC = Path(__file__).resolve().parents[3] / "engine" / "src" / "agent"


def _plan(deliverable=None) -> DeepWorkPlan:
    return DeepWorkPlan(title="T", deliverable=deliverable, sub_queries=[
        WorkerTask(worker_id=f"w{i}", sub_query=f"query number {i}", tools=["web_search"]) for i in (1, 2, 3)])


def _inputs(**kw) -> edw.ExecuteInputs:
    return edw.ExecuteInputs(plan=kw.pop("plan", _plan()), models=MODELS, **kw)


def cfg(**kw):
    return edw.compose_execute_config(_inputs(**kw), NOW)


def subs(**kw):
    return cfg(**kw)["metadata"]["subagents"]


# --- flag off ------------------------------------------------------------------------------------


def test_flag_off_equals_the_golden_and_explicit_empty_switches():
    from tests.internal.test_context_tiers_config import dw_cases

    golden = json.loads(GOLDEN.read_text())["deep_work"]
    assert json.loads(json.dumps(dw_cases(), sort_keys=True)) == golden
    assert json.dumps(dw_cases(subagent_v2=frozenset()), sort_keys=True) == json.dumps(dw_cases(), sort_keys=True)


def test_flag_off_has_no_verifier_label_schema_or_new_tools():
    c = cfg(plan=_plan("pdf_report"), sandbox_enabled=True)
    assert "verifier" not in c["tools"] and "steer_worker" not in c["tools"]
    for s in c["metadata"]["subagents"].values():
        assert not ({"outputSchema", "contextPreamble", "mode"} & set(s))
    assert "complete_task" not in json.dumps(c) and "steer_worker" not in json.dumps(c)


def test_subagent_tools_constant_keeps_the_legacy_three_and_adds_the_verifier_name():
    assert edw.SUBAGENT_TOOLS == ("research_worker", "reflector", "output_agent")
    assert edw.VERIFIER == "verifier" and edw.SUBAGENT_TOOLS_V2 == (*edw.SUBAGENT_TOOLS, "verifier")


# --- flag resolution -------------------------------------------------------------------------------


def _tenant(features=None):
    return Tenant(name="t", slug="t", settings={"features": features} if features is not None else {})


def _s(**kw):
    return SimpleNamespace(ENABLE_ENGINE_SUBAGENT_V2=kw.get("on", False),
                           ENGINE_SUBAGENT_V2_SWITCHES=kw.get("switches", "typed_results,verifier,steer_worker,fork"))


def test_flag_is_registered_operator_only_and_defaults_off():
    from app.config import Settings

    assert ff.ENGINE_SUBAGENT_V2_FLAG == "ENABLE_ENGINE_SUBAGENT_V2"
    assert ff.FLAG_METADATA[ff.ENGINE_SUBAGENT_V2_FLAG].operator_only is True
    s = Settings(_env_file=None, SECRET_KEY="x" * 40)
    assert s.ENABLE_ENGINE_SUBAGENT_V2 is False
    assert ff.get_feature(_tenant(), ff.ENGINE_SUBAGENT_V2_FLAG) is False
    assert ff.subagent_v2_switches(None, _s()) == frozenset()
    assert ff.subagent_v2_switches(_tenant(), _s()) == frozenset()


def test_global_on_enables_every_switch_and_the_tenant_value_wins(monkeypatch):
    assert ff.subagent_v2_switches(_tenant(), _s(on=True)) == ALL
    assert ff.subagent_v2_switches(None, _s(on=True)) == ALL
    monkeypatch.setattr(ff, "get_settings", lambda: _s(on=False))
    assert ff.subagent_v2_switches(_tenant({ff.ENGINE_SUBAGENT_V2_FLAG: True})) == ALL
    monkeypatch.setattr(ff, "get_settings", lambda: _s(on=True))
    assert ff.subagent_v2_switches(_tenant({ff.ENGINE_SUBAGENT_V2_FLAG: False})) == frozenset()


def test_switch_list_narrows_the_set_and_ignores_unknown_names():
    assert ff.subagent_v2_switches(None, _s(on=True, switches="verifier, bogus ,fork")) == {"verifier", "fork"}
    assert ff.subagent_v2_switches(None, _s(on=True, switches="")) == frozenset()
    assert ff.subagent_v2_switches(None, _s(on=False, switches="verifier")) == frozenset()


# --- flag on: config -------------------------------------------------------------------------------


def test_on_config_names_the_new_tools_schemas_and_the_verifier():
    c = cfg(subagent_v2=ALL)
    assert {"verifier", "steer_worker"} <= set(c["tools"]) and c["tools"] == sorted(c["tools"])
    s = c["metadata"]["subagents"]
    assert set(s) == {"research_worker", "reflector", "verifier"}
    for name in ("research_worker", "reflector", "verifier"):
        assert s[name]["outputSchema"]["type"] == "object"
    assert "outputSchema" not in json.dumps({k: v for k, v in s.items() if k not in ("research_worker", "reflector", "verifier")})


def test_verifier_has_exactly_the_two_read_tools_and_no_web():
    v = subs(subagent_v2=ALL, has_search=True, has_files=True)["verifier"]
    assert v["tools"] == ["dw_get_learnings", "dw_get_report"]
    assert v["model"] == "m/mid" and v["systemPrompt"] and "{{" not in v["systemPrompt"]
    assert set(v["outputSchema"]["required"]) == {"unsupported_claims", "missing_citations", "broken_citations"}
    assert "mode" not in v  # a clean context: never a fork


def test_each_switch_is_independent():
    only = lambda name: cfg(subagent_v2=frozenset({name}))  # noqa: E731
    t = only("typed_results")
    assert "verifier" not in t["tools"] and "steer_worker" not in t["tools"]
    assert "outputSchema" in t["metadata"]["subagents"]["research_worker"]
    assert "outputSchema" not in only("verifier")["metadata"]["subagents"]["research_worker"]
    v = only("verifier")
    assert "verifier" in v["tools"] and "outputSchema" in v["metadata"]["subagents"]["verifier"]  # a verifier needs a typed result
    st = only("steer_worker")
    assert "steer_worker" in st["tools"] and "verifier" not in st["tools"]
    assert "outputSchema" not in st["metadata"]["subagents"]["research_worker"]
    f = only("fork")
    assert f["metadata"]["subagents"]["reflector"]["mode"] == "fork"
    assert f["tools"] == cfg()["tools"]


def test_verifier_switch_without_typed_results_still_gets_a_schema():
    v = subs(subagent_v2=frozenset({"verifier"}))["verifier"]
    assert v["outputSchema"]["properties"]["broken_citations"]["type"] == "array"


def test_worker_schema_is_exactly_the_current_summary_object():
    schema = subs(subagent_v2=ALL)["research_worker"]["outputSchema"]
    assert set(schema["properties"]) == {"summary", "learning_count", "source_count", "top_findings", "sources"}
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["top_findings"]["maxItems"] == 5 and schema["properties"]["sources"]["maxItems"] == 20
    assert set(schema["properties"]["sources"]["items"]["properties"]) == {"title", "url", "type"}


# A recorded worker answer (the shape worker_engine.md has always asked for; also what the e2e scripted model answers).
RECORDED_WORKER_ANSWER = {
    "summary": "Vendor A is cheaper but slower.", "learning_count": 2, "source_count": 2,
    "top_findings": ["Finding 1 for w1"],
    "sources": [{"title": "Source w1", "url": "https://example.com/w1/1", "type": "web"},
                {"title": "Source w1", "url": "https://example.com/w1/2", "type": "web"}],
}


def _validates(schema, value, path="$"):
    """A tiny reference check of the engine's JSON-schema subset (type, properties, required, items, bounds)."""
    t = schema.get("type")
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if t and (not isinstance(value, kinds[t]) or (t in ("integer", "number") and isinstance(value, bool))):
        return [f"{path}: not {t}"]
    errs = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: not in enum")
    if t == "object":
        errs += [f"{path}.{k}: missing" for k in schema.get("required", []) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += _validates(sub, value[k], f"{path}.{k}")
    if t == "array":
        if len(value) > schema.get("maxItems", 10**9) or len(value) < schema.get("minItems", 0):
            errs.append(f"{path}: item count")
        for i, v in enumerate(value):
            errs += _validates(schema.get("items", {}), v, f"{path}[{i}]")
    if t in ("integer", "number"):
        if value < schema.get("minimum", -10**18) or value > schema.get("maximum", 10**18):
            errs.append(f"{path}: out of range")
    return errs


def test_recorded_worker_answer_validates_against_the_worker_schema():
    schema = subs(subagent_v2=ALL)["research_worker"]["outputSchema"]
    assert _validates(schema, RECORDED_WORKER_ANSWER) == []
    assert _validates(schema, {**RECORDED_WORKER_ANSWER, "top_findings": ["x"] * 6})
    bad = dict(RECORDED_WORKER_ANSWER)
    del bad["sources"]
    assert _validates(schema, bad)


def test_the_frontend_parser_reads_the_same_keys_the_schema_declares():
    ts = (Path(__file__).resolve().parents[3] / "frontend" / "lib" / "engine" / "deepWork.ts").read_text()
    for key in subs(subagent_v2=ALL)["research_worker"]["outputSchema"]["properties"]:
        assert f"answer?.{key}" in ts or f"answer.{key}" in ts, key


def test_reflector_schema_matches_the_prompt_answer():
    schema = subs(subagent_v2=ALL)["reflector"]["outputSchema"]
    assert _validates(schema, {"score": 75, "gaps": ["No data on Y"], "round": 1, "continuing": True}) == []
    assert _validates(schema, {"score": 175, "gaps": [], "round": 1, "continuing": True})


def test_verifier_schema_accepts_an_empty_report_and_flagged_items():
    schema = subs(subagent_v2=ALL)["verifier"]["outputSchema"]
    assert _validates(schema, {"unsupported_claims": [], "missing_citations": [], "broken_citations": []}) == []
    assert _validates(schema, {
        "unsupported_claims": [{"section": "Market", "claim": "X grew 40%", "reason": "no learning says so"}],
        "missing_citations": [{"section": "Intro", "claim": "Y"}],
        "broken_citations": [{"section": "Pricing", "citation": "[9]"}]}) == []
    assert _validates(schema, {"unsupported_claims": []})


def test_every_schema_stays_inside_the_engine_subset():
    """No keyword the engine's validator would reject at parse time (it rejects, never ignores)."""
    allowed = {"type", "properties", "required", "items", "enum", "minItems", "maxItems", "minLength", "maxLength",
               "minimum", "maximum", "title", "description"}

    def walk(schema):
        assert set(schema) <= allowed, set(schema) - allowed
        for sub in schema.get("properties", {}).values():
            walk(sub)
        if "items" in schema:
            walk(schema["items"])

    for s in subs(subagent_v2=ALL).values():
        if "outputSchema" in s:
            walk(s["outputSchema"])
            assert len(json.dumps(s["outputSchema"])) < 16 * 1024


def test_engine_subset_source_lists_the_keywords_used():
    src = (ENGINE_SRC / "json-schema-subset.ts").read_text()
    for kw in ("minItems", "maxItems", "minimum", "maximum", "required", "properties", "items"):
        assert kw in src


def test_complete_task_and_steer_are_listed_where_the_engine_expects_them():
    c = cfg(subagent_v2=ALL)
    # The engine adds complete_task to a typed child itself; the config must not also hand it to the parent.
    assert "complete_task" not in c["tools"]
    for s in c["metadata"]["subagents"].values():
        assert "complete_task" not in s["tools"]
    assert {"steer_worker", "verifier"} <= set(c["tools"])


def test_steer_worker_is_absent_without_the_switch_even_with_the_flag():
    c = cfg(subagent_v2=frozenset({"typed_results", "verifier", "fork"}))
    assert "steer_worker" not in c["tools"]


def test_output_agent_gets_no_schema_and_stays_isolated():
    s = subs(plan=_plan("pdf_report"), sandbox_enabled=True, subagent_v2=ALL)
    assert "outputSchema" not in s["output_agent"] and "mode" not in s["output_agent"]


# --- context preamble --------------------------------------------------------------------------------


def test_worker_preamble_lists_the_sibling_sub_queries_without_the_workers_own():
    s = subs(subagent_v2=ALL)["research_worker"]
    pre = s["contextPreamble"]
    assert len(pre) <= 4000
    for i in (1, 2, 3):
        assert f"query number {i}" in pre and f"w{i}" in pre
    assert "Other workers cover" in pre and "avoid" in pre.lower()


def test_preamble_is_capped_for_a_large_plan():
    big = DeepWorkPlan(title="T", sub_queries=[
        WorkerTask(worker_id=f"w{i}", sub_query="very long sub query " * 40, tools=["web_search"]) for i in range(1, 9)])
    pre = subs(plan=big, subagent_v2=ALL)["research_worker"]["contextPreamble"]
    assert len(pre) <= 4000 and "w8" in pre


def test_preamble_only_with_typed_results_switch():
    assert "contextPreamble" not in subs(subagent_v2=frozenset({"verifier", "fork", "steer_worker"}))["research_worker"]


# --- prompts --------------------------------------------------------------------------------------


def test_on_worker_prompt_calls_complete_task_instead_of_replying_with_json():
    off = subs()["research_worker"]["systemPrompt"]
    on = subs(subagent_v2=ALL)["research_worker"]["systemPrompt"]
    assert "Reply with ONLY this JSON object" in off and "complete_task" not in off
    assert "complete_task" in on and "Reply with ONLY this JSON object" not in on
    assert on.startswith(off.split("## Final answer")[0])  # everything before the final answer is unchanged


def test_worker_prompt_is_unchanged_when_only_other_switches_are_on():
    off = subs()["research_worker"]["systemPrompt"]
    assert subs(subagent_v2=frozenset({"verifier", "steer_worker", "fork"}))["research_worker"]["systemPrompt"] == off


def test_reflector_prompt_calls_complete_task_when_typed():
    off = subs()["reflector"]["systemPrompt"]
    on = subs(subagent_v2=ALL)["reflector"]["systemPrompt"]
    assert "complete_task" not in off and "complete_task" in on


def test_orchestrator_prompt_gains_label_brief_verifier_and_steer_rules():
    off = cfg()["systemPrompt"]
    on = cfg(subagent_v2=ALL)["systemPrompt"]
    assert on.startswith(off.split("## Approved plan")[0].rstrip())
    assert on.endswith(off.split("## Approved plan")[1]) and on.count("## Approved plan") == 1
    for needle in ("label", "worker_id", "context", "brief", "verifier", "steer_worker", "dw_write_section"):
        assert needle in on, needle
    assert on.index("verifier") < on.index("## Approved plan")  # the new rules come before the plan
    assert "verifier" not in off and "steer_worker" not in off


def test_orchestrator_prompt_sections_follow_the_switches():
    assert "verifier" not in cfg(subagent_v2=frozenset({"typed_results"}))["systemPrompt"]
    assert "steer_worker" not in cfg(subagent_v2=frozenset({"typed_results", "verifier"}))["systemPrompt"]
    only_steer = cfg(subagent_v2=frozenset({"steer_worker"}))["systemPrompt"]
    assert "steer_worker" in only_steer and "label" in only_steer


def test_verifier_prompt_reads_only_and_explains_the_result():
    p = edw.load_prompt("verifier_engine.md")
    for needle in ("dw_get_report", "dw_get_learnings", "complete_task", "unsupported_claims", "missing_citations",
                   "broken_citations"):
        assert needle in p
    assert "web_search" not in p and "dw_write_section" not in p


def test_no_search_note_still_present_with_v2():
    on = cfg(subagent_v2=ALL, has_search=False)["systemPrompt"]
    assert "Web search is NOT available" in on


# --- tiers interaction ----------------------------------------------------------------------------------


@pytest.mark.parametrize("tiers", ["offload", "offload+clear", "full"])
def test_v2_with_tiers_keeps_every_orchestrator_tool_result_inline(tiers):
    c = cfg(subagent_v2=ALL, tiers=tiers)
    assert set(c["tools"]) <= set(c["compaction"]["excludeTools"])  # includes verifier and steer_worker
    assert "compaction" in c["metadata"]["subagents"]["verifier"]


# --- tiers: reflector and output_agent have no read_file (review gap) ---------------------------------------


@pytest.mark.parametrize("tiers", ["offload", "offload+clear", "full"])
@pytest.mark.parametrize("v2", [frozenset(), ALL])
@pytest.mark.parametrize("sandbox", [False, True])
def test_no_subagent_tool_result_can_be_offloaded_to_a_pointer_it_cannot_follow(tiers, v2, sandbox):
    c = cfg(plan=_plan("pdf_report" if sandbox else None), sandbox_enabled=sandbox, tiers=tiers, subagent_v2=v2)
    for name, sub in c["metadata"]["subagents"].items():
        excluded = set(sub["compaction"].get("excludeTools", []))
        assert "read_file" in sub["tools"] or set(sub["tools"]) <= excluded, name


def test_tiers_off_subagents_have_no_compaction_block_and_no_read_file_added():
    s = subs(plan=_plan("pdf_report"), sandbox_enabled=True)
    assert all("compaction" not in v for v in s.values())
    assert "read_file" not in s["reflector"]["tools"] and "read_file" not in s["output_agent"]["tools"]


def test_reflector_and_output_agent_exclude_their_own_tools_with_tiers_on():
    s = subs(plan=_plan("pdf_report"), sandbox_enabled=True, tiers="full")
    for name in ("reflector", "output_agent"):
        assert set(s[name]["tools"]) <= set(s[name]["compaction"]["excludeTools"])
    # the research worker has read_file and keeps the shared list
    assert "read_file" in s["research_worker"]["tools"]


# --- resolution at config build time ----------------------------------------------------------------------


async def test_resolve_subagent_v2_reads_the_tenant_flag_and_survives_failures():
    import uuid

    class DB:
        def __init__(self, tenant=None, boom=False):
            self.tenant, self.boom = tenant, boom

        async def get(self, model, key):
            if self.boom:
                raise RuntimeError("db down")
            return self.tenant

    tid = str(uuid.uuid4())
    assert await edw.resolve_subagent_v2(None, tid) == frozenset()
    assert await edw.resolve_subagent_v2(DB(_tenant({ff.ENGINE_SUBAGENT_V2_FLAG: True})), tid) == ALL
    assert await edw.resolve_subagent_v2(DB(_tenant({ff.ENGINE_SUBAGENT_V2_FLAG: False})), tid) == frozenset()
    assert await edw.resolve_subagent_v2(DB(boom=True), tid) == frozenset()
