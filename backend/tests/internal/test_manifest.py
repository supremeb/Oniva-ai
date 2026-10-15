"""U3 / R4 / AE9: the tool manifest carries real JSON Schema and complete metadata."""
import asyncio

import jsonschema
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.agents.skills import registry as skills_registry
from app.agents.skills.registry import Skill
from app.config import Settings
from app.internal_api import tool_specs
from app.internal_api.router import mount_internal_router
from app.internal_api.schemas import build_parameters_schema, parse_arg_descriptions
from app.internal_api.tool_specs import DEFAULT_SPEC, TOOL_SPECS
from app.internal_api.tools import (
    SIMULATED_PREFIX,
    build_manifest,
    ensure_skills_loaded,
    undeclared_skills,
)

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
AUTH = {"Authorization": f"Bearer {GATEWAY}"}
INTERACTIVE = {"ask_user", "present_plan", "complete_task"}


@pytest.fixture(scope="module", autouse=True)
def loaded():
    # A private loop: asyncio.run() would reset the global loop policy state other async tests rely on.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(ensure_skills_loaded())
    finally:
        loop.close()


@pytest.fixture(scope="module")
def manifest_json():
    app = FastAPI()
    mount_internal_router(
        app, Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC)
    )
    r = TestClient(app).get("/internal/tools/manifest", headers=AUTH)
    assert r.status_code == 200
    return r.json()["tools"]


def by_name(tools):
    return {t["name"]: t for t in tools}


# ---- AE9 ---------------------------------------------------------------

def test_every_skill_has_a_tool_spec():
    assert undeclared_skills() == []


def test_every_spec_has_a_skill():
    assert sorted(set(TOOL_SPECS) - set(skills_registry._registry)) == []


def test_undeclared_skill_is_detected_and_defaults_to_cautious_write(monkeypatch):
    async def noop(ctx, x: str): ...

    monkeypatch.setitem(
        skills_registry._registry, "brand_new_skill",
        Skill(name="brand_new_skill", description="d", category="x", handler=noop),
    )
    assert undeclared_skills() == ["brand_new_skill"]
    spec = tool_specs.spec_for("brand_new_skill")
    assert spec == DEFAULT_SPEC
    assert (spec.side_effect, spec.idempotent, spec.requires_approval, spec.timeout_ms) == ("write", False, False, 30_000)
    assert not spec.needs_db and not spec.needs_keys and not spec.needs_connectors
    entry = by_name([t.model_dump() for t in build_manifest()])["brand_new_skill"]
    assert entry["sideEffect"] == "write" and entry["idempotent"] is False


# ---- schema generation -------------------------------------------------

def test_parameters_are_valid_draft_2020_12_objects(manifest_json):
    for t in manifest_json:
        p = t["parameters"]
        jsonschema.Draft202012Validator.check_schema(p)
        assert p["type"] == "object", t["name"]
        assert p["additionalProperties"] is False
        assert "$ref" not in str(p) and "$defs" not in p, t["name"]
        assert "ctx" not in p["properties"], t["name"]


def test_parameters_round_trip_a_sample_argument_set(manifest_json):
    tools = by_name(manifest_json)
    samples = {
        "web_search": {"query": "oniva"},
        "write_file": {"file_path": "a.txt", "content": "hi"},
        "store_decision": {"agent_name": "a", "action_type": "research", "context": "c", "decision": "d"},
        "update_task": {"task_id": "1", "status": "completed"},
        "invoke_connector_tool": {"connector_id": "c", "tool_name": "t", "arguments": {"k": 1}},
    }
    for name, args in samples.items():
        jsonschema.validate(args, tools[name]["parameters"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({}, tools["write_file"]["parameters"])  # required missing
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"query": "x", "bogus": 1}, tools["web_search"]["parameters"])  # extra forbidden
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"agent_name": "a", "action_type": "nope", "context": "c", "decision": "d"},
                            tools["store_decision"]["parameters"])  # Literal enforced


def test_required_and_defaults_follow_the_signature(manifest_json):
    p = by_name(manifest_json)["web_search"]["parameters"]
    assert p["required"] == ["query"]
    assert p["properties"]["num_results"]["default"] == 5


def test_descriptions_come_from_the_docstring_args_block():
    def fn(ctx, a: str, b: int = 1):
        """Do it.

        Args:
            a: The first thing,
                continued here.
            b (int): The second.

        Returns:
            Nothing.
        """

    assert parse_arg_descriptions(fn.__doc__) == {"a": "The first thing, continued here.", "b": "The second."}
    props = build_parameters_schema(fn)["properties"]
    assert props["a"]["description"].startswith("The first thing")
    assert props["b"]["description"] == "The second."


def test_nested_models_are_inlined():
    from pydantic import BaseModel

    class Inner(BaseModel):
        n: int

    def fn(ctx, item: Inner, items: list[Inner]): ...

    schema = build_parameters_schema(fn)
    assert "$defs" not in schema and "$ref" not in str(schema)
    assert schema["properties"]["item"]["properties"]["n"]["type"] == "integer"


# ---- classification ----------------------------------------------------

def test_known_tools_are_classified(manifest_json):
    t = by_name(manifest_json)
    assert (t["web_search"]["sideEffect"], t["web_search"]["idempotent"]) == ("none", True)
    assert t["web_search"]["timeoutMs"] == 30_000
    assert (t["store_decision"]["sideEffect"], t["store_decision"]["idempotent"]) == ("write", False)
    assert (t["create_task"]["sideEffect"], t["create_task"]["idempotent"]) == ("write", False)
    assert (t["write_file"]["sideEffect"], t["write_file"]["idempotent"]) == ("write", True)
    assert t["execute_python"]["timeoutMs"] == 90_000
    assert t["browser_navigate"]["timeoutMs"] == 120_000


def test_approval_is_required_for_code_delete_and_connector_writes(manifest_json):
    t = by_name(manifest_json)
    for name in ("execute_python", "delete_file", "invoke_connector_tool", "linear_create_issue",
                 "servicenow_create_incident"):
        assert t[name]["requiresApproval"] is True, name
    for name in ("read_file", "web_search", "write_file"):
        assert t[name]["requiresApproval"] is False, name


def test_reads_never_need_approval_and_uncertain_writes_are_not_idempotent():
    for name, spec in TOOL_SPECS.items():
        if spec.side_effect == "none":
            assert not spec.requires_approval, name
    # Only these writes may be replayed under the same key.
    assert {n for n, s in TOOL_SPECS.items() if s.side_effect == "write" and s.idempotent} == {
        "write_file", "update_todo", "update_task", "browser_screenshot",
    }


# ---- manifest shape ----------------------------------------------------

def test_interactive_skills_are_absent_and_routing_skill_too(manifest_json):
    names = {t["name"] for t in manifest_json}
    assert not names & (INTERACTIVE | {"assess_query_complexity"})


def test_mock_skills_carry_the_simulated_prefix(manifest_json):
    t = by_name(manifest_json)
    for name in ("kyc_verification", "fetch_market_data", "batch_sanctions_check", "batch_kyc_verification"):
        assert t[name]["description"].startswith(SIMULATED_PREFIX), name
    assert not t["web_search"]["description"].startswith(SIMULATED_PREFIX)


def test_response_satisfies_the_engines_parse_constraints(manifest_json):
    names = [t["name"] for t in manifest_json]
    assert names == sorted(names) and len(set(names)) == len(names) and names
    for t in manifest_json:
        assert set(t) == {"name", "description", "parameters", "sideEffect", "idempotent", "timeoutMs",
                          "requiresApproval"}
        assert isinstance(t["name"], str) and t["name"]
        assert isinstance(t["description"], str) and t["description"]
        assert t["sideEffect"] in ("none", "write")
        assert isinstance(t["idempotent"], bool) and isinstance(t["requiresApproval"], bool)
        assert type(t["timeoutMs"]) is int and t["timeoutMs"] > 0
        assert isinstance(t["parameters"], dict)


def test_manifest_requires_the_service_token():
    app = FastAPI()
    mount_internal_router(
        app, Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC)
    )
    c = TestClient(app)
    assert c.get("/internal/tools/manifest").status_code == 401
    assert c.get("/internal/tools/manifest", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_previously_unregistered_batch_skills_are_served(manifest_json):
    names = {t["name"] for t in manifest_json}
    assert {"batch_sanctions_check", "batch_kyc_verification"} <= names
