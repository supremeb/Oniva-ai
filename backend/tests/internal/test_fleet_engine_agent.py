"""Fleet agent config builder (phase 6 U6)."""
import re
from types import SimpleNamespace

from app.services import background_agent_executor as legacy
from app.services import fleet_engine_agent as fea

NOW = 1_800_000_000_000
MANIFEST = ["web_search", "create_task", "sanctions_check", "batch_sanctions_check", "list_available_connectors"]


def agent(**kw):
    base = dict(system_prompt="Be useful.", skills=["web_search"], resource_constraints={}, model="gpt-x", config={})
    base.update(kw)
    return SimpleNamespace(**base)


def build(a, **kw):
    return fea.build_fleet_agent_config(a, "job-1", NOW, manifest_names=kw.pop("manifest", MANIFEST), **kw)


def test_session_dependent_dropped_and_batch_suppresses_individual():
    out = build(agent(skills=["web_search", "create_task", "sanctions_check", "batch_sanctions_check"]))
    assert out.config["tools"] == ["web_search", "batch_sanctions_check"]
    assert out.warnings == []


def test_budgets_map_to_rounds_and_absolute_deadline():
    out = build(agent(resource_constraints={"max_tool_calls": 7, "timeout_seconds": 60}))
    assert out.config["maxRounds"] == 7
    assert out.config["deadlineMs"] == NOW + 60_000


def test_defaults_when_constraints_absent():
    out = build(agent(resource_constraints=None))
    assert out.config["maxRounds"] == fea.DEFAULT_MAX_TOOL_CALLS == 50
    assert out.config["deadlineMs"] == NOW + fea.DEFAULT_MAX_DURATION_SEC * 1000
    assert out.config["metadata"] == {"notify": "backend", "runKind": "fleet", "jobId": "job-1"}
    assert out.config["lane"] == "background"  # automation never queues ahead of a chat turn


def test_skill_missing_from_manifest_warns_not_fails():
    out = build(agent(skills=["web_search", "ghost_skill"]))
    assert out.config["tools"] == ["web_search"]
    assert len(out.warnings) == 1 and "ghost_skill" in out.warnings[0]


def test_config_has_no_secrets():
    out = build(agent(), connector_summary='- connector_id="c1" name="x" tools: [a]')
    text = str(out.config).lower()
    assert "api_key" not in text and "secret" not in text
    assert not re.search(r"sk-[a-z0-9]{8,}", text)
    assert set(out.config) <= {"systemPrompt", "tools", "model", "tier", "maxRounds", "deadlineMs", "metadata", "lane"}


def test_prompt_includes_connector_summary_and_tool_note():
    out = build(agent(skills=["list_available_connectors"]), connector_summary="- c1")
    assert "Available connectors" in out.config["systemPrompt"] and "- c1" in out.config["systemPrompt"]


def test_legacy_executor_reexports_same_objects():
    assert legacy._build_system_prompt is fea._build_system_prompt
    assert legacy._load_tools is fea._load_tools
    assert legacy._preload_connector_summary is fea._preload_connector_summary
    assert legacy._SESSION_DEPENDENT_SKILLS is fea._SESSION_DEPENDENT_SKILLS
    assert legacy._BATCH_SUPERSEDES is fea._BATCH_SUPERSEDES
